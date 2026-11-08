"""Semantic HTML rendering. Tables never round-trip through a Markdown grid."""
import html
import re

import bleach

from app.models.ir import BlockType
from app.rendering.html_renderer import render_table_text_nodes, _official_math_node
from app.rendering.markdown_exporter import render_markdown
from app.rendering.markdown_util import clean_latex, strip_math_delimiters


def retype_fragment(fragment, block, *, first_abstract):
    """Change a legacy text wrapper while preserving its inline HTML and TeX."""
    fragment = re.sub(r'^\s*<h2>Abstract</h2>', '', fragment, flags=re.I)
    fragment = re.sub(r'^\s*<h[1-6]>(.*?)</h[1-6]>\s*$', r'<p>\1</p>', fragment, flags=re.S)
    if block.type in {BlockType.HEADING, BlockType.DOCUMENT_TITLE}:
        level = 1 if block.type is BlockType.DOCUMENT_TITLE else (block.heading_level or 2)
        body = re.sub(r'^\s*<p>(.*?)</p>\s*$', r'\1', fragment, flags=re.S)
        return f'<h{level}>{body}</h{level}>'
    if block.type is BlockType.ABSTRACT and first_abstract:
        fragment = '<h2>Abstract</h2>' + fragment
    return fragment


def block_html(block, render, *, first_abstract=True):
    if block.type is BlockType.TABLE and block.content.html:
        table = bleach.clean(block.content.html,
            tags={"table","thead","tbody","tfoot","tr","th","td","p","span","br","sub","sup","strong","em"},
            attributes={"td":["colspan","rowspan"],"th":["colspan","rowspan"]},strip=True)
        return render_table_text_nodes(table)
    if block.type is BlockType.ABSTRACT:
        body = re.sub(r"^\s*(?:#{1,6}\s*)?Abstract\b[.：:\s]*", "", block.content.text or "", flags=re.I)
        return ("<h2>Abstract</h2>" if first_abstract else "") + render(body)
    if block.type in (BlockType.EQUATION_BLOCK,BlockType.EQUATION_INLINE):
        latex = clean_latex(strip_math_delimiters(block.content.latex or ""))
        display = block.type is BlockType.EQUATION_BLOCK
        content = _official_math_node(latex,display=display)
        number = (block.content.note or "").strip().strip("()")
        if display and re.fullmatch(r"[A-Za-z]?\d+[A-Za-z]?",number) and r"\tag" not in latex:
            content = f'<div class="paper-equation">{content}<span class="paper-equation-number">({html.escape(number)})</span></div>'
        return content
    return render(render_markdown([block]))
