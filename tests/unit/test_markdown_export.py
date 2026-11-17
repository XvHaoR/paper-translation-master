"""Markdown 导出测试（app/rendering/markdown_exporter.py）。"""

from __future__ import annotations

from app.models.ir import Block, BlockContent, BlockType, QualityInfo
from app.rendering.markdown_exporter import (
    heading_level,
    html_table_to_markdown,
    render_markdown,
)


def _block(
    bid,
    btype,
    page,
    order,
    text=None,
    latex=None,
    html=None,
    note=None,
    items=None,
    image_path=None,
    warnings=None,
    needs_review=False,
):
    return Block(
        id=bid,
        type=btype,
        page=page,
        order=order,
        bbox=[0.05, 0.1, 0.95, 0.3],
        content=BlockContent(
            text=text,
            latex=latex,
            html=html,
            note=note,
            items=items,
            image_path=image_path,
        ),
        quality=QualityInfo(warnings=warnings or [], needs_review=needs_review),
    )


def test_heading_level():
    assert heading_level("1. Introduction") == 2
    assert heading_level("1.1 Methods") == 3
    assert heading_level("1.2.3 Details") == 4
    assert heading_level("Conclusion") == 2


def test_render_markdown_basic_structure():
    blocks = [
        _block("t", BlockType.DOCUMENT_TITLE, 1, 0, text="A Great Paper"),
        _block("h", BlockType.HEADING, 1, 1, text="1. Introduction"),
        _block("p", BlockType.PARAGRAPH, 1, 2, text="Some $CO_{2}$ text."),
        _block("a", BlockType.ABSTRACT, 1, 3, text="We propose a method."),
    ]
    md = render_markdown(blocks)
    assert md.startswith("# A Great Paper")
    assert "## 1. Introduction" in md
    assert "Some $CO_{2}$ text." in md
    assert "## Abstract" in md


def test_equations_and_list():
    blocks = [
        _block("e", BlockType.EQUATION_BLOCK, 2, 0, latex=r"\mathbf{X}=E(x)", note="(1)"),
        _block("i", BlockType.EQUATION_INLINE, 2, 1, latex=r"\alpha"),
        _block("l", BlockType.LIST, 2, 2, items=["first", "second"]),
    ]
    md = render_markdown(blocks)
    assert "$$\n\\mathbf{X}=E(x)\n\\tag{1}\n$$" in md
    assert r"$\alpha$" in md
    assert "- first" in md and "- second" in md


def test_table_html_to_markdown():
    html = (
        "<table><tr><th>Method</th><th>IoU</th></tr>"
        "<tr><td>Ours</td><td>85.3</td></tr></table>"
    )
    md = html_table_to_markdown(html)
    assert md == "| Method | IoU |\n| --- | --- |\n| Ours | 85.3 |"


def test_table_pipe_escaping():
    md = html_table_to_markdown("<table><tr><td>a|b</td><td>c</td></tr></table>")
    assert "a\\|b" in md


def test_table_fallback_when_unparseable():
    assert html_table_to_markdown("<div>no table</div>") is None
    blocks = [_block("tb", BlockType.TABLE, 1, 0, html="<div>broken</div>")]
    md = render_markdown(blocks)
    assert "```html" in md


def test_figure_and_page_number_skipped():
    blocks = [
        _block("n1", BlockType.PAGE_NUMBER, 1, 0, text="1"),
        _block("f", BlockType.FIGURE, 1, 1, image_path="assets/figures/fig1.png"),
        _block("c", BlockType.CAPTION, 1, 2, text="Fig. 1 Overview."),
        _block("n2", BlockType.PAGE_NUMBER, 2, 0, text="2"),
        _block("p", BlockType.PARAGRAPH, 2, 1, text="next page"),
    ]
    md = render_markdown(blocks)
    assert "![图 1](assets/figures/fig1.png)" in md
    assert "Fig. 1 Overview." in md
    assert "<!-- p.2 -->" in md
    assert "next page" in md
    assert "page_number" not in md


def test_review_warning_comment():
    blocks = [
        _block(
            "p", BlockType.PARAGRAPH, 1, 0, text="low conf",
            warnings=["低置信度"], needs_review=True,
        )
    ]
    md = render_markdown(blocks)
    assert "<!-- ⚠ 需核对：低置信度 -->" in md


def test_official_formula_delimiters_are_not_wrapped_twice():
    from app.rendering.html_renderer import render_official_markdown
    from bs4 import BeautifulSoup
    blocks = [_block("eq", BlockType.EQUATION_BLOCK, 1, 0,
                     latex=r" $$ Para=Para_{25}\times\exp\left(\frac{T_k-298.15}{RT_k}\right) $$ ")]
    md = render_markdown(blocks)
    assert md.count("$$") == 2
    node = BeautifulSoup(render_official_markdown(md), "html.parser").select_one(".math-display")
    assert node["data-latex"].startswith("Para=")
    assert r"\frac" in node["data-latex"] and "$$" not in node["data-latex"]
