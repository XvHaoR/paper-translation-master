"""阅读顺序决策单元测试（READING_ORDER_TECHNICAL_DESIGN.md §10.1）。

覆盖 10 个必测场景 + 回归 fixture（Medlyn 第 1/13 页脱敏框）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.parsing.page_strategy import (
    LayoutBox,
    ReadingOrderDiagnostics,
    dedupe_containers,
    model_order_valid,
    resolve_reading_order,
    sort_boxes_reading_order,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "reading_order_medlyn.json"


def _box(label, bbox, order=None, text="t"):
    return LayoutBox(label=label, bbox=bbox, order=order, text=text)


def _load_fixture() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pages = {}
    for pg, info in data["pages"].items():
        pages[pg] = [
            LayoutBox(
                label=b["label"],
                bbox=b["bbox"],
                score=b.get("score", 1.0),
                order=b["order"],
            )
            for b in info["boxes"]
        ]
    return pages


# ---- 场景 1/2：order 语义 ----

def test_order_zero_is_valid():
    """order=0 是合法顺序，不得误判为缺失。"""
    assert _box("text", [0.1, 0.1, 0.3, 0.2], order=0).order == 0


def test_order_none_kept():
    """order=None 必须保留为 None，不得变成 0。"""
    b = _box("text", [0.1, 0.1, 0.3, 0.2])
    assert b.order is None


# ---- 场景 3：模型顺序完整时不重排 ----

def test_model_order_preserved():
    """模型顺序有效时不得调用几何重排：输出与模型 order 一致。"""
    boxes = [
        _box("text", [0.5, 0.1, 0.8, 0.2], order=0, text="right-top"),
        _box("text", [0.1, 0.1, 0.4, 0.2], order=1, text="left-top"),
        _box("text", [0.1, 0.5, 0.4, 0.6], order=2, text="left-bottom"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert diag.method == "model"
    assert [b.text for b in ordered] == ["right-top", "left-top", "left-bottom"]


def test_model_order_zero_not_missing():
    """order=0 的框排在最前，不当作无顺序元素追加页尾。"""
    boxes = [
        _box("text", [0.1, 0.5, 0.4, 0.6], order=1, text="second"),
        _box("text", [0.5, 0.1, 0.8, 0.2], order=0, text="first"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert [b.text for b in ordered] == ["first", "second"]
    assert diag.method == "model"


# ---- 场景 4：行内公式不破坏列结构（几何回退路径） ----

def test_inline_formula_not_bridging_columns():
    """行内公式位于两栏中间时不得把两栏桥接成一栏。"""
    boxes = [
        _box("text", [0.05, 0.1, 0.40, 0.2], order=None, text="left-1"),
        _box("text", [0.60, 0.1, 0.95, 0.2], order=None, text="right-1"),
        _box("inline_formula", [0.45, 0.11, 0.55, 0.19], order=None),
        _box("text", [0.05, 0.4, 0.40, 0.5], order=None, text="left-2"),
        _box("text", [0.60, 0.4, 0.95, 0.5], order=None, text="right-2"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert diag.method == "geometric_fallback"
    assert diag.detected_columns == 2
    # 正文序列保持左栏→右栏；inline_formula 作为辅助元素追加页尾
    texts = [b.text for b in ordered if b.text and b.label != "inline_formula"]
    assert texts == ["left-1", "left-2", "right-1", "right-2"]
    assert ordered[-1].label == "inline_formula"


# ---- 场景 5：父级结构框去重 ----

def test_reference_container_deduped():
    """reference 父框含 ≥2 个 reference_content 子框时只保留子框。"""
    boxes = [
        _box("reference", [0.05, 0.1, 0.95, 0.9], order=0, text="PARENT TEXT"),
        _box("reference_content", [0.06, 0.12, 0.94, 0.3], order=1, text="ref 1"),
        _box("reference_content", [0.06, 0.32, 0.94, 0.5], order=2, text="ref 2"),
        _box("reference_content", [0.06, 0.52, 0.94, 0.7], order=3, text="ref 3"),
    ]
    diag = ReadingOrderDiagnostics()
    kept = dedupe_containers(boxes, diag)
    assert diag.removed_containers == 1
    assert all(b.label != "reference" for b in kept)
    assert len([b for b in kept if b.label == "reference_content"]) == 3


def test_single_reference_content_kept():
    """父框只含 1 个子框时不删除（可能是真实引用条目）。"""
    boxes = [
        _box("reference", [0.05, 0.1, 0.95, 0.3], order=0),
        _box("reference_content", [0.06, 0.12, 0.94, 0.28], order=1),
    ]
    diag = ReadingOrderDiagnostics()
    kept = dedupe_containers(boxes, diag)
    assert diag.removed_containers == 0
    assert len(kept) == 2


# ---- 场景 6：无顺序脚注追加页尾 ----

def test_unordered_footnote_appended_tail():
    """无顺序脚注应出现在正文之后。"""
    boxes = [
        _box("text", [0.1, 0.1, 0.9, 0.2], order=0, text="body"),
        _box("footnote", [0.1, 0.8, 0.9, 0.95], order=None, text="footnote text"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert diag.method == "model_with_unordered_tail"
    assert [b.text for b in ordered] == ["body", "footnote text"]


# ---- 场景 7：覆盖率不足进入几何回退 ----

def test_low_coverage_falls_back():
    """模型顺序覆盖率 <80% 时应进入几何回退。"""
    boxes = [
        _box("text", [0.05, 0.1, 0.40, 0.2], order=0, text="a"),
        _box("text", [0.60, 0.1, 0.95, 0.2], order=None, text="b"),
        _box("text", [0.05, 0.4, 0.40, 0.5], order=None, text="c"),
        _box("text", [0.60, 0.4, 0.95, 0.5], order=None, text="d"),
        _box("text", [0.05, 0.7, 0.40, 0.8], order=None, text="e"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert diag.method == "geometric_fallback"
    assert diag.model_coverage < 0.8
    assert ordered  # 仍有稳定输出


def test_duplicate_order_falls_back():
    """order 大规模重复（重复率 >5%）时进入几何回退。"""
    boxes = [
        _box("text", [0.05, 0.1, 0.40, 0.2], order=1, text="a"),
        _box("text", [0.60, 0.1, 0.95, 0.2], order=1, text="b"),
        _box("text", [0.05, 0.4, 0.40, 0.5], order=1, text="c"),
        _box("text", [0.60, 0.4, 0.95, 0.5], order=1, text="d"),
        _box("text", [0.05, 0.7, 0.40, 0.8], order=1, text="e"),
        _box("text", [0.60, 0.7, 0.95, 0.8], order=1, text="f"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert diag.method == "geometric_fallback"


# ---- 场景 8/9：跨栏块分区 ----

def test_span_title_splits_regions():
    """跨栏标题把页面分成上下两个区域，区域内可独立检测双栏。"""
    boxes = [
        _box("text", [0.05, 0.05, 0.40, 0.12], order=None, text="top-left"),
        _box("text", [0.60, 0.05, 0.95, 0.12], order=None, text="top-right"),
        _box("paragraph_title", [0.05, 0.2, 0.95, 0.25], order=None, text="SPAN TITLE"),
        _box("text", [0.05, 0.35, 0.40, 0.45], order=None, text="bot-left"),
        _box("text", [0.60, 0.35, 0.95, 0.45], order=None, text="bot-right"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert diag.method == "geometric_fallback"
    assert diag.region_count >= 3
    texts = [b.text for b in ordered if b.text]
    # 上半区左栏 → 右栏 → 跨栏标题 → 下半区左栏 → 右栏
    assert texts.index("SPAN TITLE") > texts.index("top-left")
    assert texts.index("SPAN TITLE") > texts.index("top-right")
    assert texts.index("SPAN TITLE") < texts.index("bot-left")


def test_span_chart_splits_regions():
    """跨栏图表把页面切成上下两个排序区域（§7.6.5）。"""
    boxes = [
        _box("text", [0.05, 0.05, 0.40, 0.12], order=None, text="top-left"),
        _box("text", [0.60, 0.05, 0.95, 0.12], order=None, text="top-right"),
        _box("figure", [0.05, 0.2, 0.95, 0.45], order=None, text="WIDE FIGURE"),
        _box("text", [0.05, 0.55, 0.40, 0.65], order=None, text="bot-left"),
        _box("text", [0.60, 0.55, 0.95, 0.65], order=None, text="bot-right"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    texts = [b.text for b in ordered if b.text]
    assert texts.index("WIDE FIGURE") > texts.index("top-left")
    assert texts.index("WIDE FIGURE") < texts.index("bot-left")


# ---- 场景 10：单栏页面不退化 ----

def test_single_column_unchanged():
    """单栏页面按 (y0, x0) 排序，行为不退化。"""
    boxes = [
        _box("text", [0.1, 0.5, 0.9, 0.6], order=None, text="second"),
        _box("text", [0.1, 0.1, 0.9, 0.2], order=None, text="first"),
        _box("text", [0.1, 0.8, 0.9, 0.9], order=None, text="third"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0)
    assert diag.detected_columns == 1
    assert [b.text for b in ordered] == ["first", "second", "third"]


# ---- 兼容入口与模式开关 ----

def test_legacy_geometric_mode():
    """legacy-geometric 模式强制几何排序（回滚开关）。"""
    boxes = [
        _box("text", [0.5, 0.1, 0.8, 0.2], order=0, text="right-top"),
        _box("text", [0.1, 0.1, 0.4, 0.2], order=1, text="left-top"),
    ]
    ordered, diag = resolve_reading_order(boxes, 1.0, mode="legacy-geometric")
    assert diag.method == "geometric_fallback"
    assert [b.text for b in ordered] == ["left-top", "right-top"]


def test_sort_boxes_compat_entry():
    """sort_boxes_reading_order 兼容入口仍可用。"""
    boxes = [
        _box("text", [0.5, 0.1, 0.8, 0.2], text="right"),
        _box("text", [0.05, 0.1, 0.45, 0.2], text="left"),
    ]
    ordered = sort_boxes_reading_order(boxes, 1.0)
    assert [b.text for b in ordered] == ["left", "right"]


def test_deterministic_output():
    """同一输入产生相同输出（排序稳定）。"""
    boxes = [
        _box("text", [0.05, 0.1, 0.40, 0.2], order=2, text="a"),
        _box("text", [0.60, 0.1, 0.95, 0.2], order=0, text="b"),
        _box("text", [0.05, 0.4, 0.40, 0.5], order=1, text="c"),
    ]
    r1, _ = resolve_reading_order(boxes, 1.0)
    r2, _ = resolve_reading_order(boxes, 1.0)
    assert [b.text for b in r1] == [b.text for b in r2]


# ---- 回归 fixture：Medlyn 第 1/13 页 ----

@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture 未生成")
def test_medlyn_page13_references_not_alternating():
    """第 13 页参考文献必须整栏顺序：LLLL...RRRR，禁止 LRLR 交替。"""
    pages = _load_fixture()
    ordered, diag = resolve_reading_order(pages["page_013"], 1.0)
    assert diag.method == "model"
    refs = [b for b in ordered if b.label == "reference_content"]
    assert len(refs) >= 10
    seq = "".join("L" if (b.bbox[0] + b.bbox[2]) / 2 < 0.5 else "R" for b in refs)
    switches = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
    assert switches <= 1, f"参考文献左右交替：{seq}"


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture 未生成")
def test_medlyn_page13_container_dedup():
    """第 13 页 reference 父框应被去重。"""
    pages = _load_fixture()
    diag = ReadingOrderDiagnostics()
    kept = dedupe_containers(pages["page_013"], diag)
    assert diag.removed_containers >= 1
    assert all(b.label != "reference" for b in kept)


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture 未生成")
def test_medlyn_page1_abstract_after_title():
    """第 1 页：摘要正文应排在标题之后、且整页无左右振荡。"""
    pages = _load_fixture()
    ordered, diag = resolve_reading_order(pages["page_001"], 1.0)
    labels = [b.label for b in ordered]
    assert diag.method == "model"
    assert labels.index("abstract") > labels.index("doc_title")
    # 摘要区（abstract + 相邻 text）无 LRLR 交替
    seq = [
        "L" if (b.bbox[0] + b.bbox[2]) / 2 < 0.5 else "R"
        for b in ordered
        if b.label in ("abstract", "text", "paragraph_title")
    ]
    switches = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
    assert switches <= 2, f"第 1 页左右振荡：{''.join(seq)}"


def test_model_order_not_mutated():
    """排序后不修改 LayoutBox.order（保留 model_order 语义）。"""
    boxes = [
        _box("text", [0.1, 0.1, 0.4, 0.2], order=5, text="a"),
        _box("text", [0.6, 0.1, 0.9, 0.2], order=2, text="b"),
    ]
    resolve_reading_order(boxes, 1.0)
    assert boxes[0].order == 5
    assert boxes[1].order == 2
