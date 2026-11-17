"""Exercise the unified service using the real layout engine and a mock cloud provider."""
import io
import time
import zipfile
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.parsing.paddleocr_vl import OfficialPageResult
from app.server import create_app


def wait_for_export(client, submitted):
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["job_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in {"ready", "needs_review", "failed"}:
            break
        time.sleep(0.05)
    else:
        pytest.fail("Recognition did not finish")
    assert job["state"] in {"ready", "needs_review"}, job
    response = client.post(f"/api/documents/{job['document_id']}/ocr-export")
    assert response.status_code == 200, response.text
    artifact = response.json()
    bundle = client.get(artifact["download_url"])
    assert bundle.status_code == 200
    return artifact, zipfile.ZipFile(io.BytesIO(bundle.content))


@pytest.mark.skipif(not hasattr(pymupdf.Document, "to_json"), reason="requires the layout extra")
def test_cloud_and_layout_keep_distinct_snapshots_and_translate_exact_source(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBRIDGE_UNIFIED", "1")
    calls = []

    def cloud(self, pdf, output, *, progress, pages):
        calls.append(pages)
        return [OfficialPageResult(1, {"res": {"width": 600, "height": 800,
                   "parsing_res_list": [{"block_label": "text", "block_content": "Cloud source paragraph.",
                                         "block_bbox": [45, 68, 500, 190]}]}},
                   "Cloud source paragraph.", output)]

    monkeypatch.setattr("app.parsing.paddle_cloud.PaddleCloudRecognizer.recognize_document", cloud)
    workspace = tmp_path / "recognition"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_textbox((50, 75, 510, 320), "Native source paragraph. " * 12, fontsize=12)
        original = pdf.tobytes()
    files = {"upload": ("sample.pdf", original, "application/pdf")}

    with TestClient(create_app(Settings(workspace=workspace, ocr_mode="layout"))) as client:
        assert client.get("/api/zotero/status").json()["ocr_modes"] == ["layout", "cloud"]
        assert client.post("/api/jobs", files=files, data={"ocr_mode": "cloud"}).status_code == 403
        assert not calls
        native, native_zip = wait_for_export(client, client.post("/api/jobs", files=files, data={"ocr_mode": "layout"}))
        assert "Native source paragraph" in native_zip.read("ocr.md").decode()
        assert not calls
        remote, remote_zip = wait_for_export(client, client.post("/api/jobs", files=files,
            data={"ocr_mode": "cloud"}, headers={"x-paperloom-cloud": "1"}))
        assert "Cloud source paragraph" in remote_zip.read("ocr.md").decode()
        assert calls == [[1]]
        assert native["document_id"] != remote["document_id"]
        assert native["source_revision"] != remote["source_revision"]
        assert (workspace / "documents" / native["document_id"] / "original.pdf").read_bytes() == original
        assert client.get(native["download_url"]).status_code == 200

    # Reopen the attachment on a different machine/workspace without the source service.
    def translate(directory, ids, *_args):
        assert "Cloud source paragraph" in (Path(directory) / "document.json").read_text("utf-8")
        return {"results": {ident: {"text": "已翻译原文快照。"} for ident in ids}}

    monkeypatch.setattr("app.services.translator.translate_batch", translate)
    with TestClient(create_app(Settings(workspace=tmp_path / "llm"))) as client:
        settings = client.put("/api/settings", json={"llm_base_url": "http://localhost:1/v1", "llm_model": "fixture"})
        assert settings.status_code == 200
        source_route = f"/api/documents/{remote['document_id']}"
        revision = remote["source_revision"]
        imported = client.post(source_route + "/edited-source", data={"revision": revision},
                               files={"upload": ("source.zip", remote_zip.read("source.zip"), "application/zip")})
        assert imported.status_code == 200, imported.text
        translation = client.post(source_route + "/translations", json={"source_revision": revision})
        assert translation.status_code == 200, translation.text
        assert translation.json()["ocr_revision"] == revision
        assert calls == [[1]]
