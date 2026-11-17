"""模型层缓存（缓存分离）单元测试。

覆盖：
- json_safe 序列化（剔除图像调试字段、numpy 转换）
- restore_result 回放与原始解析一致性
- _raw_cache_valid 门禁（版本/哈希/缺页）
- _load/_save_page_ocr 往返
- 表格 300 DPI 重试缓存命中（不重复调模型）
"""

from __future__ import annotations

import json

import pytest

from app.parsing.paddleocr_vl import (
    MockOcrParser,
    PaddleOcrVlParser,
    json_safe,
)
from app.parsing.page_strategy import LayoutBox
from app.pipeline.orchestrator import Orchestrator, ConvertOptions
from app.pipeline.orchestrator import _table_retry_reason

RAW_FIXTURE = {
    "width": 2200,
    "height": 3400,
    "layout_det_res": {
        "input_img": "debug-image-bytes",
        "boxes": [
            {
                "label": "paragraph",
                "coordinate": [100, 100, 900, 200],
                "score": 0.99,
                "order": 0,
            },
            {
                "label": "table",
                "coordinate": [100, 300, 2000, 800],
                "score": 0.95,
                "order": None,
            },
        ],
    },
    "parsing_res_list": [
        "#################\n"
        "label:\tparagraph\n"
        "bbox:\t[100, 100, 900, 200]\n"
        "content:\tHello world\n"
        "#################",
        "#################\n"
        "label:\ttable\n"
        "bbox:\t[100, 300, 2000, 800]\n"
        "content:\t<table><tr><td>x</td></tr></table>\n"
        "#################",
    ],
    "imgs_in_doc": [],
}


def _make_parser() -> PaddleOcrVlParser:
    # 只调用纯 Python 解析，不触发模型加载
    return PaddleOcrVlParser()


# ---- json_safe ----------------------------------------------------------

def test_json_safe_skips_image_fields():
    out = json_safe(RAW_FIXTURE)
    assert "input_img" not in out["layout_det_res"]
    assert "imgs_in_doc" not in out
    assert out["layout_det_res"]["boxes"][0]["score"] == 0.99


def test_json_safe_skips_preprocessor_output_image():
    out = json_safe(
        {
            "doc_preprocessor_res": {"output_img": [[[255, 255, 255]]]},
            "width": 100,
            "height": 200,
        }
    )
    assert out == {
        "doc_preprocessor_res": {},
        "width": 100,
        "height": 200,
    }


def test_json_safe_numpy_scalars():
    np = pytest.importorskip("numpy")
    raw = {"score": np.float32(0.95), "n": np.int64(3), "arr": np.array([1, 2])}
    out = json_safe(raw)
    assert out["score"] == pytest.approx(0.95)
    assert out == {"score": out["score"], "n": 3, "arr": [1, 2]}
    json.dumps(out)  # 必须可序列化


def test_json_safe_roundtrip_restore():
    """序列化 → 反序列化 → restore_result 与原解析结果一致。"""
    parser = _make_parser()
    orig = parser.restore_result(RAW_FIXTURE, 1)
    dumped = json_safe(RAW_FIXTURE)
    # 序列化后缺失字段不影响解析（回放只需 boxes/parsing/宽高）
    replay = parser.restore_result(json.loads(json.dumps(dumped)), 1)
    assert len(replay.boxes) == len(orig.boxes)
    assert [b.text for b in replay.boxes] == [b.text for b in orig.boxes]
    assert [b.label for b in replay.boxes] == [b.label for b in orig.boxes]


def test_first_json_uses_compact_fields_without_materializing_json():
    class Result(dict):
        @property
        def json(self):
            raise AssertionError("不应访问会物化像素数组的 .json")

    item = Result(RAW_FIXTURE)
    parser = _make_parser()
    compact = parser._first_json([item])
    assert compact["width"] == 2200
    assert compact["height"] == 3400
    assert len(compact["layout_det_res"]["boxes"]) == 2
    assert len(compact["parsing_res_list"]) == 2
    assert "imgs_in_doc" not in compact


# ---- restore_result 回放 -------------------------------------------------

def test_restore_result_parses_raw():
    parser = _make_parser()
    res = parser.restore_result(RAW_FIXTURE, 7)
    assert res.page == 7
    assert not res.error
    labels = [b.label for b in res.boxes]
    assert "paragraph" in labels and "table" in labels
    # 表格文本（HTML）保留；paragraph 文本正确
    table = next(b for b in res.boxes if b.label == "table")
    assert table.text.startswith("<table>")
    para = next(b for b in res.boxes if b.label == "paragraph")
    assert para.text == "Hello world"


# ---- _raw_cache_valid 门禁 ----------------------------------------------

def _write_raw_cache(doc_dir, pages=(1,), pdf_hash="h1", vlm_version="mock-0",
                     render_dpi=220):
    (doc_dir / "raw").mkdir(parents=True, exist_ok=True)
    meta = {
        "schema": 2,
        "pdf_hash": pdf_hash,
        "vlm_version": vlm_version,
        "render_dpi": render_dpi,
        "pages": list(pages),
    }
    (doc_dir / "raw" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    for p in pages:
        (doc_dir / "raw" / f"page_{p:03d}.json").write_text(
            json.dumps({"schema": 2, "pdf_hash": pdf_hash, "vlm_version": vlm_version, "raw": {}}),
            encoding="utf-8",
        )


def test_raw_cache_valid_hit(tmp_path):
    doc_dir = tmp_path / "d1"
    _write_raw_cache(doc_dir, pages=(1, 2, 3))
    orch = Orchestrator(workspace=tmp_path)
    opts = ConvertOptions(render_dpi=220)
    assert orch._raw_cache_valid(doc_dir, "h1", [1, 2, 3], MockOcrParser(), opts)


def test_raw_cache_invalid_version(tmp_path):
    doc_dir = tmp_path / "d1"
    _write_raw_cache(doc_dir, vlm_version="old-1")
    orch = Orchestrator(workspace=tmp_path)
    opts = ConvertOptions(render_dpi=220)
    assert not orch._raw_cache_valid(doc_dir, "h1", [1], MockOcrParser(), opts)


def test_raw_cache_invalid_pdf_hash(tmp_path):
    doc_dir = tmp_path / "d1"
    _write_raw_cache(doc_dir, pdf_hash="other")
    orch = Orchestrator(workspace=tmp_path)
    opts = ConvertOptions(render_dpi=220)
    assert not orch._raw_cache_valid(doc_dir, "h1", [1], MockOcrParser(), opts)


def test_raw_cache_invalid_missing_page(tmp_path):
    doc_dir = tmp_path / "d1"
    _write_raw_cache(doc_dir, pages=(1, 2))
    orch = Orchestrator(workspace=tmp_path)
    opts = ConvertOptions(render_dpi=220)
    assert not orch._raw_cache_valid(doc_dir, "h1", [1, 2, 3], MockOcrParser(), opts)


def test_raw_cache_invalid_no_meta(tmp_path):
    doc_dir = tmp_path / "d1"
    (doc_dir / "raw").mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(workspace=tmp_path)
    opts = ConvertOptions(render_dpi=220)
    assert not orch._raw_cache_valid(doc_dir, "h1", [1], MockOcrParser(), opts)


# ---- 页面级缓存往返 ------------------------------------------------------

def test_page_ocr_roundtrip(tmp_path):
    """_save_page_ocr → _load_page_ocr 回放，识别器不调用模型。"""
    parser = _make_parser()
    doc_dir = tmp_path / "d1"
    (doc_dir / "raw").mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(workspace=tmp_path)

    res = parser.restore_result(RAW_FIXTURE, 3)
    # 生产代码中 doc_dir.name == pdf_hash（documents/{pdf_hash}）
    orch._save_page_ocr(doc_dir, 3, res, parser, doc_dir.name)

    loaded = orch._load_page_ocr(doc_dir, 3, parser)
    assert loaded is not None
    assert loaded.page == 3
    assert [b.text for b in loaded.boxes] == [b.text for b in res.boxes]

    # 文件缺失 → None
    assert orch._load_page_ocr(doc_dir, 99, parser) is None


def test_page_ocr_roundtrip_wrong_hash(tmp_path):
    """缓存文件内 pdf_hash 与文档目录不符 → 视为无效（防御缓存混入）。"""
    parser = _make_parser()
    doc_dir = tmp_path / "d1"
    (doc_dir / "raw").mkdir(parents=True, exist_ok=True)
    (doc_dir / "raw" / "page_003.json").write_text(
        json.dumps({"schema": 2, "pdf_hash": "other", "vlm_version": "mock-0", "raw": RAW_FIXTURE}),
        encoding="utf-8",
    )
    orch = Orchestrator(workspace=tmp_path)
    assert orch._load_page_ocr(doc_dir, 3, parser) is None


# ---- 表格重试缓存 -------------------------------------------------------

class _CountingParser(MockOcrParser):
    """可计数的假识别器：表格重试命中时不应再调用 recognize_page。"""

    def __init__(self) -> None:
        super().__init__(
            [
                LayoutBox(
                    label="table",
                    bbox=[0.1, 0.1, 0.9, 0.5],
                    text="<table><tr><td>a</td></tr></table>",
                )
            ]
        )
        self.calls = 0

    def recognize_page(self, image, page_no):
        self.calls += 1
        return super().recognize_page(image, page_no)


class _FakeRenderer:
    def __init__(self) -> None:
        self.crops = 0

    def crop_region(self, page_no, bbox, dpi=220):
        self.crops += 1
        return b"fake-crop"


def test_table_retry_cache_hit(tmp_path):
    doc_dir = tmp_path / "d1"
    orch = Orchestrator(workspace=tmp_path)
    recognizer = _CountingParser()
    renderer = _FakeRenderer()
    box = LayoutBox(label="table", bbox=[0.1, 0.1, 0.9, 0.5])

    html1 = orch._retry_table_ocr(7, box, renderer, recognizer, doc_dir)
    assert html1.startswith("<table>")
    assert recognizer.calls == 1 and renderer.crops == 1

    # 第二次：命中缓存，不调模型、不裁剪
    html2 = orch._retry_table_ocr(7, box, renderer, recognizer, doc_dir)
    assert html2 == html1
    assert recognizer.calls == 1 and renderer.crops == 1

    # force（use_raw_cache=False）：跳过缓存读取，重新识别
    html3 = orch._retry_table_ocr(7, box, renderer, recognizer, doc_dir,
                                  use_raw_cache=False)
    assert html3 == html1
    assert recognizer.calls == 2 and renderer.crops == 2


def test_table_retry_quality_gate():
    good = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    assert _table_retry_reason(good, 0.9) == ""
    assert _table_retry_reason("", 0.9) == "首次结果为空"
    assert _table_retry_reason("plain text", 0.9) == "未生成 table 结构"
    assert _table_retry_reason("<table><tr><td>x</td></tr></table>", 0.9) == "行列结构不完整"
    assert _table_retry_reason(good, 0.5).startswith("低置信度")


def test_official_vllm_options_are_forwarded(monkeypatch):
    """官方 FastDeploy/vLLM 客户端配置必须原样传给 PaddleOCRVL。"""
    import sys
    import types

    # The pipeline is a test double; importing PaddleOCR here would load its
    # optional runtime and can trigger online model-source checks.
    paddleocr = types.ModuleType("paddleocr")
    paddleocr.__version__ = "test"
    paddlex = types.ModuleType("paddlex")
    paddlex.__version__ = "test"
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    monkeypatch.setitem(sys.modules, "paddlex", paddlex)

    created = []

    class FakePipeline:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(paddleocr, "PaddleOCRVL", FakePipeline, raising=False)
    parser = PaddleOcrVlParser(
        device="gpu",
        vl_backend="vllm",
        vl_server_url="http://127.0.0.1:8118",
        vl_max_concurrency=4,
        use_queues=True,
        enable_hpi=True,
        use_tensorrt=True,
        precision="fp16",
    )

    parser._load()

    assert created == [
        {
            "vl_rec_backend": "vllm",
            "vl_rec_server_url": "http://127.0.0.1:8118",
            "vl_rec_max_concurrency": 4,
            "use_queues": True,
            "enable_hpi": True,
            "use_tensorrt": True,
            "precision": "fp16",
        }
    ]
    assert "backend=vllm" in parser.cache_signature()


def test_official_queue_batch_returns_one_result_per_page():
    """多页输入必须作为一次官方 predict 调用，并保留页号映射。"""
    calls = []

    class FakePipeline:
        def predict(self, paths, **kwargs):
            calls.append((list(paths), kwargs))
            return [RAW_FIXTURE, RAW_FIXTURE]

    parser = PaddleOcrVlParser(use_queues=True)
    parser._ocr = FakePipeline()
    results = parser.recognize_pages([b"page-one", b"page-two"], [4, 9])

    assert [result.page for result in results] == [4, 9]
    assert all(result.boxes for result in results)
    assert calls[0][1] == {"use_queues": True}


def test_runtime_recognizer_is_reused(monkeypatch, tmp_path):
    import app.parsing.paddleocr_vl as paddle_module

    created = []

    class FakeParser(MockOcrParser):
        def __init__(self, device="auto", *, vl_backend=None, use_queues=None):
            super().__init__()
            self.device = device
            created.append(self)

    monkeypatch.setattr(paddle_module, "PaddleOcrVlParser", FakeParser)
    orch = Orchestrator(workspace=tmp_path)
    opts = ConvertOptions(ocr_mode="auto", ocr_device="gpu")
    first = orch._resolve_recognizer(opts)
    second = orch._resolve_recognizer(opts)
    assert first is second
    assert len(created) == 1
