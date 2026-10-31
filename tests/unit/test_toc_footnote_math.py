"""目录、脚注与公式渲染测试（TOC_FOOTNOTE_MATH_RENDERING_DESIGN.md §14）。"""

from __future__ import annotations

from pathlib import Path

from app.models.ir import Block, BlockContent, BlockType, DocumentIR, block_id
from app.pipeline.footnote_anchor import (
    arrange_footnotes,
    resolve_footnote_anchors,
)
from app.pipeline.quality import (
    check_footnote_anchors,
    check_html_unrendered_math,
)
from app.rendering.html_renderer import (
    HtmlRenderer,
    iter_outline_entries,
    render_inline_math,
    render_table_text_nodes,
)

_DOC_DIR = Path(".")  # 渲染测试不依赖文档目录（MathJax 回退 CDN）


def _block(bid, btype, page, order, text=None, bbox=None, latex=None, html=None):
    return Block(
        id=bid,
        type=btype,
        page=page,
        order=order,
        bbox=bbox or [0.05, 0.1, 0.95, 0.3],
        content=BlockContent(text=text, latex=latex, html=html),
    )


def _ir(blocks) -> DocumentIR:
    ir = DocumentIR()
    ir.document.id = "test"
    for b in blocks:
        ir.add_block(b)
    return ir


# ==================== 14.1 目录 ====================


def test_toc_abstract_dedup():
    """HEADING ABSTRACT + ABSTRACT 正文 → 目录只有一个 Abstract，指向正文块。"""
    blocks = [
        _block("p1-b0", BlockType.DOCUMENT_TITLE, 1, 0, "Paper"),
        _block("p1-b3", BlockType.HEADING, 1, 1, "ABSTRACT"),
        _block("p1-b4", BlockType.ABSTRACT, 1, 2, "The dependence of $C_3$ photosynthesis..."),
        _block("p1-b7", BlockType.HEADING, 1, 3, "Introduction"),
    ]
    ir = _ir(blocks)
    entries = list(iter_outline_entries(ir))
    labels = [label for _b, label, _lvl in entries]
    assert labels == ["Abstract", "Introduction"]
    # 目录链接指向摘要正文块（p1-b4），而不是被隐藏的标题块（p1-b3）
    html = HtmlRenderer().render_document(ir, _DOC_DIR)
    toc = html.split('<div id="toc-panel"')[1].split("</div>")[0]
    assert 'href="#p1-b4"' in toc
    assert 'href="#p1-b3"' not in toc
    assert "ABSTRACT" not in toc


def test_toc_heading_math_rendered():
    """目录标题中的 $...$ 公式渲染为 math-inline 节点。"""
    blocks = [
        _block("p1-b0", BlockType.HEADING, 1, 0, "T-dependence of $J_{max}$ and $V_{cmax}$"),
    ]
    ir = _ir(blocks)
    html = HtmlRenderer().render_document(ir, _DOC_DIR)
    toc = html.split('<div id="toc-panel"')[1].split("</div>")[0]
    assert 'class="math-inline' in toc
    assert "data-latex" in toc
    assert "$J_{max}$" not in toc  # 不再显示源码


def test_toc_no_abstract_body_as_title():
    """摘要正文不得作为目录标题。"""
    blocks = [
        _block("p1-b4", BlockType.ABSTRACT, 1, 0, "The temperature dependence of..."),
    ]
    ir = _ir(blocks)
    entries = list(iter_outline_entries(ir))
    assert len(entries) == 1
    assert entries[0][1] == "Abstract"


# ==================== 14.2 脚注 ====================


def test_footnote_section_anchor():
    """跨页章节脚注：Correspondence 放在完整 Introduction 之后。"""
    blocks = [
        _block("p1-b7", BlockType.HEADING, 1, 0, "Introduction"),
        _block("p1-b8", BlockType.PARAGRAPH, 1, 1, "page1 body"),
        _block("p1-b11", BlockType.FOOTNOTE, 1, 2, "Correspondence: ...",
               bbox=[0.05, 0.85, 0.95, 0.95]),
        _block("p2-b12", BlockType.PARAGRAPH, 2, 3, "page2 continuation"),
        _block("p2-b15", BlockType.HEADING, 2, 4, "Methods"),
    ]
    ir = _ir(blocks)
    resolve_footnote_anchors(ir)
    fn = ir.blocks[2]
    assert fn.anchor_id == "p1-b7"
    assert fn.anchor_kind == "section"
    assert fn.placement == "section_end"
    # 来源页不变
    assert fn.page == 1

    arranged = arrange_footnotes(ir.blocks)
    ids = [b.id for b in arranged]
    assert ids.index("p1-b11") > ids.index("p2-b12")  # 脚注在 p2 续文之后
    assert ids.index("p1-b11") < ids.index("p2-b15")  # 脚注在 Methods 之前


def test_footnote_table_anchor():
    """表格脚注：同页、位于表格下方 → 紧跟表格组。"""
    blocks = [
        _block("p9-b112", BlockType.TABLE, 9, 0, html="<table><tr><td>x</td></tr></table>",
               bbox=[0.05, 0.1, 0.95, 0.5]),
        _block("p9-b118", BlockType.FOOTNOTE, 9, 1, "Values of $k_{25}$...",
               bbox=[0.05, 0.52, 0.95, 0.6]),
    ]
    ir = _ir(blocks)
    resolve_footnote_anchors(ir)
    fn = ir.blocks[1]
    assert fn.anchor_kind == "table"
    assert fn.placement == "group_end"
    arranged = arrange_footnotes(ir.blocks)
    assert arranged[0].id == "p9-b112"
    assert arranged[1].id == "p9-b118"


def test_footnote_document_end():
    """Received 收稿信息 → 文档末尾。"""
    blocks = [
        _block("p13-b158", BlockType.HEADING, 13, 0, "References"),
        _block("p13-b190", BlockType.FOOTNOTE, 13, 1, "Received 8 November 2001...",
               bbox=[0.05, 0.85, 0.95, 0.95]),
    ]
    ir = _ir(blocks)
    resolve_footnote_anchors(ir)
    fn = ir.blocks[1]
    assert fn.anchor_kind == "document"
    assert fn.placement == "document_end"
    arranged = arrange_footnotes(ir.blocks)
    assert arranged[-1].id == "p13-b190"


def test_footnote_no_anchor_kept():
    """无法可靠归属的脚注保持原位置并标记需核对。"""
    blocks = [
        _block("p1-b0", BlockType.PARAGRAPH, 1, 0, "body"),
        _block("p1-b1", BlockType.FOOTNOTE, 1, 1, "odd footnote",
               bbox=[0.05, 0.3, 0.95, 0.4]),
    ]
    ir = _ir(blocks)
    warnings = resolve_footnote_anchors(ir)
    fn = ir.blocks[1]
    assert fn.placement is None
    assert fn.quality.needs_review
    assert any("锚点" in w for w in fn.quality.warnings)
    assert warnings  # 告警列表非空


def test_footnote_quality_check():
    """无锚点脚注触发质量告警。"""
    blocks = [
        _block("p1-b0", BlockType.FOOTNOTE, 1, 0, "no anchor"),
    ]
    warnings = check_footnote_anchors(blocks)
    assert any("锚点" in w for w in warnings)


# ==================== 14.3 公式渲染 ====================


def test_render_inline_math_in_heading():
    html = render_inline_math("T-dependence of $J_{max}$ and $V_{cmax}$")
    assert html.count("math-inline") == 2
    assert 'translate="no"' in html
    assert 'data-latex="J_{max}"' in html
    assert "$J_{max}$" not in html


def test_render_inline_math_money_not_parsed():
    """金额 $100 不误判为公式。"""
    html = render_inline_math("cost is $100 and $200 total")
    assert "math-inline" not in html
    assert "cost is $100 and $200 total" in html


def test_render_inline_math_incomplete_delimiter():
    """不完整定界符不吞掉后续文本。"""
    html = render_inline_math("value $V_{cmax} and more text")
    assert "math-inline" not in html  # 无配对 $ 保持原文
    assert "and more text" in html


def test_render_inline_math_caption():
    html = render_inline_math("Figure 1. Response of $K_{c}$ at $25^{\\circ}C$")
    assert html.count("math-inline") == 2


def test_table_text_nodes_math():
    """表格单元格中的公式转换为 math-inline 节点。"""
    raw = "<table><tr><td>$k_{25}(\\mu mol m^{-2} s^{-1})$</td></tr></table>"
    out = render_table_text_nodes(raw)
    assert "math-inline" in out
    assert 'data-latex="k_{25}(\\mu mol m^{-2} s^{-1})"' in out
    assert "<td>" in out  # 结构保留


def test_table_text_nodes_newline():
    """表格文本节点中的字面量 \\n 转为 <br>。"""
    raw = "<table><tr><td>$V_{cmax}$\\n$E_a$\\n58.52</td></tr></table>"
    out = render_table_text_nodes(raw)
    assert out.count("<br>") == 2
    assert out.count("math-inline") == 2


def test_table_text_nodes_structure_preserved():
    """标签与属性不被修改。"""
    raw = '<table><tr><td colspan="2">$x$</td><th rowspan="1">h</th></tr></table>'
    out = render_table_text_nodes(raw)
    assert 'colspan="2"' in out
    assert 'rowspan="1"' in out
    assert "<th rowspan=" in out


# ==================== 14.4 安全 ====================


def test_table_safety_script_stripped_then_math():
    """script 被删除，$x$ 被转换为安全公式节点。"""
    import bleach

    from app.rendering.html_renderer import ALLOWED_ATTRS, ALLOWED_TAGS

    raw = "<table><tr><td><script>alert(1)</script>$x$</td></tr></table>"
    cleaned = bleach.clean(
        raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True
    )
    assert "<script" not in cleaned
    out = render_table_text_nodes(cleaned)
    assert "script" not in out
    assert "math-inline" in out


def test_html_unrendered_math_check():
    """渲染后可见定界符检查：script 内容与 math-inline 节点不计入。"""
    html = (
        '<script>{"text": "raw $x$ data"}</script>'
        '<p><span class="math-inline" translate="no" data-latex="x">\\(x\\)</span></p>'
        "<p>still $unconverted$ here</p>"
    )
    warnings = check_html_unrendered_math(html)
    assert len(warnings) == 1
    assert "1 处" in warnings[0]


# ==================== 渲染集成 ====================


def test_render_document_footnote_placement():
    """渲染集成：脚注按章节结束放置。"""
    blocks = [
        _block("p1-b7", BlockType.HEADING, 1, 0, "Introduction"),
        _block("p1-b8", BlockType.PARAGRAPH, 1, 1, "body"),
        _block("p1-b11", BlockType.FOOTNOTE, 1, 2, "Correspondence",
               bbox=[0.05, 0.85, 0.95, 0.95]),
        _block("p2-b12", BlockType.PARAGRAPH, 2, 3, "continuation"),
        _block("p2-b15", BlockType.HEADING, 2, 4, "Methods"),
    ]
    ir = _ir(blocks)
    resolve_footnote_anchors(ir)
    html = HtmlRenderer().render_document(ir, _DOC_DIR)
    # 正文中 Introduction 的 p2 续文应出现在脚注之前
    body = html.split("<main")[1].split("</main>")[0]
    assert body.index("continuation") < body.index("Correspondence")
    assert body.index("Correspondence") < body.index("Methods")
