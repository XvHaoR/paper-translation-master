"""集成测试：完整转换流水线 + 本地服务 API。"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from app.config import get_settings
from app.pipeline.orchestrator import ConvertOptions, Orchestrator
from app.pipeline.preflight import run_preflight
from app.server import create_app


def test_preflight_detects_native(native_pdf):
    pre = run_preflight(str(native_pdf), ocr_mode="auto")
    assert pre.ok
    assert pre.page_count == 3
    assert pre.document_type == "native"
    assert all(p.strategy == "native" for p in pre.pages)


def test_preflight_detects_scanned(scanned_pdf):
    pre = run_preflight(str(scanned_pdf), ocr_mode="auto")
    assert pre.ok
    assert pre.document_type == "scanned"
    assert all(p.strategy == "ocr" for p in pre.pages)


def test_convert_native_end_to_end(native_pdf, workspace):
    orch = Orchestrator(workspace)
    result = orch.convert(native_pdf, ConvertOptions(ocr_mode="native", force=True))
    assert result.ok
    assert result.status in ("ready", "needs_review")
    assert result.ir is not None
    assert len(result.ir.blocks) > 10

    doc_dir = result.doc_dir
    assert (doc_dir / "index.html").is_file()
    assert (doc_dir / "document.json").is_file()
    assert (doc_dir / "original.pdf").is_file()
    assert (doc_dir / "assets" / "reader.css").is_file()
    assert (doc_dir / "assets" / "reader.js").is_file()
    assert (doc_dir / "assets" / "page-previews").is_dir()

    # 标题与作者
    types = [b.type.value for b in result.ir.blocks]
    assert "document_title" in types
    assert "authors" in types
    assert "figure" in types
    # 双栏阅读顺序：Introduction 应在 Method 之前
    texts = [b.content.text for b in result.ir.blocks if b.content.text]
    assert texts.index("1. Introduction") < texts.index("2. Method")


def test_convert_caches(native_pdf, workspace):
    orch = Orchestrator(workspace)
    r1 = orch.convert(native_pdf, ConvertOptions(ocr_mode="auto", force=False))
    r2 = orch.convert(native_pdf, ConvertOptions(ocr_mode="auto", force=False))
    assert r1.ok and r2.ok
    assert r1.document_id == r2.document_id
    assert any("缓存" in w for w in r2.warnings)


def test_force_rerun_reuses_readonly_original(native_pdf, workspace):
    orch = Orchestrator(workspace)
    first = orch.convert(native_pdf, ConvertOptions(ocr_mode="native", force=True))
    original = first.doc_dir / "original.pdf"
    original.chmod(stat.S_IREAD)
    try:
        second = orch.convert(native_pdf, ConvertOptions(ocr_mode="native", force=True))
        assert second.ok
    finally:
        original.chmod(stat.S_IWRITE)


def test_convert_mock_ocr_blocks(native_pdf, workspace):
    """用假 OCR 后端走 OCR 路径，验证版面框→IR 的映射逻辑。"""
    from app.parsing.paddleocr_vl import MockOcrParser
    from app.parsing.page_strategy import LayoutBox

    mock = MockOcrParser(
        boxes=[
            LayoutBox(label="paragraph", bbox=[0.05, 0.05, 0.95, 0.15], order=0,
                      score=0.9, text="OCR paragraph text here."),
            LayoutBox(label="equation", bbox=[0.1, 0.2, 0.9, 0.3], order=1,
                      score=0.85, text=r"$$E = mc^2$$"),
            LayoutBox(label="table", bbox=[0.1, 0.35, 0.9, 0.6], order=2,
                      score=0.7, text="| A | B |\n|---|---|\n| 1 | 2 |"),
        ]
    )
    orch = Orchestrator(workspace, recognizer=mock)
    result = orch.convert(native_pdf, ConvertOptions(ocr_mode="full", force=True))
    assert result.ok
    types = {b.type.value for b in result.ir.blocks}
    assert "equation_block" in types
    assert "table" in types
    eq = next(b for b in result.ir.blocks if b.type.value == "equation_block")
    assert "E = mc^2" in (eq.content.latex or "")
    assert eq.source.crop  # 公式必须保留原图
    assert (doc_dir_for(result) / eq.source.crop).is_file()

    # --force skips old cache reads, but must still persist fresh model output.
    raw_dir = doc_dir_for(result) / "raw"
    page_cache = raw_dir / "page_001.json"
    assert page_cache.is_file()
    assert json.loads(page_cache.read_text(encoding="utf-8"))["schema"] == 2


def test_convert_official_keeps_official_markdown_structure_and_images(
    native_pdf, workspace
):
    """The official route must not reclassify headings or recrop images."""
    from app.parsing.paddleocr_vl import OfficialPageResult

    class OfficialMock:
        def is_available(self):
            return True

        def recognize_document(self, _pdf_path, output_dir):
            page_dir = Path(output_dir) / "page-001"
            image_path = page_dir / "imgs" / "workflow.jpg"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"official-image")
            return [
                OfficialPageResult(
                    page=1,
                    output_dir=page_dir,
                    markdown=(
                        "# Paper title\n\n"
                        "### 1. Introduction\n\n"
                        '<img src="imgs/workflow.jpg" alt="workflow">\n'
                    ),
                    raw={
                        "res": {
                            "width": 1000,
                            "height": 1400,
                            "parsing_res_list": [
                                {
                                    "block_label": "doc_title",
                                    "block_content": "Paper title",
                                    "block_bbox": [10, 10, 900, 80],
                                },
                                {
                                    "block_label": "paragraph_title",
                                    "block_content": "1. Introduction",
                                    "block_bbox": [10, 100, 700, 140],
                                },
                                {
                                    "block_label": "image",
                                    "block_content": "",
                                    "block_bbox": [10, 200, 700, 700],
                                },
                            ],
                        }
                    },
                )
            ]

    result = Orchestrator(workspace, recognizer=OfficialMock()).convert(
        native_pdf, ConvertOptions(ocr_mode="official", force=True)
    )

    assert result.ok
    assert result.ir is not None
    assert any(
        b.type.value == "heading" and b.content.text == "1. Introduction"
        for b in result.ir.blocks
    )
    html = (result.doc_dir / "index.html").read_text(encoding="utf-8")
    assert "<h3>1. Introduction</h3>" in html
    assert 'src="assets/official/page-001/imgs/workflow.jpg"' in html
    assert 'id="tb-toc"' in html
    assert 'src="assets/reader.js?v=17"' in html
    assert 'src="assets/table_excel.js"' in html
    assert 'href="#p001-b001"' in html
    assert (
        result.doc_dir / "assets" / "official" / "page-001" / "imgs" / "workflow.jpg"
    ).is_file()


def test_official_markdown_preserves_latex_before_markdown_parsing(native_pdf, workspace):
    from app.parsing.paddleocr_vl import OfficialPageResult

    class OfficialMathMock:
        def is_available(self):
            return True

        def recognize_document(self, _pdf_path, output_dir):
            page_dir = Path(output_dir) / "page-001"
            page_dir.mkdir(parents=True, exist_ok=True)
            return [
                OfficialPageResult(
                    page=1,
                    output_dir=page_dir,
                    markdown=(
                        "# Formula paper\n\n"
                        "$$ \\\\mathrm{X}_{\\\\mathrm{i}}^{\\\\prime}"
                        "=\\\\frac{a_{min}}{b_{max}} $$\n\n"
                        "Inline $r^2$ stays readable."
                    ),
                    raw={
                        "res": {
                            "width": 1000,
                            "height": 1400,
                            "parsing_res_list": [
                                {"block_label": "doc_title", "block_content": "Formula paper"},
                                {"block_label": "formula", "block_content": r"\\\\frac{a}{b}"},
                            ],
                        }
                    },
                )
            ]

    result = Orchestrator(workspace, recognizer=OfficialMathMock()).convert(
        native_pdf, ConvertOptions(ocr_mode="official", force=True)
    )
    html = (result.doc_dir / "index.html").read_text(encoding="utf-8")
    assert 'class="equation-wrap math-display notranslate"' in html
    assert '"kind": "equation"' in html
    assert "\\\\mathrm{X}_{\\\\mathrm{i}}" in html
    assert '<em>{\\\\mathrm' not in html
    assert 'class="math-inline notranslate"' in html


def test_official_mode_prefers_verified_fastdeploy_vl16(workspace, monkeypatch):
    monkeypatch.setenv("PAPERBRIDGE_OFFICIAL_ACCELERATOR", "fastdeploy-vl16")

    class FastDeployMock:
        def __init__(self, *, device):
            self.device = device

        def is_available(self):
            return True

    import app.parsing.paddleocr_vl as parser_module

    monkeypatch.setattr(parser_module, "PaddleOcrVlParser", FastDeployMock)
    parser = Orchestrator(workspace)._resolve_recognizer(
        ConvertOptions(ocr_mode="official")
    )
    assert parser.device == "auto"


def test_convert_uses_official_batch_queue(native_pdf, workspace):
    """启用官方队列时，强制冷解析应把所有 OCR 页一次提交。"""
    from app.parsing.paddleocr_vl import MockOcrParser
    from app.parsing.page_strategy import LayoutBox

    class BatchMock(MockOcrParser):
        supports_batch = True

        def __init__(self):
            super().__init__(
                [
                    LayoutBox(
                        label="paragraph",
                        bbox=[0.05, 0.05, 0.95, 0.15],
                        text="Batch OCR text.",
                    )
                ]
            )
            self.batch_calls = []

        def recognize_pages(self, images, page_nos):
            self.batch_calls.append((len(images), list(page_nos)))
            return [
                MockOcrParser.recognize_page(self, image, page_no)
                for image, page_no in zip(images, page_nos)
            ]

        def recognize_page(self, image, page_no):
            raise AssertionError("官方队列模式不应回退为逐页模型调用")

    mock = BatchMock()
    result = Orchestrator(workspace, recognizer=mock).convert(
        native_pdf, ConvertOptions(ocr_mode="full", force=True)
    )

    assert result.ok
    assert mock.batch_calls == [(3, [1, 2, 3])]


def doc_dir_for(result):
    return result.doc_dir


def test_server_documents_api(native_pdf, workspace):
    from fastapi.testclient import TestClient

    orch = Orchestrator(workspace)
    orch.convert(native_pdf, ConvertOptions(ocr_mode="native", force=True))

    settings = get_settings()
    client = TestClient(create_app(settings))

    r = client.get("/api/documents")
    assert r.status_code == 200
    docs = r.json()
    assert len(docs) == 1
    doc = docs[0]
    assert doc["has_html"]

    # 阅读页
    r = client.get(f"/documents/{doc['id']}/")
    assert r.status_code == 200
    assert "PaperLoom" in r.text

    # 资源防穿越
    r = client.get(f"/documents/{doc['id']}/../../config.py")
    assert r.status_code == 404

    # 块接口
    r = client.get(f"/api/documents/{doc['id']}/blocks/p001-b000")
    assert r.status_code in (200, 404)


def test_server_export_markdown_api(native_pdf, workspace):
    from fastapi.testclient import TestClient

    orch = Orchestrator(workspace)
    orch.convert(native_pdf, ConvertOptions(ocr_mode="native", force=True))

    settings = get_settings()
    client = TestClient(create_app(settings))

    doc_id = client.get("/api/documents").json()[0]["id"]
    r = client.get(f"/documents/{doc_id}/export.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert ".md" in r.headers.get("content-disposition", "")
    assert r.text.strip()

    # 不存在的文档 404
    assert client.get("/documents/nonexistent/export.md").status_code == 404


def test_server_settings_and_translate_api(native_pdf, workspace):
    from fastapi.testclient import TestClient

    orch = Orchestrator(workspace)
    orch.convert(native_pdf, ConvertOptions(ocr_mode="native", force=True))

    settings = get_settings()
    client = TestClient(create_app(settings))

    # 设置读写
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert "llm_base_url" in r.json()

    r = client.put("/api/settings", json={"llm_base_url": "https://mock/v1", "llm_model": "m"})
    assert r.status_code == 200
    assert r.json()["llm_model"] == "m"

    # 非法 URL 拒绝
    r = client.put("/api/settings", json={"llm_base_url": "not-a-url"})
    assert r.status_code == 400

    # 未配置(清空)时翻译端点返回 400 提示
    client.put("/api/settings", json={"llm_base_url": "", "llm_model": ""})
    doc_id = client.get("/api/documents").json()[0]["id"]
    r = client.post(f"/api/documents/{doc_id}/translate", json={"block_ids": ["x"]})
    assert r.status_code == 400
    assert "翻译未配置" in r.json()["detail"]

    # 文档不存在 404
    r = client.post("/api/documents/nonexistent/translate", json={"block_ids": ["x"]})
    assert r.status_code == 404


def test_server_jobs_api(native_pdf, workspace):
    from fastapi.testclient import TestClient

    settings = get_settings()
    client = TestClient(create_app(settings))

    with open(native_pdf, "rb") as f:
        r = client.post(
            "/api/jobs",
            files={"upload": ("paper.pdf", f, "application/pdf")},
            data={"ocr_mode": "native"},  # 快速路径：任务 API 流程验证
        )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    # 轮询直到终态
    import time

    state = "queued"
    for _ in range(50):
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        state = r.json()["state"]
        if state in ("ready", "needs_review", "failed"):
            break
        time.sleep(0.1)
    assert state in ("ready", "needs_review", "failed")


def test_server_rejects_non_pdf(workspace):
    from fastapi.testclient import TestClient

    settings = get_settings()
    client = TestClient(create_app(settings))
    r = client.post(
        "/api/jobs",
        files={"upload": ("evil.txt", b"not a pdf", "text/plain")},
    )
    assert r.status_code == 400


def test_pairing_flow(workspace):
    from fastapi.testclient import TestClient

    settings = get_settings()
    client = TestClient(create_app(settings))

    # 生成配对码
    r = client.get("/api/browser/pair-code")
    assert r.status_code == 200
    code = r.json()["pair_code"]

    # 兑换
    r = client.post("/api/browser/pair", json={"pair_code": code})
    assert r.status_code == 200
    token = r.json()["token"]

    # 错误令牌被拒
    r = client.get("/api/browser/jobs/xxxx")
    assert r.status_code == 401

    # 正确令牌访问
    r = client.get("/api/browser/jobs/xxxx", headers={"X-PaperBridge-Token": token})
    assert r.status_code == 404  # 通过鉴权，任务不存在

    # 过期/无效配对码
    r = client.post("/api/browser/pair", json={"pair_code": "000000"})
    assert r.status_code == 401


def test_patch_block_rerender(native_pdf, workspace):
    from fastapi.testclient import TestClient

    orch = Orchestrator(workspace)
    result = orch.convert(native_pdf, ConvertOptions(ocr_mode="native", force=True))
    block = next(b for b in result.ir.blocks if b.content.text)
    settings = get_settings()
    client = TestClient(create_app(settings))

    r = client.patch(
        f"/api/documents/{result.document_id}/blocks/{block.id}",
        json={"text": "REPLACED TEXT"},
    )
    assert r.status_code == 200
    doc = client.get(f"/api/documents/{result.document_id}/blocks/{block.id}").json()
    assert doc["content"]["text"] == "REPLACED TEXT"
    html = client.get(f"/documents/{result.document_id}/").text
    assert "REPLACED TEXT" in html
