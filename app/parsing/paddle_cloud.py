"""AI Studio document parsing; no local model or model SDK."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import urlsplit

import httpx

from app.parsing.paddleocr_vl import OfficialPageResult

JOBS = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"


def cloud_enabled():
    return os.getenv("PAPERBRIDGE_OFFICIAL_ACCELERATOR") == "aistudio-vl16"


def _data(response):
    if response.status_code in (401, 403):
        raise ValueError("飞桨云端 OCR 密钥无效或没有权限，请检查设置")
    if response.status_code == 429:
        raise ValueError("飞桨云端 OCR 请求过多或额度不足，请稍后再试")
    body = response.json()
    if response.is_error or body.get("code") != 0:
        code = body.get("code")
        raise ValueError(f"飞桨云端 OCR 请求失败（HTTP {response.status_code}，错误码 {code if isinstance(code, int) else '未知'}）")
    if not isinstance(body.get("data"), dict):
        raise ValueError("飞桨云端 OCR 返回格式异常")
    return body["data"]


def _resource(client, url, limit):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (host == "paddleocr.aistudio-app.com" or host.endswith(".bcebos.com"))):
        raise ValueError("飞桨云端 OCR 返回了不受支持的资源地址")
    # Signed result URLs carry their own access. Never forward the API token.
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise ValueError(f"飞桨 OCR 结果下载失败（HTTP {response.status_code}）")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("飞桨 OCR 返回的资源过大")
        return bytes(data)


class PaddleCloudRecognizer:
    supports_document_progress = True
    supports_page_selection = True

    def recognize_document(self, pdf: Path, output: Path, *, progress=lambda _: None, pages=None):
        token_file = os.getenv("PAPERBRIDGE_CLOUD_TOKEN_FILE")
        if token_file:
            path = Path(token_file)
            token = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        else:
            token = os.getenv("PADDLEOCR_ACCESS_TOKEN", "").strip()
        if not token:
            raise ValueError("请在插件设置中填写飞桨 AI Studio Access Token")
        if pdf.stat().st_size > 50_000_000:
            raise ValueError("飞桨云端 OCR 本地上传上限为 50 MB，请先拆分 PDF")
        if pages and len(pages) > 1000:
            raise ValueError("单次云端识别最多支持 1000 页，请先拆分 PDF")
        try:
            with httpx.Client(timeout=60, follow_redirects=False) as client, pdf.open("rb") as stream:
                return self._parse(client, token, stream, output, progress, pages)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as error:
            # Do not leak signed URLs, response bodies or credentials into logs.
            raise ValueError(f"飞桨云端 OCR 网络或响应异常（{type(error).__name__}）") from None

    def _parse(self, client, token, stream, output, progress, pages):
        headers = {"Authorization": "Bearer " + token}
        fields = {"model": MODEL}
        if pages:
            fields["pageRanges"] = (f"{pages[0]}-{pages[-1]}" if pages == list(range(pages[0], pages[-1] + 1))
                                    and len(pages) > 1 else ",".join(map(str, pages)))
        progress(0)
        job = _data(client.post(JOBS, headers=headers, data=fields,
                                files={"file": ("document.pdf", stream, "application/pdf")}))["jobId"]
        if not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", job):
            raise ValueError("飞桨云端 OCR 返回了无效任务编号")
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            data = _data(client.get(f"{JOBS}/{job}", headers=headers))
            state = data.get("state")
            if state == "done":
                break
            if state not in ("pending", "running"):
                raise ValueError("飞桨云端 OCR 未能完成解析，请检查文件或服务额度")
            count = (data.get("extractProgress") or {}).get("extractedPages", 0)
            progress(count if isinstance(count, int) else 0)
            time.sleep(2)
        else:
            raise ValueError("飞桨云端 OCR 等待超时")
        lines = _resource(client, data["resultUrl"]["jsonUrl"], 64 * 1024 * 1024).splitlines()
        parsed = []
        for line in filter(bytes.strip, lines):
            row = json.loads(line)
            if row.get("errorCode", 0) != 0:
                raise ValueError("飞桨云端 OCR 返回了失败页")
            parsed.extend(row["result"]["layoutParsingResults"])
        if not parsed or (pages and len(parsed) != len(pages)):
            raise ValueError("飞桨云端 OCR 返回的页数不完整，请检查服务额度或拆分 PDF")
        results = []
        for index, page in enumerate(parsed):
            number = pages[index] if pages else index + 1
            progress(index + 1)
            raw = page["prunedResult"]
            if not isinstance(raw, dict) or "parsing_res_list" not in raw:
                raise ValueError("飞桨云端 OCR 未返回页面版面数据")
            markdown = page["markdown"]
            directory = output / f"page-{number:03d}"
            directory.mkdir(parents=True, exist_ok=True)
            for name, url in (markdown.get("images") or {}).items():
                path = PurePosixPath(name)
                if (len(path.parts) != 2 or path.parts[0] != "imgs"
                        or not re.fullmatch(r"[\w.-]+\.(?:png|jpe?g|webp)", path.name, re.I)):
                    raise ValueError("飞桨云端 OCR 返回了无效图片路径")
                target = directory / "imgs" / path.name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(_resource(client, url, 16 * 1024 * 1024))
            results.append(OfficialPageResult(number, {"res": raw}, markdown["text"], directory))
        return results
