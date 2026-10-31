"""Markdown 工具与质量检查。"""

from app.pipeline.quality import (
    check_duplicate_text,
    check_html_safety,
    check_latex_blocks,
    check_missing_pages,
)
from app.models.ir import Block, BlockContent, BlockType, DocumentIR, block_id
from app.rendering.markdown_util import (
    check_latex_renderable,
    check_latex_safe,
    markdown_table_to_html,
    split_inline_latex,
)


def test_markdown_table_to_html():
    md = "| Method | Acc |\n|---|---|\n| Ours | 99% |\n| Base | 95% |"
    html = markdown_table_to_html(md)
    assert html is not None
    assert "<table>" in html and "<thead>" in html and "<tbody>" in html
    assert "<td>Ours</td>" in html
    assert "<script" not in html


def test_markdown_table_rejects_plain_text():
    assert markdown_table_to_html("Just a paragraph of text.") is None


def test_split_inline_latex():
    parts = split_inline_latex("Energy is $E = mc^2$ in physics")
    assert parts[0] == ("text", "Energy is ")
    assert parts[1][0] == "latex"
    assert "E = mc^2" in parts[1][1]
    assert parts[2] == ("text", " in physics")


def test_check_latex_safe_blocks_dangerous():
    assert check_latex_safe(r"\input{/etc/passwd}") != []
    assert check_latex_safe(r"\href{http://x}{y}") != []
    assert check_latex_safe(r"\frac{a}{b}") == []


def test_check_latex_unbalanced_braces():
    assert check_latex_safe(r"\frac{a}{b") != []


def test_check_latex_renderable():
    assert check_latex_renderable("x^2")
    assert not check_latex_renderable("\\")


def _para_block(text, bid="p1-b0"):
    return Block(id=bid, type=BlockType.PARAGRAPH, page=1, order=0,
                 content=BlockContent(text=text))


def test_duplicate_text_detected():
    blocks = [_para_block("Same long sentence appears many times in output. " * 1, f"p1-b{i}") for i in range(4)]
    warnings = check_duplicate_text(blocks)
    assert any("重复" in w for w in warnings)


def test_html_safety_script():
    b = Block(id="p1-b0", type=BlockType.TABLE, page=1, order=0,
              content=BlockContent(html="<table><script>alert(1)</script></table>"))
    warnings = check_html_safety([b])
    assert warnings and b.quality.needs_review


def test_html_safety_onclick():
    b = Block(id="p1-b0", type=BlockType.TABLE, page=1, order=0,
              content=BlockContent(html='<table><td onclick="x()">a</td></table>'))
    warnings = check_html_safety([b])
    assert warnings


def test_latex_block_warning():
    b = Block(id="p1-b0", type=BlockType.EQUATION_BLOCK, page=1, order=0,
              content=BlockContent(latex=r"\input{x}"))
    warnings = check_latex_blocks([b])
    assert warnings and b.quality.warnings


def test_missing_pages():
    ir = DocumentIR()
    ir.document.id = "x"
    ir.document.page_count = 5
    ir.add_block(_para_block("only page 1", "p1-b0"))
    warnings = check_missing_pages(ir)
    assert warnings and "2" in warnings[0]
