"""Exercise hosted and self-hosted wire contracts through the real Zotero export."""
import io
import json
import zipfile

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.parsing import mineru_client as mc
from app.parsing.mineru_result import import_bundle
from app.server import create_app
from tests.integration.test_unified_recognition import wait_for_export


def pdf_bytes():
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=600)
        page.insert_text((40, 60), "MinerU fixture")
        page.insert_text((40, 130), "The observed growth rate.")
        page.draw_rect((40, 180, 200, 260))
        return doc.tobytes()


def bundle(name, data):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, json.dumps(data))
    return stream.getvalue()


def legacy():
    def region(kind, rect, span, **extra):
        return {"type": kind, "bbox": rect, "lines": [{"spans": [span]}], **extra}
    blocks = [
        region("title", [40, 40, 220, 66], {"type": "text", "content": "MinerU fixture"}, level=1),
        region("text", [40, 110, 260, 140], {"type": "text", "content": "The observed growth rate."}),
        {"type": "image", "blocks": [
            region("image_body", [40, 180, 200, 260], {"type": "image", "image_path": "missing.jpg"}),
            region("image_caption", [40, 260, 220, 280], {"type": "text", "content": "Fig. 1 | Growth."})]},
        {"type": "table", "blocks": [
            region("table_caption", [40, 302, 240, 324], {"type": "text", "content": "Table 1. Values"}),
            region("table_body", [40, 328, 240, 390], {"type": "table", "html": "<table><tr><th colspan='2'>Rate</th></tr><tr><td>A</td><td>2</td></tr></table>"})]},
        region("interline_equation", [42, 410, 210, 435], {"type": "interline_equation", "content": r"E=mc^{2}"}),
    ]
    return {"pdf_info": [{"page_idx": 0, "page_size": [400, 600], "para_blocks": blocks}]}


def current():
    return {"schema": "docvortex.middle", "schema_version": "2.0", "pages": [{"page_idx": 0, "blocks": [
        {"type": "doc_title", "bbox": [.1, .07, .55, .11], "content": [{"type": "text", "content": "MinerU fixture"}]},
        {"type": "text", "bbox": [.1, .18, .65, .23], "content": [
            {"type": "text", "content": "The observed growth rate "}, {"type": "equation_inline", "content": "r"}, {"type": "text", "content": "."}]},
        {"type": "equation", "bbox": [.1, .4, .5, .6], "content": "E=mc^{2}"},
        {"type": "equation", "bbox": [.1, .6, .45, .68], "content": ""},
        {"type": "table", "bbox": [.1, .7, .85, .88], "content": [
            {"type": "table_body", "content": "<table><tr><th>Rate</th></tr><tr><td>23</td></tr></table>"},
            {"type": "table_caption", "content": [{"type": "text", "content": "Table 1. Results"}]}]}]}]}


@pytest.mark.parametrize("mode", [mc.CLOUD, mc.SELF_HOSTED])
def test_provider_through_job_attachment_and_translation(mode, tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBRIDGE_UNIFIED", "1")
    server = "http://127.0.0.1:9988/mineru"
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"cloudToken": "cloud-test-only", "serverURL": server, "serverToken": "local-test-only"}))
    monkeypatch.setenv("PAPERBRIDGE_MINERU_CONFIG", str(config))
    pdf = pdf_bytes()
    archive_data = (bundle("layout.json", legacy()) if mode == mc.CLOUD
                    else bundle("document/middle_json.json", current()))
    cloud = mode == mc.CLOUD
    upload = "https://mineru-input.oss-cn-shanghai.aliyuncs.com/up?signature=fixture" if cloud else server + "/upload"
    result = "https://cdn-mineru.openxlab.org.cn/document.zip" if cloud else server + "/result"
    calls = []
    def wire(request):
        path = request.url.path
        calls.append((request.method, path))
        auth = request.headers.get("authorization")
        if request.url.host in ("mineru.net", "127.0.0.1"):
            assert auth == ("Bearer cloud-test-only" if cloud else "Bearer local-test-only")
        else:
            assert auth is None
        if path.endswith("/file-urls/batch"):
            entry = json.loads(request.content)
            assert entry["model_version"] == "vlm" and entry["enable_table"] and entry["enable_formula"]
            return httpx.Response(200, json={"code": 0, "data": {"batch_id": "test", "file_urls": [upload]}})
        if path.endswith("/v1/tasks"):
            assert json.loads(request.content)["files"][0]["bytes"] == len(pdf)
            return httpx.Response(202, json={"task_id": "test", "files": [{"file_id": "one", "status": "waiting-file", "upload": {"url": upload, "headers": {}}}]})
        if request.method == "PUT":
            assert request.content == pdf
            return httpx.Response(200)
        if path.endswith("/test"):
            if calls.count(("GET", path)) == 1:
                return httpx.Response(503)
            row = {"file_id": "one", "status": "completed", "result_url": result}
            body = {"code": 0, "data": {"extract_result": [{"state": "done", "full_zip_url": result}]}} if cloud else {"files": [row]}
            return httpx.Response(200, json=body)
        if path.endswith("/result"):
            return httpx.Response(302, headers={"Location": "https://example.com/result.zip"})
        assert path.endswith(".zip")
        return httpx.Response(200, content=archive_data)

    fetch = mc.fetch_bundle
    with httpx.Client(transport=httpx.MockTransport(wire)) as mock:
        monkeypatch.setattr(mc, "fetch_bundle", lambda *a, **kw: fetch(*a, client=mock, **kw))
        submitted = {"files": {"upload": ("fixture.pdf", pdf, "application/pdf")}, "data": {"ocr_mode": mode}}
        workspace = tmp_path / "work"
        prior = workspace / "documents" / "old-snapshot"
        prior.mkdir(parents=True)
        (prior / "document.json").write_text(json.dumps({"document": {"id": "old-snapshot", "created_at": None}, "blocks": []}))
        with TestClient(create_app(Settings(workspace=workspace, ocr_mode="layout"))) as api:
            assert api.post("/api/jobs", **submitted).status_code == 403
            assert api.post("/api/jobs", **submitted, headers={"X-PaperLoom-Cloud": "1"}).status_code == 403
            assert not calls
            chosen = {"X-PaperLoom-MinerU": mode}
            artifact, zip_ocr = wait_for_export(api, api.post("/api/jobs", **submitted, headers=chosen))
            source = json.loads(zip_ocr.read("source.json"))
            assert artifact["document_id"].startswith(mode + "-")
            assert source["document"]["parser"]["settings"]["ocr_mode"] == mode
            assert source["document"]["created_at"]
            text = source["blocks"][1]
            assert text["page"] == 1 and text["bbox"] == pytest.approx([.1, 110/600, .65, 140/600] if cloud else [.1, .18, .65, .23])
            assert text["source"]["regions"][0]["bbox"] == text["bbox"]
            assert "MinerU fixture" in zip_ocr.read("index.html").decode()
            assert any(b["content"]["latex"] == "E=mc^{2}" for b in source["blocks"])
            if cloud:
                figure = next(b for b in source["blocks"] if b["type"] == "figure")
                assert zip_ocr.read(figure["content"]["image_path"]).startswith(b"\x89PNG")
                assert any(b["anchor_id"] == figure["id"] for b in source["blocks"] if b["type"] == "caption")
                assert 'colspan="2"' in zip_ocr.read("index.html").decode()
            else:
                assert "$r$" in text["content"]["text"]
                image_formula = next(b for b in source["blocks"] if b["type"] == "equation_block" and b["content"]["image_path"])
                path = image_formula["content"]["image_path"]
                assert zip_ocr.read(path).startswith(b"\x89PNG") and path in zip_ocr.read("index.html").decode()
                table = next(b for b in source["blocks"] if b["type"] == "table")
                assert "<td>23</td>" in table["content"]["html"]
                assert any(b["anchor_id"] == table["id"] for b in source["blocks"] if b["type"] == "caption")
            count = len(calls)
            same, _ = wait_for_export(api, api.post("/api/jobs", **submitted, headers=chosen))
            assert same["id"] == artifact["id"] and len(calls) == count
            assert sum(method == "PUT" for method, _ in calls) == 1
            assert b"test-only" not in zip_ocr.read("source.json")
            def translate(directory, ids, *_):
                return {"results": {i: {"text": "译文结果"} for i in ids}}
            monkeypatch.setattr("app.services.translator.translate_batch", translate)
            assert api.put("/api/settings", json={"llm_base_url": "http://localhost:1/v1", "llm_model": "fixture"}).status_code == 200
            route = f"/api/documents/{artifact['document_id']}"
            prepared = api.post(route + "/edited-source", data={"revision": artifact["source_revision"]},
                                files={"upload": ("source.zip", zip_ocr.read("source.zip"), "application/zip")})
            assert prepared.status_code == 200, prepared.text
            response = api.post(route + "/translations", json={"source_revision": artifact["source_revision"]})
            assert response.status_code == 200, response.text
            assert response.json()["ocr_revision"] == artifact["source_revision"] and len(calls) == count


def test_old_file_parse_only_after_route_discovery(tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(pdf_bytes())
    result = tmp_path / "result.zip"
    def wire(request):
        if request.url.path.endswith("/v1/tasks"):
            return httpx.Response(404)
        if request.url.path.endswith("/openapi.json"):
            return httpx.Response(200, json={"paths": {"/file_parse": {"post": {}}}})
        assert request.url.path == "/file_parse" and request.method == "POST"
        assert b'name="files"; filename="document.pdf"' in request.content and pdf.read_bytes() in request.content
        return httpx.Response(200, content=bundle("document/document_middle.json", legacy()))
    with httpx.Client(transport=httpx.MockTransport(wire)) as mock:
        mc.fetch_bundle(pdf, result, mc.SELF_HOSTED, mc.Connection("http://localhost:8000", ""), page_count=1, report=lambda *_: None, client=mock)
    ir, _ = import_bundle(result, pdf, tmp_path, "local-fixture", [1], mc.SELF_HOSTED)
    assert any(b.type.value == "table" for b in ir.blocks)


def test_incomplete_bundle_and_expired_token_are_reported(tmp_path, monkeypatch):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(pdf_bytes())
    invalid = tmp_path / "missing.zip"
    invalid.write_bytes(bundle("layout.json", {"pdf_info": []}))
    with pytest.raises(ValueError, match="缺少第 1 页"):
        import_bundle(invalid, pdf, tmp_path, "sample", [1], mc.CLOUD)
    monkeypatch.setenv("PAPERBRIDGE_MINERU_CONFIG", str(tmp_path / "absent.json"))
    with pytest.raises(ValueError, match="官方 API Token"):
        mc.Connection.read(mc.CLOUD)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"code": "A0211"}))) as mock:
        with pytest.raises(ValueError, match="过期"):
            mc.fetch_bundle(pdf, invalid, mc.CLOUD, mc.Connection(mc.OFFICIAL_API, "example"), page_count=1, report=lambda *_: None, client=mock)
    with pytest.raises(ValueError, match="文件存储地址"):
        mc._resource("http://localhost/internal", mc.Connection(mc.OFFICIAL_API, "unused"), True)
