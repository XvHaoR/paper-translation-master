"""Verify a freshly installed macOS runtime through the managed HTTP service."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid


def request(url: str, route: str, **kwargs):
    with urlopen(Request(url + route, **kwargs), timeout=10) as response:
        return json.load(response)


def verify(descriptor: Path) -> None:
    config = json.loads(descriptor.read_text("utf-8"))
    root = Path(config["projectRoot"])
    pdf = Path(__file__).resolve().parents[1] / "fixtures/sample_native.pdf"
    with tempfile.TemporaryDirectory(prefix="paperloom-smoke-") as temporary:
        directory = Path(temporary)
        state = directory / "state.json"
        lease = directory / "lease.json"
        instance = uuid.uuid4().hex
        lease.write_text(json.dumps({"instance": instance, "time": time.time()}))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        arguments = [
            config["pythonPath"], "-s", "-X", "utf8", str(root / "scripts/zotero_service.py"),
            "--state-file", str(state), "--lease-file", str(lease), "--instance", instance,
            "--port", str(port), "--backend", "local", "--ocr-mode", "layout",
            "--parent-pid", str(os.getpid()), "--runtime-config", str(descriptor),
        ]
        with subprocess.Popen(arguments, cwd=root) as service:
            try:
                for _ in range(200):
                    status = json.loads(state.read_text("utf-8")) if state.exists() else {}
                    if status.get("phase") == "ready":
                        break
                    assert status.get("phase") != "failed" and service.poll() is None, status
                    time.sleep(0.25)
                else:
                    raise AssertionError("The macOS runtime did not start")

                url = status["service_url"]
                assert status["owned"]
                assert request(url, "/api/zotero/status")["ocr_modes"] == ["layout", "cloud", "mineru", "mineru-local"]
                boundary = uuid.uuid4().hex
                payload = (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="upload"; filename="paper.pdf"\r\n'
                    'Content-Type: application/pdf\r\n\r\n'
                ).encode() + pdf.read_bytes() + (
                    f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="ocr_mode"\r\n\r\n'
                    f'layout\r\n--{boundary}--\r\n'
                ).encode()
                job = request(url, "/api/jobs", data=payload,
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
                for _ in range(120):
                    result = request(url, f"/api/jobs/{job['job_id']}")
                    if result["state"] in {"ready", "needs_review", "failed"}:
                        break
                    time.sleep(0.25)
                assert result["state"] in {"ready", "needs_review"}, result
                artifact = request(url, f"/api/documents/{result['document_id']}/ocr-export", method="POST")
                assert artifact["download_url"]
            finally:
                lease.unlink(missing_ok=True)
                try:
                    assert service.wait(timeout=12) == 0
                except subprocess.TimeoutExpired:
                    service.kill()
                    raise
            try:
                request(url, "/api/zotero/status")
            except URLError:
                pass
            else:
                raise AssertionError("The API still accepts requests after Zotero releases the lease")
    print("Installed runtime passed PDF parsing, export, and service shutdown.")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
