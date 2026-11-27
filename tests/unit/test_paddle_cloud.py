import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.parsing.paddle_cloud import JOBS, PaddleCloudRecognizer, _resource
from app.server import create_app


def test_cloud_page_mapping_and_asset_download_do_not_forward_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("app.parsing.paddle_cloud.time.sleep", lambda _: None)
    counts = {}
    result_url = "https://fixture.bcebos.com/result.jsonl?signature=fake"
    image_url = "https://fixture.bcebos.com/figure.png"
    block = {"block_label": "formula", "block_content": "x^2", "block_bbox": [1,2,3,4]}
    row = {"errorCode":0, "result":{"layoutParsingResults":[{
        "prunedResult":{"width":100,"height":200,"parsing_res_list":[block]},
        "markdown":{"text":"$$x^2$$","images":{"imgs/figure.png":image_url}},
    }]}}
    def request(req):
        url = str(req.url)
        counts[url] = counts.get(url, 0) + 1
        if url == JOBS + "/job-1" and counts[url] == 1:
            return httpx.Response(502)
        if url == image_url and counts[url] == 1:
            raise httpx.ReadError("connection reset")
        if url == JOBS:
            assert req.headers["authorization"] == "Bearer fixture-token"
            assert b'name="pageRanges"\r\n\r\n7' in req.read()
            return httpx.Response(200,json={"code":0,"data":{"jobId":"job-1"}})
        if url == JOBS + "/job-1":
            assert req.headers["authorization"] == "Bearer fixture-token"
            return httpx.Response(200,json={"code":0,"data":{"state":"done","resultUrl":{"jsonUrl":result_url}}})
        assert "authorization" not in req.headers
        assert url in {result_url,image_url}
        return httpx.Response(200,content=json.dumps(row).encode() if url == result_url else b"image data")
    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        parsed = PaddleCloudRecognizer()._parse(client,"fixture-token",io.BytesIO(b"fake PDF"),tmp_path,lambda _:None,[7])
        with pytest.raises(ValueError, match="资源地址"):
            _resource(client,"https://example.com/result.json",100)
    assert parsed[0].page == 7 and parsed[0].raw["res"]["parsing_res_list"] == [block]
    assert counts == {JOBS: 1, JOBS + "/job-1": 2, result_url: 1, image_url: 2}
    assert (tmp_path/"page-007/imgs/figure.png").read_bytes() == b"image data"


def test_official_local_ocr_resumes_unfinished_pages_after_502(tmp_path, monkeypatch):
    import pymupdf
    from app.parsing.paddleocr_vl import PaddleOcrVlParser

    pdf = tmp_path / "three-pages.pdf"
    with pymupdf.open() as doc:
        for page in range(3):
            doc.new_page().insert_text((50, 50), str(page + 1))
        doc.save(pdf)
    inputs = []

    class Model:
        def predict_iter(self, path):
            with pymupdf.open(path) as doc:
                pages = [page.get_text().strip() for page in doc]
            inputs.append(pages)
            for number in pages:
                if len(inputs) == 1 and number == "2":
                    raise RuntimeError("Exception from the vlm worker: Error code: 502")
                yield number

    parser = PaddleOcrVlParser.__new__(PaddleOcrVlParser)
    parser._ocr = Model()
    monkeypatch.setattr("app.parsing.paddleocr_vl.time.sleep", lambda _: None)
    assert list(parser._document_pages(pdf, {})) == [(1, "1", 0), (2, "2", 1), (3, "3", 1)]
    assert inputs == [["1", "2", "3"], ["2", "3"]]


def test_cloud_server_rejects_upload_without_plugin_header(tmp_path,monkeypatch):
    monkeypatch.setenv("PAPERBRIDGE_OFFICIAL_ACCELERATOR","aistudio-vl16")
    with TestClient(create_app(Settings(workspace=tmp_path))) as client:
        assert client.get("/api/zotero/status").json()["cloud_ocr"]
        assert client.post("/api/jobs",files={"upload":("test.pdf",b"invalid","application/pdf")}).status_code == 403
        assert client.post("/api/browser/upload",files={"upload":("test.pdf",b"invalid","application/pdf")}).status_code == 403
