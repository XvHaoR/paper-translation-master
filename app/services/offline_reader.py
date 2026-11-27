"""Structured offline blocks keep translation pairs and source PDF coordinates."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

from app.models.ir import Block, BlockType, ordered_blocks
from app.rendering.markdown_exporter import render_markdown, SKIP_TYPES
from app.services.translator import TRANSLATABLE_TYPES
from app.services.ocr_edit import role
from app.rendering.paper_blocks import block_html, retype_fragment

ROLE_LABELS = {"document_title": "论文标题", "heading": "章节标题", "paragraph": "正文", "abstract": "摘要",
               "figure_caption": "图注", "table_caption": "表注", "figure": "图片", "table": "表格",
               "equation_block": "公式", "equation_inline": "行内公式", "reference": "参考文献",
               "footnote": "脚注", "list": "列表", "code": "代码", "authors": "作者"}


def render_sections(blocks: list[Block], originals: list[Block] | None, render, *, target_html=None, source_html=None) -> str:
    source = {block.id: block for block in originals or blocks}
    parts = []
    notes = []
    abstract_seen = False
    for block in ordered_blocks(blocks):
        if block.type in SKIP_TYPES:
            continue
        original = source.get(block.id, block)
        bbox = original.bbox
        bbox = [max(0, min(1, value)) for value in bbox] if len(bbox) == 4 and all(math.isfinite(value) for value in bbox) else []
        rendered = target_html.get(block.id) if target_html is not None else None
        if rendered is not None:
            # Legacy translations retain their original HTML, including TeX and
            # tables. Retyping changes presentation, never the translated text.
            rendered = retype_fragment(rendered, block, first_abstract=not abstract_seen)
        else:
            rendered = block_html(block, render, first_abstract=not abstract_seen)
        if not rendered.strip():
            continue
        attrs = (f'data-block-id="{html.escape(block.id, quote=True)}" data-page="{block.page}" '
                 f'data-bbox="{html.escape(json.dumps(bbox), quote=True)}" '
                 f'data-block-type="{block.type.value}" data-role="{role(block)}" '
                 f'data-anchor-id="{html.escape(block.anchor_id or "", quote=True)}" '
                 f'data-placement="{html.escape(block.placement or "", quote=True)}" '
                 f'data-reading-order="{block.reading_order if block.reading_order is not None else ""}" '
                 f'data-regions="{html.escape(json.dumps(original.source.regions), quote=True)}" '
                 f'data-source-text="{html.escape(original.source.text or "", quote=True)}"')
        content = f'<div class="pl-target">{rendered}</div>'
        if originals and block.type.value in TRANSLATABLE_TYPES:
            source_fragment = source_html.get(block.id) if source_html is not None else None
            if source_fragment is None:
                source_fragment = block_html(original, render, first_abstract=not abstract_seen)
            else:
                source_fragment = retype_fragment(source_fragment, block, first_abstract=not abstract_seen)
            content = f'<div class="pl-source" lang="en">{source_fragment}</div>' + content
        if block.type is BlockType.ABSTRACT:
            abstract_seen = True
        if block.quality.needs_review and block.quality.warnings:
            content += '<details class="paper-warning"><summary>识别结果需核对</summary>' + html.escape('；'.join(block.quality.warnings)) + '</details>'
        badge = f'<span class="pl-role">{ROLE_LABELS.get(role(block), block.type.value)}</span>'
        (notes if block.type is BlockType.FOOTNOTE and block.placement != "manual" else parts).append(f'<section class="pl-block" {attrs}>{badge}{content}</section>')
    if notes:
        parts.append('<section class="paper-footnotes" role="doc-endnotes" aria-label="脚注"><h2>脚注</h2>' + '\n'.join(notes) + '</section>')
    return "\n".join(parts)


def copy_runtime(output: Path) -> None:
    import shutil
    static = Path(__file__).resolve().parents[1] / "static"
    (output / "assets").mkdir(exist_ok=True)
    for filename in ("offline_reader.js", "table_excel.js", "offline_reader.css", "ocr_editor.js"):
        shutil.copyfile(static / filename, output / "assets" / filename)
