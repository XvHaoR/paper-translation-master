"""Local PDF text/layout → structured document → offline reader."""
from __future__ import annotations
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from app.models.ir import DocumentIR
from app.pipeline.preflight import run_preflight
logger = logging.getLogger(__name__)
ProgressFn = Callable[[str, int, int, str], None]

def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件写完后原子替换，避免中断留下半截 JSON/HTML。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass
class ConvertOptions:
    # Kept as a storage/API compatibility name; only native layout is supported.
    ocr_mode: str = "auto"
    pages: str = ""
    force: bool = False

@dataclass
class ConversionResult:
    ok: bool = False
    document_id: str = ""
    doc_dir: Path | None = None
    ir: Optional[DocumentIR] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    status: str = "failed"  # ready | needs_review | failed

    @property
    def review_needed(self) -> bool:
        return any(b.quality.needs_review for b in (self.ir.blocks if self.ir else []))


def _parse_page_range(spec: str, page_count: int) -> list[int]:
    """解析页码范围 "1-5,8,10-12"。"""
    if not spec or spec.strip() == "":
        return list(range(1, page_count + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(part))
    return sorted({p for p in pages if 1 <= p <= page_count})


class Orchestrator:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        (self.workspace / "documents").mkdir(parents=True, exist_ok=True)

    def convert(
        self,
        pdf_path: str | Path,
        options: ConvertOptions | None = None,
        progress: ProgressFn | None = None,
    ) -> ConversionResult:
        opts = options or ConvertOptions()
        if opts.ocr_mode not in {"auto", "native"}:
            raise ValueError("此版本仅支持本地文字和版面解析")
        opts.ocr_mode = "auto"
        t0 = time.time()
        pdf_path = Path(pdf_path)
        result = ConversionResult()
        total_pages = 0

        def report(stage: str, page: int = 0, message: str = "") -> None:
            if progress:
                progress(stage, page, total_pages, message)
            logger.info("[%s] %s", stage, message or f"page {page}")

        # 1. 预检
        report("preflight", message=f"预检 {pdf_path.name}")
        pre = run_preflight(str(pdf_path))
        total_pages = pre.page_count
        result.warnings += pre.warnings
        if not pre.ok:
            result.errors += pre.errors
            return result
        result.document_id = pre.pdf_hash
        doc_dir = self.workspace / "documents" / result.document_id
        ir_path = doc_dir / "document.json"
        index_path = doc_dir / "index.html"

        # 2. 缓存命中（须匹配解析器缓存版本，算法升级后旧缓存自动失效）
        from app.parsing.reading_flow import PARSER_VERSION as native_parser_version
        if (
            not opts.force
            and opts.ocr_mode == "auto"
            and ir_path.is_file()
            and index_path.is_file()
        ):
            try:
                ir = DocumentIR.model_validate_json(ir_path.read_text(encoding="utf-8"))
                if (ir.document.parser.get("cache_version") != self._cache_version()
                    or ir.document.parser.get("settings", {}).get("ocr_mode") != opts.ocr_mode
                    or (opts.ocr_mode == "auto" and (
                        ir.document.parser.get("auto_parser_version") != native_parser_version
                        or ir.document.parser.get("settings", {}).get("pages") != _parse_page_range(opts.pages, pre.page_count)))):
                    logger.info("解析器缓存版本升级，旧文档重新解析")
                else:
                    result.ok = True
                    result.ir = ir
                    result.doc_dir = doc_dir
                    result.status = "needs_review" if ir and any(
                        b.quality.needs_review for b in ir.blocks
                    ) else "ready"
                    result.warnings.append("命中缓存，直接复用已生成文档")
                    result.duration_ms = int((time.time() - t0) * 1000)
                    return result
            except Exception as e:  # noqa: BLE001
                logger.warning("缓存文档损坏，重新生成：%s", e)

        pages = _parse_page_range(opts.pages, pre.page_count)
        if not pages:
            result.errors.append("页码范围未包含有效页面")
            return result
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "assets").mkdir(exist_ok=True)
        (doc_dir / "assets" / "crops").mkdir(exist_ok=True)
        (doc_dir / "assets" / "figures").mkdir(exist_ok=True)
        (doc_dir / "assets" / "page-previews").mkdir(exist_ok=True)
        (doc_dir / "raw").mkdir(exist_ok=True)
        # 文档目录由内容哈希命名；目标已存在时必然是同一份 PDF。
        # 不重复覆盖还能避开 Windows 上 copy2 继承源文件只读属性后的 PermissionError。
        original_pdf = doc_dir / "original.pdf"
        if not original_pdf.is_file():
            shutil.copy2(pdf_path, original_pdf)
        self._prepare_static_assets(doc_dir)

        return self._convert_auto(pdf_path, pre, pages, opts, doc_dir, result, report, t0)

    def _convert_auto(self, pdf_path, pre, pages, opts, doc_dir, result, report, started_at):
        from app.parsing.auto_pdf import parse_document
        from app.rendering.markdown_exporter import render_markdown
        from app.services.offline_reader import copy_runtime
        from app.services.translation_export import reader_page
        try:
            ir, warnings = parse_document(pdf_path, doc_dir, result.document_id, pages,
                                          report=report)
            ir.document.parser["cache_version"] = self._cache_version()
            result.warnings = warnings
            markdown = render_markdown(ir.blocks)
            copy_runtime(doc_dir)
            _atomic_write_text(doc_dir / "index.html", reader_page(ir.document.title, "en", markdown, blocks=ir.blocks))
            _atomic_write_text(doc_dir / "ocr.md", markdown)
            _atomic_write_text(doc_dir / "document.json", ir.model_dump_json(indent=2))
            result.ir, result.doc_dir = ir, doc_dir
            result.ok = bool(ir.blocks)
            if not result.ok:
                result.errors.append("PDF 没有可显示的内容")
            result.status = "needs_review" if result.review_needed else "ready" if result.ok else "failed"
        except Exception as error:
            result.errors.append(f"自动解析失败：{type(error).__name__}: {error}")
        result.duration_ms = int((time.time() - started_at) * 1000)
        report("done", message=f"解析完成：{result.status}")
        return result


    def _prepare_static_assets(self, doc_dir: Path) -> None:
        """把 reader.css / reader.js / MathJax 复制进文档目录，保证可整体移动。"""
        static_dir = Path(__file__).resolve().parents[1] / "static"
        assets = doc_dir / "assets"
        for name in ("reader.css", "reader.js", "table_excel.js"):
            src = static_dir / name
            if src.is_file():
                shutil.copy2(src, assets / name)
        mj_src = static_dir / "mathjax"
        if mj_src.is_dir():
            # 拷贝完整 MathJax 分发（tex-mml-chtml.js + input/tex/extensions + output/chtml/fonts），
            # 否则 \boldsymbol 等扩展触发动态加载时 404，导致整页公式不渲染
            mj_dst = assets / "mathjax"
            if mj_dst.exists():
                shutil.rmtree(mj_dst)
            shutil.copytree(mj_src, mj_dst)


    def _cache_version(self) -> str:
        from app.parsing.reading_flow import PARSER_VERSION
        return PARSER_VERSION + ":native-only"
