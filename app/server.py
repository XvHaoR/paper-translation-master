"""本地 FastAPI 服务：任务上传/进度、文档列表、阅读页、浏览器扩展接口。

对应方案 §10.3 本地 API 草案与 §17.1 本地安全要求：
- 只监听 127.0.0.1
- 上传限制扩展名、MIME、大小和最大页数
- 路径解析后校验，阻止目录穿越
- 浏览器接口验证配对令牌与扩展来源
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import shutil
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.parsing.native_pdf import NativePdfExtractor
from app.pipeline.orchestrator import ConvertOptions, Orchestrator

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200MB
MAX_IMPORT_URL_BYTES = 200 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf"}
ALLOWED_MIME_PREFIXES = ("application/pdf", "application/octet-stream")

JOB_STAGES = [
    "queued",
    "queued_gpu",
    "preflight",
    "rendering_pages",
    "layout_analysis",
    "recognizing",
    "normalizing",
    "rendering_html",
    "ready",
    "needs_review",
    "failed",
]

JOB_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def new_job_id() -> str:
    return "".join(secrets.choice(JOB_ID_CHARS) for _ in range(12))


# ---------------------------------------------------------------------------
# 文档存储（JSON 持久化）
# ---------------------------------------------------------------------------


class DocumentStore:
    """扫描 workspace/documents 下的生成结果。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _scan(self) -> list[dict]:
        docs = []
        for d in sorted(self.settings.documents_dir.iterdir()):
            if not d.is_dir():
                continue
            ir_path = d / "document.json"
            if not ir_path.is_file():
                continue
            try:
                data = json.loads(ir_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            meta = data.get("document", {})
            blocks = data.get("blocks", [])
            needs_review = any(
                b.get("quality", {}).get("needs_review") for b in blocks
            )
            docs.append(
                {
                    "id": meta.get("id", d.name),
                    "dir": str(d),
                    "title": meta.get("title", ""),
                    "source_pdf": meta.get("source_pdf", ""),
                    "page_count": meta.get("page_count", 0),
                    "blocks": len(blocks),
                    "created_at": meta.get("created_at", ""),
                    "status": "needs_review" if needs_review else "ready",
                    "has_html": (d / "index.html").is_file(),
                }
            )
        return sorted(docs, key=lambda x: x["created_at"], reverse=True)

    def list(self) -> list[dict]:
        return self._scan()

    def get(self, doc_id: str) -> Optional[dict]:
        for d in self._scan():
            if d["id"] == doc_id or d["id"].startswith(doc_id):
                return d
        return None

    def dir_for(self, doc_id: str) -> Optional[Path]:
        for d in self._scan():
            if d["id"] == doc_id or d["id"].startswith(doc_id):
                return Path(d["dir"])
        return None


def document_store(settings: Settings) -> DocumentStore:
    return DocumentStore(settings)


# ---------------------------------------------------------------------------
# 任务管理
# ---------------------------------------------------------------------------


class JobStore:
    """任务去重 + JSON 持久化 + 单消费者 GPU 队列。"""

    def __init__(self, settings: Settings, orchestrator: Orchestrator) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self.jobs: dict[str, dict] = {}
        self.jobs_file = settings.workspace / "jobs.json"
        self._lock = threading.Lock()
        self._load()
        self._queue: queue.Queue[tuple[str, Path, ConvertOptions] | None] = queue.Queue()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="paperloom-gpu-worker",
            daemon=True,
        )
        self._worker.start()

    def _load(self) -> None:
        if self.jobs_file.is_file():
            try:
                data = json.loads(self.jobs_file.read_text(encoding="utf-8"))
                # 进程退出后没有可安全接管的模型调用；旧活动任务标记失败，
                # 已完成文档由 DocumentStore 和 Orchestrator 缓存继续提供。
                self.jobs = {}
                for key, job in data.items():
                    if job.get("state") in ("ready", "needs_review", "failed"):
                        continue
                    job["state"] = "failed"
                    job["errors"] = job.get("errors", []) + ["服务重启，任务已中止，请重新提交"]
                    self.jobs[key] = job
            except Exception:  # noqa: BLE001
                self.jobs = {}

    def _persist(self) -> None:
        try:
            text = json.dumps(self.jobs, ensure_ascii=False, indent=1)
            tmp = self.jobs_file.with_name(
                f".{self.jobs_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.jobs_file)
        except Exception:  # noqa: BLE001
            logger.warning("任务状态持久化失败")

    @staticmethod
    def _job_key(document_id: str, options: ConvertOptions) -> str:
        return "|".join(
            [
                document_id,
                options.ocr_mode,
                options.ocr_device,
                str(options.render_dpi),
                options.pages or "all",
                "force" if options.force else "cached",
            ]
        )

    def _cleanup_temp_upload(self, pdf_path: Path) -> None:
        """只删除 workspace/tmp 内由上传接口创建的临时副本。"""
        try:
            resolved = pdf_path.resolve()
            if resolved.parent == self.settings.tmp_dir.resolve():
                resolved.unlink(missing_ok=True)
        except OSError:
            pass

    def create(self, pdf_path: Path, options: ConvertOptions | None = None) -> str:
        options = options or ConvertOptions()
        document_id = NativePdfExtractor.content_hash(pdf_path)
        job_key = self._job_key(document_id, options)
        job_id = new_job_id()
        with self._lock:
            for existing in self.jobs.values():
                if existing.get("job_key") != job_key:
                    continue
                if existing.get("state") not in ("failed", "cancelled") and not (
                    options.force and existing.get("state") in ("ready", "needs_review")
                ):
                    self._cleanup_temp_upload(pdf_path)
                    return str(existing["id"])
            self.jobs[job_id] = {
                "id": job_id,
                "job_key": job_key,
                "state": "queued_gpu",
                "progress": {"stage": "queued_gpu", "page": 0, "total": 0, "message": "等待 GPU OCR"},
                "document_id": document_id,
                "errors": [],
                "warnings": [],
                "source_pdf": pdf_path.name,
                "options": {
                    "ocr_mode": options.ocr_mode,
                    "ocr_device": options.ocr_device,
                    "render_dpi": options.render_dpi,
                    "pages": options.pages,
                    "force": options.force,
                },
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            self._persist()
        self._queue.put((job_id, pdf_path, options))
        return job_id

    def _worker_loop(self) -> None:
        """唯一 GPU 消费者；禁止同一进程同时运行多个完整模型任务。"""
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            job_id, pdf_path, options = item
            try:
                self._run(job_id, pdf_path, options)
            finally:
                self._queue.task_done()

    def _run(self, job_id: str, pdf_path: Path, options: ConvertOptions | None) -> None:
        def progress(stage: str, page: int, total: int, message: str) -> None:
            with self._lock:
                job = self.jobs.get(job_id)
                if job is None:
                    return
                if job.get("state") == "cancelled":
                    raise RuntimeError("OCR 已取消")
                job["state"] = stage if stage not in JOB_STAGES else stage
                job["progress"] = {"stage": stage, "page": page, "total": total, "message": message}
                job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            if self.get(job_id).get("state") == "cancelled":
                return
            result = self.orchestrator.convert(pdf_path, options, progress=progress)
            with self._lock:
                job = self.jobs[job_id]
                if job.get("state") == "cancelled":
                    return
                job["state"] = result.status if result.ok else "failed"
                job["document_id"] = result.document_id
                job["errors"] = result.errors
                job["warnings"] = result.warnings
                job["duration_ms"] = result.duration_ms
                job["progress"] = {"stage": job["state"], "page": 0, "total": 0, "message": f"完成：{job['state']}"}
                job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception as e:  # noqa: BLE001
            logger.exception("任务 %s 异常", job_id)
            with self._lock:
                job = self.jobs.get(job_id)
                if job and job.get("state") != "cancelled":
                    job["state"] = "failed"
                    job["errors"] = job.get("errors", []) + [f"{type(e).__name__}: {e}"]
                    job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        finally:
            self._cleanup_temp_upload(pdf_path)
        with self._lock:
            self._persist()

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            job["state"] = "cancelled"
            job["errors"] = ["OCR 已取消，可以重新识别"]
            self._persist()
            return True

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)
            return items[:limit]


def _save_upload(upload: UploadFile, settings: Settings) -> Path:
    """校验并保存上传文件，返回保存路径。"""
    name = upload.filename or "upload.pdf"
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"不支持的扩展名：{suffix}（仅支持 .pdf）")
    dest = settings.tmp_dir / f"upload-{new_job_id()}.pdf"
    size = 0
    try:
        with dest.open("wb") as f:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "文件超过 200MB 限制")
                f.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    # 校验 PDF 文件头
    head = dest.read_bytes()[:5]
    if head != b"%PDF-":
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "文件不是有效 PDF（缺少 %PDF 文件头）")
    return dest


# ---------------------------------------------------------------------------
# 浏览器扩展配对
# ---------------------------------------------------------------------------


class PairManager:
    """一次性配对码 → 随机访问令牌。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._codes: dict[str, float] = {}  # code -> expires_at
        self._tokens: dict[str, float] = {}  # token -> expires_at
        self._lock = threading.Lock()

    def issue_code(self) -> str:
        code = "".join(str(secrets.randbelow(10)) for _ in range(6))
        with self._lock:
            self._codes[code] = time.time() + self.settings.pair_code_ttl_seconds
        return code

    def redeem(self, code: str) -> str:
        with self._lock:
            expires = self._codes.pop(code.strip(), 0)
            if expires < time.time():
                raise HTTPException(401, "配对码无效或已过期，请在本地应用重新生成")
            token = secrets.token_urlsafe(32)
            self._tokens[token] = time.time() + 7 * 86400  # 7 天
            return token

    def check(self, token: str) -> bool:
        with self._lock:
            expires = self._tokens.get(token.strip(), 0)
            if expires < time.time():
                self._tokens.pop(token.strip(), None)
                return False
            return True

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token.strip(), None)

    def new_pair_code(self) -> str:
        return self.issue_code()


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------


class RenderRequest(BaseModel):
    pass


class BlockPatch(BaseModel):
    text: Optional[str] = Field(default=None)
    latex: Optional[str] = Field(default=None)
    html: Optional[str] = Field(default=None)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    from app.parsing.paddle_cloud import cloud_enabled
    cloud = cloud_enabled()
    orch = Orchestrator(settings.workspace)
    jobs = JobStore(settings, orch)
    store = DocumentStore(settings)
    pairs = PairManager(settings)
    from app.services.translator import TranslateCache

    translator_cache = TranslateCache(settings.workspace / "translate-cache.json")

    app = FastAPI(title="论文翻译大师", version="0.5.0")
    from app.services.browser_sessions import BrowserSessions

    sessions = BrowserSessions()

    @app.get("/api/runtime")
    async def runtime_status():
        return sessions.status()

    @app.websocket("/api/runtime/browser")
    async def browser_connection(socket: WebSocket):
        origin = socket.headers.get("origin", "")
        if origin and origin not in (f"http://{socket.headers.get('host')}", f"https://{socket.headers.get('host')}"):
            await socket.close(code=1008)
            return
        await socket.accept()
        client = secrets.token_hex(16)
        sessions.connect(client)
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            sessions.disconnect(client)

    @app.get("/static/runtime.js")
    async def runtime_script():
        return FileResponse(Path(__file__).resolve().parent / "static" / "runtime.js", media_type="application/javascript")

    def reader_response(index: Path):
        html = index.read_text(encoding="utf-8")
        html = html.replace("</body>", '<script src="/static/runtime.js" defer></script></body>')
        return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})

    # CORS：仅允许登记的扩展 ID（普通网页不能调用本地上传接口）
    extension_origins = [
        f"chrome-extension://{eid}" for eid in settings.allow_extension_ids
    ]
    if extension_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=extension_origins,
            allow_methods=["POST", "GET"],
            allow_headers=["*"],
        )

    # ---- 任务 ----
    @app.post("/api/jobs")
    async def create_job(
        request: Request,
        upload: UploadFile = File(...),
        ocr_mode: str = Form(""),
        device: str = Form("auto"),
        force: bool = Form(False),
    ):
        if cloud and (ocr_mode != "official" or request.headers.get("x-paperloom-cloud") != "1"):
            raise HTTPException(403, "云端 OCR 需由已选择云端模式的 Zotero 插件提交")
        pdf_path = _save_upload(upload, settings)
        job_id = jobs.create(
            pdf_path,
            ConvertOptions(ocr_mode=ocr_mode or settings.ocr_mode, ocr_device=device, force=force),
        )
        return {"job_id": job_id, "state": "queued"}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        if not jobs.cancel(job_id):
            raise HTTPException(404, "任务不存在")
        return {"state": "cancelled"}

    # ---- 文档 ----
    @app.get("/api/documents")
    async def list_docs():
        return store.list()

    @app.get("/api/documents/{doc_id}")
    async def get_doc(doc_id: str):
        doc = store.get(doc_id)
        if not doc:
            raise HTTPException(404, "文档不存在")
        return doc

    @app.get("/api/documents/{doc_id}/blocks/{block_id}")
    async def get_block(doc_id: str, block_id: str):
        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        ir_path = doc_dir / "document.json"
        data = json.loads(ir_path.read_text(encoding="utf-8"))
        for b in data.get("blocks", []):
            if b.get("id") == block_id:
                return b
        raise HTTPException(404, "内容块不存在")

    @app.patch("/api/documents/{doc_id}/blocks/{block_id}")
    async def patch_block(doc_id: str, block_id: str, patch: BlockPatch):
        """修改块内容并重新渲染 HTML（Beta 功能）。"""
        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        ir_path = doc_dir / "document.json"
        data = json.loads(ir_path.read_text(encoding="utf-8"))
        target = None
        for b in data.get("blocks", []):
            if b.get("id") == block_id:
                target = b
                break
        if target is None:
            raise HTTPException(404, "内容块不存在")
        content = target.setdefault("content", {})
        for key in ("text", "latex", "html"):
            if getattr(patch, key) is not None:
                content[key] = getattr(patch, key)
        ir_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _rerender(settings, doc_dir)
        return {"ok": True, "block": target}

    @app.post("/api/documents/{doc_id}/render")
    async def rerender(doc_id: str, _body: RenderRequest | None = None):
        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        _rerender(settings, doc_dir)
        return {"ok": True}

    @app.get("/documents/{doc_id}/export.md")
    async def export_markdown(doc_id: str):
        """导出 OCR 识别结果（IR 块）为 Markdown 文件。"""
        from app.models.ir import Block
        from app.rendering.markdown_exporter import render_markdown

        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        doc_file = doc_dir / "document.json"
        if not doc_file.is_file():
            raise HTTPException(404, "文档数据不存在")
        data = json.loads(doc_file.read_text(encoding="utf-8"))
        blocks = [Block(**b) for b in data.get("blocks", [])]
        title = (data.get("document", {}).get("title") or "").strip()
        text = render_markdown(blocks)
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_") or doc_id
        filename = f"{slug[:80]}.md"
        return Response(
            content=text.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/settings")
    async def get_settings_api():
        """读取翻译/LLM 运行时设置(仅本地)。"""
        from app.services.translator import load_settings

        return load_settings(settings.workspace)

    @app.put("/api/settings")
    async def put_settings_api(body: dict):
        """保存翻译/LLM 运行时设置。"""
        from app.services.translator import save_settings

        try:
            return save_settings(settings.workspace, body or {})
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/settings/test")
    def test_translation_api(body: dict):
        from app.services.translator import http_chat, load_settings, translate_enabled, DEFAULT_SETTINGS

        config = load_settings(settings.workspace)
        config.update({k: v for k, v in body.items() if k in DEFAULT_SETTINGS})
        if not translate_enabled(config):
            raise HTTPException(400, "请填写服务地址与模型名")
        try:
            text = http_chat(config["llm_base_url"], config.get("llm_api_key", ""), config["llm_model"],
                             [{"role": "user", "content": "Translate into Chinese. Output only the translation: Hello."}], timeout=20)
            return {"ok": True, "text": text}
        except Exception as exc:
            detail = str(exc)
            if config.get("llm_api_key"):
                detail = detail.replace(config["llm_api_key"], "[redacted]")
            raise HTTPException(400, detail) from exc

    @app.get("/api/documents/{doc_id}/translate-info")
    async def translate_info(doc_id: str):
        """翻译单元统计(合并后的语义段数),供前端显示进度。"""
        from app.services.translator import TRANSLATABLE_TYPES, _block_text, build_units

        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        doc_file = doc_dir / "document.json"
        if not doc_file.is_file():
            raise HTTPException(404, "文档数据不存在")
        blocks = json.loads(doc_file.read_text(encoding="utf-8")).get("blocks", [])
        units = build_units(blocks)
        translateable = sum(
            1 for b in blocks
            if b.get("type") in TRANSLATABLE_TYPES and _block_text(b)
        )
        return {"total_units": len(units), "translateable_blocks": translateable}

    @app.post("/api/documents/{doc_id}/translate")
    async def translate_doc(doc_id: str, body: dict):
        """批量翻译文档块(服务端调 LLM,带缓存)。body: {"block_ids": [...], "lang_to": "zh"}"""
        from app.services.translator import load_settings, translate_batch, translate_enabled

        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        block_ids = (body or {}).get("block_ids") or []
        if not isinstance(block_ids, list) or not block_ids:
            raise HTTPException(400, "block_ids 不能为空")
        llm_settings = load_settings(settings.workspace)
        if (body or {}).get("lang_to"):
            llm_settings["lang_to"] = str(body["lang_to"]).strip()[:8]
        if not translate_enabled(llm_settings):
            raise HTTPException(400, "翻译未配置:请先在首页「翻译设置」填写 LLM 服务地址与模型")
        return await run_in_threadpool(translate_batch, doc_dir, block_ids, llm_settings, translator_cache)

    # ---- Zotero 持久化译文 ----
    @app.get("/api/zotero/status")
    async def zotero_status():
        from app.services.translation_export import EXPORT_VERSION
        return {"ok": True, "protocol": 2, "export_schema": EXPORT_VERSION,
                "managed_owner": os.environ.get("PAPERBRIDGE_ZOTERO_OWNER") or None,
                "cloud_ocr": cloud,
                "features": ["ocr-export", "translations", "offline-reader", "force-ocr", "cancel-ocr", "edited-source"],
                "ocr_modes": ["official", "auto", "native", "full", "hybrid"] +
                    (["rapidocr"] if __import__("importlib.util", fromlist=["find_spec"]).find_spec("rapid_doc") else [])}

    @app.post("/api/documents/{doc_id}/edited-source")
    async def edited_source(doc_id: str, revision: str = Form(...), upload: UploadFile = File(...)):
        from app.services.ocr_edit import import_source
        import tempfile
        import zipfile
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", doc_id) or not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise HTTPException(400, "编辑文档编号无效")
        root = settings.workspace / "edited-sources" / doc_id
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temporary:
            temporary = Path(temporary)
            package = temporary / "source.zip"
            size = 0
            with package.open("wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "编辑文档包超过 200MB")
                    stream.write(chunk)
            try:
                staging = temporary / "content"
                await run_in_threadpool(import_source, package, staging, revision)
                data = json.loads((staging / "document.json").read_text(encoding="utf-8"))
                if data["document"]["id"] != doc_id:
                    raise ValueError("编辑内容与文献不匹配")
                target = root / revision
                if not target.exists():
                    try:
                        staging.replace(target)
                    except OSError:
                        if not target.is_dir():
                            raise
            except (ValueError, KeyError, zipfile.BadZipFile) as exc:
                raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "revision": revision}

    @app.post("/api/documents/{doc_id}/ocr-export")
    async def save_ocr(doc_id: str):
        from app.services.translation_export import create_ocr_export

        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        try:
            result = await run_in_threadpool(create_ocr_export, doc_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result["download_url"] = f"/api/documents/{doc_id}/ocr/{result['id']}/bundle.zip"
        return result

    @app.get("/api/documents/{doc_id}/ocr/{export_id}/bundle.zip")
    async def ocr_bundle(doc_id: str, export_id: str):
        doc_dir = store.dir_for(doc_id)
        if not doc_dir or not re.fullmatch(r"[0-9a-f]{24}", export_id):
            raise HTTPException(404, "OCR 版本不存在")
        path = doc_dir / "ocr" / export_id / "bundle.zip"
        if not path.is_file():
            raise HTTPException(404, "OCR 版本不存在")
        return FileResponse(path, filename="bundle.zip")

    @app.get("/api/zotero/updates")
    async def zotero_updates():
        # Zotero requires update_url in plugin manifests. This locally installed
        # build has no automatic update channel; upgrades are installed as XPIs.
        return {"addons": {}}

    @app.post("/api/documents/{doc_id}/translations")
    async def save_translation(doc_id: str, body: dict):
        from app.services.translator import load_settings
        from app.services.translation_export import create_translation, IncompleteTranslation

        source_dir = None
        if body.get("source_revision"):
            revision = body["source_revision"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", doc_id) or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
                raise HTTPException(400, "编辑版本无效")
            source_dir = settings.workspace / "edited-sources" / doc_id / revision
            if not (source_dir / "document.json").is_file():
                raise HTTPException(404, "编辑版本尚未上传")
        doc_dir = store.dir_for(doc_id)
        if not doc_dir and source_dir:
            doc_dir = settings.documents_dir / doc_id
            doc_dir.mkdir(parents=True, exist_ok=True)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        config = load_settings(settings.workspace)
        lang = str(body.get("lang_to") or config.get("lang_to") or "zh").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,15}", lang):
            raise HTTPException(400, "目标语言代码无效，例如 zh、en、zh-TW")
        config["lang_to"] = lang
        # Keep desktop launcher alive even if its homepage is closed mid-export.
        client = "translation-" + secrets.token_hex(12)
        sessions.connect(client)
        try:
            if body.get("cache_only") and body.get("source_sha256"):
                import hashlib
                if hashlib.sha256((doc_dir / "document.json").read_bytes()).hexdigest() != body["source_sha256"]:
                    raise ValueError("译文对应的识别版本已更改，无法只升级显示；旧译文已保留")
            if body.get("force") is not None and not isinstance(body["force"], bool):
                raise ValueError("force 必须为布尔值")
            result = await run_in_threadpool(create_translation, doc_dir, config, translator_cache,
                                            force=body.get("force") is True, cache_only=body.get("cache_only") is True,
                                            revision=body.get("revision"), source_dir=source_dir)
        except IncompleteTranslation as exc:
            raise HTTPException(422, {"message": str(exc), "errors": exc.errors}) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            sessions.disconnect(client)
        result["download_url"] = f"/api/documents/{doc_id}/translations/{result['id']}/bundle.zip"
        return result

    @app.get("/api/documents/{doc_id}/translations/{export_id}/{filename}")
    async def translation_file(doc_id: str, export_id: str, filename: str):
        from app.services.translation_export import translation_path

        doc_dir = store.dir_for(doc_id)
        if not doc_dir and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", doc_id):
            candidate = settings.documents_dir / doc_id
            if candidate.is_dir() and (settings.workspace / "edited-sources" / doc_id).is_dir():
                doc_dir = candidate
        path = translation_path(doc_dir, export_id, filename) if doc_dir else None
        if path is None:
            raise HTTPException(404, "保存的译文不存在")
        return FileResponse(path, filename=filename)

    # ---- 阅读页 ----
    @app.get("/documents/{doc_id}/")
    async def reader_index(doc_id: str):
        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        index = doc_dir / "index.html"
        if not index.is_file():
            raise HTTPException(404, "文档尚未生成 HTML")
        return reader_response(index)

    @app.get("/documents/{doc_id}/{path:path}")
    async def reader_asset(doc_id: str, path: str):
        doc_dir = store.dir_for(doc_id)
        if not doc_dir:
            raise HTTPException(404, "文档不存在")
        target = (doc_dir / path).resolve()
        # Serve reader fixes for existing documents as well as newly generated ones.
        if path in ("assets/reader.js", "assets/reader.css"):
            return FileResponse(Path(__file__).resolve().parent / "static" / Path(path).name,
                                media_type=_media_type(path), headers={"Cache-Control": "no-cache"})
        if not str(target).startswith(str(doc_dir.resolve())) or not target.is_file():
            raise HTTPException(404, "资源不存在")
        return FileResponse(target, media_type=_media_type(path))

    def _media_type(path: str) -> str:
        # Windows 上 mimetypes.guess_type 可能返回错误类型，手动映射常见资源
        suffix = Path(path).suffix.lower()
        return {
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".html": "text/html",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".pdf": "application/pdf",
            ".md": "text/markdown",
        }.get(suffix, "application/octet-stream")

    # ---- 浏览器扩展 ----
    @app.get("/api/browser/health")
    async def browser_health():
        return {
            "ok": True,
            "service": "paperbridge",
            "version": "0.1.0",
            "protocol_version": PROTOCOL_VERSION,
        }

    @app.get("/api/browser/pair-code")
    async def pair_code():
        """本地应用侧生成配对码（在 Web 界面展示给用户）。"""
        code = pairs.new_pair_code()
        return {"pair_code": code, "expires_in": settings.pair_code_ttl_seconds}

    @app.post("/api/browser/pair")
    async def pair(body: dict):
        code = (body or {}).get("pair_code", "")
        token = pairs.redeem(code)
        return {"ok": True, "token": token, "protocol_version": PROTOCOL_VERSION}

    def _require_token(request: Request) -> None:
        token = request.headers.get("x-paperbridge-token", "")
        if not pairs.check(token):
            raise HTTPException(401, "令牌无效或已过期，请重新配对")

    @app.post("/api/browser/import-url")
    async def import_url(request: Request):
        if cloud:
            raise HTTPException(403, "请从 Zotero 提交云端 OCR")
        _require_token(request)
        body = await request.json()
        url = (body or {}).get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "仅支持 http/https URL")
        dest = settings.tmp_dir / f"import-{new_job_id()}.pdf"
        req = urllib.request.Request(url, headers={"User-Agent": "PaperBridge/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                ct = resp.headers.get("Content-Type", "")
                if not (
                    ct.startswith("application/pdf")
                    or ct == "application/octet-stream"
                    or url.lower().endswith(".pdf")
                ):
                    raise HTTPException(400, f"目标不是 PDF（Content-Type: {ct}）")
                size = 0
                with dest.open("wb") as f:
                    while chunk := resp.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_IMPORT_URL_BYTES:
                            raise HTTPException(413, "PDF 超过 200MB 限制")
                        f.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            raise HTTPException(502, f"下载失败：{type(e).__name__}: {e}") from e
        if dest.read_bytes()[:5] != b"%PDF-":
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "下载内容不是有效 PDF")
        job_id = jobs.create(dest, ConvertOptions(ocr_mode=settings.ocr_mode))
        return {"ok": True, "job_id": job_id}

    @app.post("/api/browser/upload")
    async def browser_upload(request: Request, upload: UploadFile = File(...)):
        if cloud:
            raise HTTPException(403, "请从 Zotero 提交云端 OCR")
        _require_token(request)
        pdf_path = _save_upload(upload, settings)
        job_id = jobs.create(pdf_path, ConvertOptions(ocr_mode=settings.ocr_mode))
        return {"ok": True, "job_id": job_id}

    @app.get("/api/browser/jobs/{job_id}")
    async def browser_job(request: Request, job_id: str):
        _require_token(request)
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    # ---- 服务状态 ----
    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": "0.1.0"}

    # ---- Web 首页（拖放上传 + 配对码 + 文档列表） ----
    @app.get("/")
    async def web_index():
        index = Path(__file__).resolve().parent / "static" / "index.html"
        return reader_response(index)

    app.state.jobs = jobs
    app.state.store = store
    app.state.pairs = pairs
    return app


def _rerender(settings: Settings, doc_dir: Path) -> None:
    """从现有 IR 重新生成 HTML。"""
    from app.models.ir import DocumentIR
    from app.rendering.html_renderer import HtmlRenderer

    ir_path = doc_dir / "document.json"
    ir = DocumentIR.model_validate_json(ir_path.read_text(encoding="utf-8"))
    html = HtmlRenderer().render_document(ir, doc_dir)
    (doc_dir / "index.html").write_text(html, encoding="utf-8")


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    app = create_app(get_settings())
    uvicorn.run(app, host=host, port=port, log_level="info")
