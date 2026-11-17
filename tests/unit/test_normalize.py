"""规范化：连字符、换行合并、页眉页脚、段落分类。"""

from app.pipeline.normalize import (
    assemble_paragraphs,
    classify_native_paragraph,
    collect_repeated_short_lines,
    dehyphenate,
    is_header_or_footer,
    merge_cross_page_paragraphs,
)
from app.models.ir import Block, BlockContent, BlockType, block_id
from app.parsing.native_pdf import NativeLine


def _line(text, y0=0, y1=14, size=10.0, font="helv"):
    return NativeLine(text=text, bbox=(0, y0, 300, y1), size=size, font=font)


def test_dehyphenate_joins():
    text = "This is a hy-\nphenated word across lines."
    assert dehyphenate(text) == "This is a hyphenated word across lines."


def test_dehyphenate_keeps_numbered_lines():
    text = "[1] First ref line\n[2] Second ref line"
    out = dehyphenate(text)
    assert "[1]" in out and "[2]" in out


def test_dehyphenate_plain_join():
    assert dehyphenate("line one\nline two") == "line one line two"


def test_assemble_paragraphs_splits_by_gap():
    lines = [
        _line("First paragraph line 1", 0, 14),
        _line("First paragraph line 2", 16, 30),
        _line("Second paragraph starts", 60, 74),  # 大间隙
    ]
    paras = assemble_paragraphs(lines)
    assert len(paras) == 2


def test_assemble_paragraphs_splits_by_size():
    lines = [
        _line("Body text", 0, 14, size=10),
        _line("BIG HEADING", 16, 30, size=14),
    ]
    paras = assemble_paragraphs(lines)
    assert len(paras) == 2


def test_is_header_or_footer_page_number():
    assert is_header_or_footer([0.0, 0.01, 0.5, 0.03], "12")
    assert is_header_or_footer([0.0, 0.97, 0.5, 0.99], "- 3 -")


def test_is_header_or_footer_conservative_without_repeats():
    # 无重复信息时，顶部短标题不应被误删
    assert not is_header_or_footer([0.1, 0.02, 0.9, 0.05], "A Benchmark Study Title")


def test_is_header_or_footer_with_repeats():
    repeats = {"Journal of Testing 2026"}
    assert is_header_or_footer([0.1, 0.01, 0.9, 0.03], "Journal of Testing 2026", repeats)
    assert not is_header_or_footer([0.1, 0.01, 0.9, 0.03], "A Different Line", repeats)


def test_collect_repeated_short_lines():
    pages = [
        [([0.0, 0.01, 0.9, 0.03], "Header Line"), ([0.1, 0.5, 0.8, 0.6], "Body")],
        [([0.0, 0.01, 0.9, 0.03], "Header Line"), ([0.1, 0.5, 0.8, 0.6], "Other")],
        [([0.0, 0.01, 0.9, 0.03], "Header Line")],
    ]
    assert "Header Line" in collect_repeated_short_lines(pages)


def test_classify_heading_vs_reference():
    # 章节标题：不按小字号参考文献处理
    heading_lines = [_line("1. Introduction", size=10.0)]
    btype, _ = classify_native_paragraph(heading_lines, 1)
    assert btype is BlockType.PARAGRAPH

    ref_lines = [_line("[1] Smith J. Title. 2025.", size=9.0)]
    btype, _ = classify_native_paragraph(ref_lines, 1)
    assert btype is BlockType.REFERENCE


def test_classify_authors():
    lines = [_line("Alice Zhang and Bob Li", size=12.0)]
    btype, _ = classify_native_paragraph(lines, 1)
    assert btype is BlockType.AUTHORS


def test_classify_caption():
    lines = [_line("Figure 1: Pipeline diagram", size=10.0)]
    btype, _ = classify_native_paragraph(lines, 1)
    assert btype is BlockType.CAPTION


def test_merge_cross_page_paragraphs():
    b1 = Block(id=block_id(1, 0), type=BlockType.PARAGRAPH, page=1, order=0,
               content=BlockContent(text="This sentence continues"))
    b2 = Block(id=block_id(2, 0), type=BlockType.PARAGRAPH, page=2, order=0,
               content=BlockContent(text="onto the next page."))
    merged = merge_cross_page_paragraphs([b1, b2])
    assert len(merged) == 1
    assert merged[0].content.text == "This sentence continues onto the next page."
    assert merged[0].quality.needs_review


def test_no_merge_when_sentence_complete():
    b1 = Block(id=block_id(1, 0), type=BlockType.PARAGRAPH, page=1, order=0,
               content=BlockContent(text="This sentence ends here."))
    b2 = Block(id=block_id(2, 0), type=BlockType.PARAGRAPH, page=2, order=0,
               content=BlockContent(text="New paragraph starts."))
    merged = merge_cross_page_paragraphs([b1, b2])
    assert len(merged) == 2


# ---- 列表(bullet)识别 ----

def test_is_list_text():
    from app.pipeline.normalize import is_list_text
    assert is_list_text("- We propose MoBaNet, a framework.\n- Second contribution.")
    assert is_list_text("• Item one\n• Item two")
    assert is_list_text("1) First\n2) Second")
    assert is_list_text("(a) first\n(b) second")
    assert not is_list_text("We propose MoBaNet, a parameter-efficient framework.")
    assert not is_list_text("")
    # 普通文本中的连字符行不是列表
    assert not is_list_text("some - text here\nwithout bullets")


def test_split_list_items():
    from app.pipeline.normalize import split_list_items
    items = split_list_items("- First item continues\n  on next line.\n- Second item.")
    assert items == ["First item continues on next line.", "Second item."]
    items2 = split_list_items("• A\n• B\n• C")
    assert items2 == ["A", "B", "C"]
    items3 = split_list_items("1) One\n2) Two with\n   continuation")
    assert items3 == ["One", "Two with continuation"]
    # 无 bullet 的文本返回单个整段
    assert split_list_items("plain text") == ["plain text"]
