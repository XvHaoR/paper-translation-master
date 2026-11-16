import pathlib
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from app.config import Settings
from app.server import create_app, JobStore
from app.pipeline.orchestrator import ConvertOptions
from fastapi.testclient import TestClient
import pymupdf

key = JobStore._job_key('test-document', ConvertOptions())
with patch('app.parsing.reading_flow.PARSER_VERSION', 'next-parser-version'):
    assert JobStore._job_key('test-document', ConvertOptions()) != key

with tempfile.TemporaryDirectory(prefix="paperloom-api-test-") as tmp:
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_textbox((50, 70, 500, 400), "A native PDF without OCR configuration. " * 30, fontsize=12)
    contents = pdf.tobytes()
    pdf.close()
    app = create_app(Settings(workspace=pathlib.Path(tmp)))
    with TestClient(app) as client:
        status = client.get("/api/zotero/status").json()
        assert "auto-layout" in status["features"]
        files = {"upload": ("native.pdf", contents, "application/pdf")}
        job = client.post("/api/jobs", files=files)
        assert job.status_code == 200, job.text
        jid = job.json()["job_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.get("/api/jobs/" + jid).json()
            if result["state"] in {"ready", "needs_review", "failed"}:
                break
            time.sleep(.05)
        assert result["state"] == "ready", result
        assert result["options"]["ocr_mode"] == "auto"
        assert "ocr_fallback" not in result["options"]
        assert "native-only" in status["features"]
        assert "optional-ocr" not in status["features"]
        artifact = client.post("/api/documents/" + result["document_id"] + "/ocr-export")
        assert artifact.status_code == 200, artifact.text
        bundle = client.get(artifact.json()["download_url"])
        assert bundle.status_code == 200 and bundle.content.startswith(b"PK")
        assert client.post("/api/jobs", files=files, data={"ocr_fallback": "true"}).status_code == 422
        for mode in ["official", "full", "hybrid", "invalid"]:
            assert client.post("/api/jobs", files=files, data={"ocr_mode": mode}).status_code == 422
print("PASS: native-only API, saved export and rejection of removed OCR requests.")
