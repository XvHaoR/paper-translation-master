"""MinerU hosted v4 and self-hosted Tasks v1. Both return one structured ZIP."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit


CLOUD = "mineru"
SELF_HOSTED = "mineru-local"
MODES = (CLOUD, SELF_HOSTED)
CACHE_VERSION = "mineru-structured-v1"
OFFICIAL_API = "https://mineru.net/api/v4"
DEFAULT_SERVER = "http://127.0.0.1:8000"
_MAX_DOWNLOAD = 512 * 1024 * 1024
_TRANSIENT = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Connection:
    base: str
    token: str = field(repr=False)

    @property
    def cache_key(self):
        return hashlib.sha256(self.base.encode()).hexdigest()[:12]

    @classmethod
    def read(cls, mode):
        filename = os.getenv("PAPERBRIDGE_MINERU_CONFIG")
        values = {}
        if filename and Path(filename).is_file():
            try:
                values = json.loads(Path(filename).read_text(encoding="utf-8"))
                if not isinstance(values, dict):
                    raise ValueError
            except (OSError, ValueError):
                raise ValueError("MinerU 配置读取失败，请在 Zotero 设置中重新保存") from None
        if mode == CLOUD:
            token = values.get("cloudToken", "")
            if not token:
                raise ValueError("请在「论文翻译大师 → 设置 → MinerU」填写官方 API Token")
            return cls(OFFICIAL_API, token)
        base = str(values.get("serverURL") or DEFAULT_SERVER).rstrip("/")
        url = urlsplit(base)
        if url.scheme not in ("http", "https") or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError("MinerU 自部署地址须为 HTTP(S) 服务根地址")
        return cls(base, values.get("serverToken", ""))


def _http_error(status):
    reasons = {401: "密钥无效或已过期", 403: "无访问权限或链接已过期", 404: "接口不存在，请检查服务地址和版本",
               413: "文件超过服务限制", 429: "请求过多或可用额度不足"}
    return ValueError(f"MinerU 请求失败（HTTP {status}）：{reasons.get(status, '请检查服务状态后重试')}")


def _json(response, cloud):
    if not response.is_success:
        raise _http_error(response.status_code)
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("MinerU 返回的任务信息格式异常")
    if not cloud:
        return body
    code = body.get("code")
    if code != 0:
        if code in ("A0202", "A0211"):
            raise ValueError("MinerU 官方 Token 无效或已过期，请在设置中更新")
        safe_code = str(code) if re.fullmatch(r"[\w-]{1,24}", str(code)) else "未知"
        raise ValueError(f"MinerU 官方 API 拒绝请求（{safe_code}），请核对文件、额度和账号权限")
    if not isinstance(body.get("data"), dict):
        raise ValueError("MinerU 未返回任务数据")
    return body["data"]


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\w.-]{1,160}", value) or value in (".", ".."):
        raise ValueError("MinerU 返回了无效任务编号")
    return value


def _origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def _resource(url, connection, cloud):
    if not isinstance(url, str) or not url:
        raise ValueError("MinerU 未提供文件传输地址")
    url = urljoin(connection.base + "/", url)
    parsed = urlsplit(url)
    same = _origin(url) == _origin(connection.base)
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("MinerU 文件传输地址无效")
    if cloud:
        trusted = ("mineru.net", "openxlab.org.cn", "aliyuncs.com")
        if parsed.scheme != "https" or parsed.port not in (None, 443) or not any(
                parsed.hostname == host or parsed.hostname.endswith("." + host) for host in trusted):
            raise ValueError("MinerU 官方接口返回了未知的文件存储地址")
    elif not same and parsed.scheme != "https":
        raise ValueError("MinerU 跨站文件传输需要 HTTPS")
    # Hosted v4 uses signed links. Self-hosted v1 authenticates same-origin requests.
    headers = {"Authorization": "Bearer " + connection.token} if not cloud and same and connection.token else {}
    return url, headers


def _retry_read(operation):
    import httpx
    for attempt in range(3):
        try:
            return operation()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if status is not None and status not in _TRANSIENT or attempt == 2:
                if status is not None:
                    raise _http_error(status) from None
                raise ValueError("MinerU 连接中断，重复读取任务或结果后仍未恢复") from None
            time.sleep(2 ** attempt)


def _write_download(response, destination):
    with destination.open("wb") as output:
        for chunk in response.iter_bytes(1024 * 1024):
            if output.tell() + len(chunk) > _MAX_DOWNLOAD:
                raise ValueError("MinerU 结果包超过 512 MiB，请拆分论文")
            output.write(chunk)


def _download(client, url, connection, cloud, destination):
    def get():
        current = url
        for _ in range(5):
            target, headers = _resource(current, connection, cloud)
            with client.stream("GET", target, headers=headers) as response:
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(target, response.headers["location"])
                    continue
                response.raise_for_status()
                _write_download(response, destination)
                return
        raise ValueError("MinerU 结果链接重定向次数过多")
    _retry_read(get)


def _one_file(items):
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("MinerU 任务未返回本次提交的文件")
    return items[0]


def fetch_bundle(pdf, destination, mode, connection, *, page_count, report, client=None):
    import httpx
    cloud = mode == CLOUD
    size = pdf.stat().st_size
    if cloud and (size > 200_000_000 or page_count > 200):
        raise ValueError("MinerU 官方 API 单个 PDF 最多 200 MB、200 页，请先拆分")
    owned_client = client is None
    loopback = urlsplit(connection.base).hostname in ("127.0.0.1", "::1", "localhost")
    client = client or httpx.Client(timeout=httpx.Timeout(120, connect=20), follow_redirects=False, trust_env=not loopback)
    headers = {"Authorization": "Bearer " + connection.token} if connection.token else {}
    try:
        report("recognizing", 0, "正在向 MinerU 提交论文")
        if cloud:
            job = _json(client.post(connection.base + "/file-urls/batch", headers=headers, json={
                "files": [{"name": "document.pdf"}], "model_version": "vlm",
                "enable_formula": True, "enable_table": True}), True)
            status_url = connection.base + "/extract-results/batch/" + _id(job["batch_id"])
            links = job.get("file_urls")
            if not isinstance(links, list) or len(links) != 1:
                raise ValueError("MinerU 未返回本次 PDF 的上传链接")
            upload_url = links[0]
            upload_headers = {}
        else:
            receipt = client.post(connection.base + "/v1/tasks", headers=headers, json={
                "files": [{"file_name": "document.pdf", "bytes": size}], "output_formats": ["default"]})
            if receipt.status_code in (404, 405):
                # At this point only the manifest has been sent. Verify the legacy route before uploading.
                schema = client.get(connection.base + "/openapi.json", headers=headers)
                if schema.is_success and "post" in schema.json().get("paths", {}).get("/file_parse", {}):
                    report("recognizing", 0, "MinerU 自部署服务正在解析论文")
                    fields = {"return_middle_json": "true", "return_images": "true", "return_md": "false",
                              "response_format_zip": "true", "formula_enable": "true", "table_enable": "true"}
                    with pdf.open("rb") as stream, client.stream("POST", connection.base + "/file_parse",
                            headers=headers, files={"files": ("document.pdf", stream, "application/pdf")},
                            data=fields, timeout=httpx.Timeout(1800, connect=20)) as result:
                        if not result.is_success:
                            raise _http_error(result.status_code)
                        _write_download(result, destination)
                    return
            job = _json(receipt, False)
            status_url = connection.base + "/v1/tasks/" + _id(job["task_id"])
            file = _one_file(job["files"])
            file_id = _id(file["file_id"])
            upload = file.get("upload") or {}
            upload_url = upload.get("url")
            if file.get("status") == "waiting-file" and not upload_url:
                raise ValueError("MinerU 自部署服务未返回上传链接")
            upload_headers = {k: v for k, v in (upload.get("headers") or {}).items()
                              if k.lower() not in ("authorization", "cookie", "host", "proxy-authorization")}
        if upload_url:
            target, auth = _resource(upload_url, connection, cloud)
            with pdf.open("rb") as stream:
                response = client.put(target, headers={**upload_headers, **auth, "Content-Length": str(size)}, content=stream)
            if not response.is_success:
                raise _http_error(response.status_code)

        def poll():
            response = client.get(status_url, headers=headers)
            response.raise_for_status()
            return _json(response, cloud)

        deadline = time.monotonic() + 3300
        while time.monotonic() < deadline:
            snapshot = _retry_read(poll)
            rows = snapshot.get("extract_result" if cloud else "files")
            if cloud and rows == []:
                file = {"state": "pending"}
            else:
                file = _one_file(rows)
            if not cloud and file.get("file_id") != file_id:
                raise ValueError("MinerU 返回的文件编号与提交记录不符")
            state = file.get("state" if cloud else "status")
            if state in ("done", "completed"):
                report("recognizing", page_count, "正在下载 MinerU 识别结果")
                _download(client, file.get("full_zip_url" if cloud else "result_url"), connection, cloud, destination)
                return
            if state not in ("waiting-file", "pending", "running", "converting"):
                detail = str(file.get("err_msg") or (file.get("error") or {}).get("code") or "")
                detail = re.sub(r"https?://\S+|Bearer\s+\S+", "[已隐藏]", detail, flags=re.I)
                if connection.token:
                    detail = detail.replace(connection.token, "[已隐藏]")
                raise ValueError("MinerU 解析失败" + (f"：{detail[:180]}" if detail else "，请检查服务端任务记录"))
            count = (file.get("extract_progress") or {}).get("extracted_pages", 0)
            report("recognizing", count if isinstance(count, int) else 0,
                   "MinerU 正在解析论文" if state in ("running", "converting") else "MinerU 正在排队")
            time.sleep(2)
        raise ValueError("等待 MinerU 识别结果超时")
    except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"MinerU 连接或返回格式异常（{type(exc).__name__}）") from None
    finally:
        if owned_client:
            client.close()
