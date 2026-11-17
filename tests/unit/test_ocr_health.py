import json

import pytest

from scripts.ocr_health import probe_vlm
from app.parsing.paddleocr_vl import PaddleOcrVlParser


def test_probe_requires_readable_ocr_not_only_http_success(monkeypatch):
    from io import BytesIO

    calls = []
    def response(request, timeout):
        calls.append(request)
        body = {"data": [{"id": "ocr-model"}]} if isinstance(request, str) else {
            "choices": [{"message": {"content": "共" * 64}}]
        }
        return BytesIO(json.dumps(body).encode())

    monkeypatch.setattr("scripts.ocr_health.urllib.request.urlopen", response)
    with pytest.raises(RuntimeError, match="实际识别内容异常"):
        probe_vlm()
    assert json.loads(calls[1].data)["max_completion_tokens"] == 64


def test_probe_accepts_readable_output(monkeypatch):
    from io import BytesIO

    def response(request, timeout):
        return BytesIO(json.dumps({"data": [{"id": "ocr-model"}]} if isinstance(request, str) else {
            "choices": [{"message": {"content": "PaperLoom OCR ready 12345\n"}}]
        }).encode())

    monkeypatch.setattr("scripts.ocr_health.urllib.request.urlopen", response)
    assert "12345" in probe_vlm()


def test_repetition_guard_rejects_garbage_without_truncating_real_tables():
    raw = lambda text: {"parsing_res_list": [{"block_content": text}]}
    with pytest.raises(RuntimeError, match="异常重复内容"):
        PaddleOcrVlParser.validate_output(raw("乱码" + "共" * 600))
    with pytest.raises(RuntimeError, match="异常重复内容"):
        PaddleOcrVlParser.validate_output(raw("\ufffdab" * 120))
    PaddleOcrVlParser.validate_output(raw("<table>" + "<tr><td>00000000</td><td>0.025</td></tr>" * 100 + "</table>"))
    PaddleOcrVlParser.validate_output(raw("This paragraph describes canopy phenology and forest growth. " * 20))


def test_page_progress_is_available_before_document_finishes(tmp_path):
    events = []
    class Page:
        def save_to_json(self, save_path):
            from pathlib import Path
            (Path(save_path) / "page.json").write_text(json.dumps({"parsing_res_list": []}))
        def save_to_markdown(self, save_path):
            from pathlib import Path
            (Path(save_path) / "page.md").write_text("## A real page")

    class Pipeline:
        def predict(self, *args, **kwargs):
            raise AssertionError("eager prediction prevents early progress")
        def predict_iter(self, *args, **kwargs):
            yield Page()
            assert events == [1], "first page must be reported before waiting for later pages"
            yield Page()

    parser = PaddleOcrVlParser()
    parser._ocr = Pipeline()
    result = parser.recognize_document("source.pdf", tmp_path, progress=events.append)
    assert len(result) == 2 and events == [1, 2]
