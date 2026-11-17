"""截断标题重识别修复测试(app/pipeline/heading_repair.py)。"""

from __future__ import annotations

from app.pipeline.heading_repair import (
    _clean_ocr_text,
    _column_ranges,
    find_truncated_headings,
    repair_headings,
)


def _h(bid, text, page=1, bbox=(0.1, 0.1, 0.3, 0.12)):
    return {"id": bid, "type": "heading", "page": page, "bbox": bbox, "content": {"text": text}}


def test_find_truncated_headings():
    blocks = [
        _h("a", "I. INTRODUCTION"),        # 完整,不匹配
        _h("b", "IV. E"),                   # 截断
        _h("c", "R"),                       # 截断
        _h("d", "A. Datasets"),             # 小节标题,不匹配
        {"id": "e", "type": "paragraph", "page": 1, "bbox": [0, 0, 0.1, 0.1], "content": {"text": "IV. E"}},  # 非 heading
        _h("f", "V. DISCUSSION AND FUTURE WORK"),  # 长文本,不匹配
        _h("g", "III. M", bbox=None),       # 无 bbox,跳过
    ]
    found = find_truncated_headings(blocks)
    assert [b["id"] for b in found] == ["b", "c"]


def test_column_ranges():
    blocks = [
        _h("a", "x", bbox=(0.08, 0.1, 0.45, 0.12)),
        _h("b", "y", bbox=(0.1, 0.2, 0.48, 0.22)),
        _h("c", "z", bbox=(0.55, 0.1, 0.9, 0.12)),
        {"id": "d", "type": "table", "page": 1, "bbox": [0.6, 0.3, 0.95, 0.4], "content": {}},
    ]
    left, right = _column_ranges(blocks)
    assert left == (0.08, 0.48)
    assert right == (0.55, 0.95)
    # 单栏只有一边时另一边用默认范围
    left2, right2 = _column_ranges([_h("a", "x", bbox=(0.6, 0.1, 0.9, 0.12))])
    assert left2 == (0.05, 0.49)
    assert right2 == (0.6, 0.9)


def test_clean_ocr_text():
    assert _clean_ocr_text("IV. EXPERIMENTS") == "IV. EXPERIMENTS"
    assert _clean_ocr_text("  IV.   EXPERIMENTS  \n") == "IV. EXPERIMENTS"
    assert _clean_ocr_text("OCR: II. RELATED WORK") == "II. RELATED WORK"
    assert _clean_ocr_text("REFERENCES.") == "REFERENCES"


def test_repair_headings_replaces_longer_text(monkeypatch, tmp_path):
    from PIL import Image

    blocks = [
        _h("a", "IV. E", page=7, bbox=(0.22, 0.2, 0.35, 0.21)),
        _h("b", "I. INTRODUCTION", page=1),
    ]
    Image.new("RGB", (100, 140), "white").save(tmp_path / "preview-007.png")
    monkeypatch.setattr("app.pipeline.heading_repair._ocr_crop", lambda *a, **k: "IV. EXPERIMENTS")
    n = repair_headings(blocks, tmp_path, server_url="http://mock/v1")
    assert n == 1
    assert blocks[0]["content"]["text"] == "IV. EXPERIMENTS"
    assert blocks[1]["content"]["text"] == "I. INTRODUCTION"  # 未截断不动


def test_repair_headings_keeps_original_when_not_longer(monkeypatch, tmp_path):
    from PIL import Image

    blocks = [_h("a", "IV. E", page=7, bbox=(0.22, 0.2, 0.35, 0.21))]
    Image.new("RGB", (100, 140), "white").save(tmp_path / "preview-007.png")
    # 模型返回更短的文本 → 保留原文
    monkeypatch.setattr("app.pipeline.heading_repair._ocr_crop", lambda *a, **k: "IV.")
    n = repair_headings(blocks, tmp_path, server_url="http://mock/v1")
    assert n == 0
    assert blocks[0]["content"]["text"] == "IV. E"


def test_repair_headings_skips_without_server():
    blocks = [_h("a", "IV. E")]
    assert repair_headings(blocks, __import__("pathlib").Path("."), server_url="") == 0


def test_repair_headings_skips_missing_preview(monkeypatch):
    blocks = [_h("a", "IV. E", page=7, bbox=(0.22, 0.2, 0.35, 0.21))]
    called = []

    def fake_ocr(*a, **k):
        called.append(1)
        return "IV. EXPERIMENTS"

    monkeypatch.setattr("app.pipeline.heading_repair._ocr_crop", fake_ocr)
    n = repair_headings(blocks, __import__("pathlib").Path("no-such-dir"), server_url="http://mock/v1")
    assert n == 0 and called == []
