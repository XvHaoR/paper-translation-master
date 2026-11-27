"""Map MinerU's ordered blocks into the editable reader IR, keeping PDF geometry."""
from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import time
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

import pymupdf
from PIL import Image

from app.models.ir import Block, BlockContent, BlockType as T, DocumentIR, DocumentMeta, PageInfo, QualityInfo, SourceInfo, SourceMethod, block_id
from app.parsing.auto_pdf import _crop
from app.parsing.reading_flow import join_text
from app.rendering.markdown_util import markdown_table_to_html, strip_math_delimiters

_TYPES = {
    "text": T.PARAGRAPH, "ref_text": T.REFERENCE, "doc_title": T.DOCUMENT_TITLE,
    "title": T.HEADING, "paragraph_title": T.HEADING, "abstract": T.ABSTRACT,
    "page_footnote": T.FOOTNOTE, "footnote": T.FOOTNOTE, "aside_text": T.PARAGRAPH,
    "equation": T.EQUATION_BLOCK, "interline_equation": T.EQUATION_BLOCK,
    "image": T.FIGURE, "chart": T.FIGURE, "table": T.TABLE,
    "code": T.CODE, "algorithm": T.CODE, "list": T.LIST, "index": T.LIST,
}
_DECORATION = {"header", "footer", "page_number", "page_header", "page_footer"}
_VISUAL = {"image", "table", "chart", "code", "algorithm"}
_RASTER = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}
_MAX_JSON = 64 * 1024 * 1024


def _box(rect, width, height):
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        return []
    if not all(isinstance(n, (float, int)) and math.isfinite(n) for n in rect):
        return []
    box = [max(0., min(1., n / size)) for n, size in zip(rect, (width, height) * 2)]
    return box if box[0] < box[2] and box[1] < box[3] else []


def _spans(spans):
    if isinstance(spans, str):
        return spans
    text = []
    for span in spans or []:
        value = span.get("content", "")
        kind = span.get("type")
        if kind in {"equation_inline", "inline_equation", "inline_formula"} and value:
            value = "$" + strip_math_delimiters(value) + "$"
        elif kind == "hyperlink":
            value = _spans(value)
        elif kind == "code_inline":
            value = "`" + value + "`"
        elif "superscript" in (span.get("styles") or []):
            value = "<sup>" + value + "</sup>"
        elif "subscript" in (span.get("styles") or []):
            value = "<sub>" + value + "</sub>"
        if isinstance(value, str):
            text.append(value)
    return "".join(text)


def _text(node):
    if "lines" in node:
        value = ""
        for line in node["lines"]:
            value = join_text(value, _spans(line.get("spans")).strip())
        return value
    return _spans(node.get("content", node.get("text", ""))).strip()


def _asset(zipfile, folder, ref, root):
    if not isinstance(ref, str) or not ref or "\\" in ref or ":" in ref:
        return None
    relative = PurePosixPath(ref)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    if len(relative.parts) == 1:
        relative = PurePosixPath("images") / relative
    try:
        info = zipfile.getinfo((folder / relative).as_posix())
    except KeyError:
        return None
    if info.file_size > 32 * 1024 * 1024:
        return None
    data = zipfile.read(info)
    try:
        with Image.open(BytesIO(data)) as image:
            suffix = _RASTER.get(image.format)
            if not suffix or image.width * image.height > 80_000_000:
                return None
            image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return None
    relative = Path("assets/media") / (hashlib.sha256(data).hexdigest()[:24] + suffix)
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    if not (root / relative).exists():
        (root / relative).write_bytes(data)
    return relative.as_posix()


def _leaf_nodes(node, fallback_box=None):
    kind = node.get("type", "text")
    rect = node.get("bbox") or fallback_box
    if kind in _DECORATION or node.get("lines_deleted"):
        return
    children = node.get("blocks")
    if children is None and kind in _VISUAL | {"list", "index"} and isinstance(node.get("content"), list):
        children = node["content"]
    if children:
        for child in children:
            child_kind = child.get("type", "")
            if child_kind.endswith("_body"):
                yield kind, child, child.get("bbox") or rect
            elif child_kind.endswith(("_caption", "_footnote")):
                yield child_kind, child, child.get("bbox") or rect
            elif kind in ("list", "index") and child_kind in ("text", "ref_text"):
                yield "ref_text" if child_kind == "ref_text" or node.get("sub_type") == "ref_text" else "list", child, child.get("bbox") or rect
            else:
                yield from _leaf_nodes(child, rect)
        return
    yield kind, node, rect


def _flat_nodes(node):
    """Legacy content_list groups captions into string arrays and uses a 1000-unit grid."""
    kind = node.get("type", "")
    if kind in ("discarded", "header", "footer", "page_number"):
        return
    if kind == "text" and node.get("text_level"):
        kind = "title"
    if kind == "table":
        for caption in node.get("table_caption") or []:
            yield "table_caption", {"text": caption}, None
    yield kind, node, node.get("bbox")
    if kind in ("image", "chart"):
        for caption in node.get("img_caption") or []:
            yield "image_caption", {"text": caption}, None
    for note in (node.get("img_footnote") or node.get("table_footnote") or []):
        yield kind + "_footnote", {"text": note}, None


def _read_layout(archive):
    infos = archive.infolist()
    if len(infos) > 30000:
        raise ValueError("MinerU 结果包文件过多")
    def priority(name):
        if name == "layout.json" or name in ("middle.json", "middle_json.json") or name.endswith("_middle.json"):
            return 0
        if name == "content_list.json" or name.endswith("_content_list.json"):
            return 1
        return 2
    choices = sorted((priority(PurePosixPath(i.filename).name), i.filename) for i in infos if not i.is_dir())
    if not choices or choices[0][0] == 2:
        raise ValueError("MinerU 结果包缺少带版面信息的 Middle JSON 或 content_list.json")
    rank, name = choices[0]
    if len(choices) > 1 and choices[1][0] == rank:
        raise ValueError("MinerU 结果包包含多份论文的版面数据")
    if archive.getinfo(name).file_size > _MAX_JSON:
        raise ValueError("MinerU 版面数据过大")
    data = json.loads(archive.read(name))
    folder = PurePosixPath(name).parent
    if rank == 1 and isinstance(data, list):
        pages = {}
        for node in data:
            pages.setdefault(node["page_idx"], []).append(node)
        return "flat", [{"page_idx": i, "nodes": values} for i, values in pages.items()], folder
    if isinstance(data, dict) and isinstance(data.get("pdf_info"), list):
        return "legacy", data["pdf_info"], folder
    if isinstance(data, dict) and data.get("schema", "docvortex.middle") == "docvortex.middle" and isinstance(data.get("pages"), list):
        return "middle", data["pages"], folder
    raise ValueError("无法读取此版本的 MinerU 版面数据")


def _make_block(kind, node, box, page, ident, archive, folder, root):
    if kind.endswith(("_caption", "_footnote")) and kind != "page_footnote":
        block_type = T.CAPTION
    else:
        block_type = _TYPES.get(kind, T.PARAGRAPH)
    raw = _text(node)
    content = BlockContent(text=raw)
    warning = None
    if block_type in {T.FIGURE, T.TABLE, T.EQUATION_BLOCK}:
        spans = [s for line in node.get("lines", []) for s in line.get("spans", [])]
        if block_type is T.TABLE:
            table = node.get("table_body") or next((s.get("html") for s in spans if s.get("html")), "") or raw
            html = table if "<table" in table.lower() else markdown_table_to_html(table)
            if html:
                content = BlockContent(html=html)
        elif block_type is T.EQUATION_BLOCK and raw:
            content = BlockContent(latex=strip_math_delimiters(raw), display=True)
        if not content.html and not content.latex:
            ref = node.get("image_path") or node.get("img_path") or next((s["image_path"] for s in spans if s.get("image_path")), "")
            image = _asset(archive, folder, ref, root)
            if not image and box:
                rect = pymupdf.Rect(*(box[i] * (page.rect.width if i % 2 == 0 else page.rect.height) for i in range(4)))
                image = _crop(page, rect, root, ident)
            if not image:
                if not raw:
                    raise ValueError(f"MinerU 第 {page.number + 1} 页的图表或公式缺少图像及位置，无法恢复")
                block_type = T.PARAGRAPH
                warning = "此区域缺少图像，已保留 MinerU 返回的文字。"
            else:
                content = BlockContent(image_path=image)
                if block_type is not T.FIGURE:
                    warning = "此区域只返回了图像，请对照原文核对。"
    elif block_type is T.LIST:
        items = node.get("list_items") or [raw]
        content = BlockContent(items=items, text="\n".join(items))
    if not (content.html or content.latex or content.image_path or (content.text or "").strip()):
        return None
    num = page.number + 1
    regions = [{"page": num, "bbox": box}] if box else []
    if not box:
        warning = warning or "此段缺少独立定位框，可按页对照。"
    heading = node.get("level") or node.get("text_level") or 2
    return Block(id=ident, type=block_type, layout_label=kind, page=num, bbox=box,
                 heading_level=min(6, max(1, int(heading))) if block_type is T.HEADING else None, content=content,
                 source=SourceInfo(method=SourceMethod.OCR, page=num, text=raw, regions=regions, raw_ref="mineru"),
                 quality=QualityInfo(needs_review=bool(warning), warnings=[warning] if warning else []))


def import_bundle(bundle, pdf, root, document_id, selected, mode):
    ir = DocumentIR(document=DocumentMeta(id=document_id, source_pdf=pdf.name,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), parser={
        "name": "mineru", "settings": {"ocr_mode": mode, "pages": selected}}))
    try:
        with ZipFile(bundle) as archive, pymupdf.open(pdf) as document:
            style, raw_pages, folder = _read_layout(archive)
            lookup = {p["page_idx"]: p for p in raw_pages}
            if len(lookup) != len(raw_pages) or any(type(i) is not int or not 0 <= i < len(document) for i in lookup):
                raise ValueError("MinerU 返回了重复或超出 PDF 范围的页码")
            ir.document.page_count = len(document)
            for num in selected:
                record = lookup.get(num - 1)
                if record is None:
                    raise ValueError(f"MinerU 返回的结果缺少第 {num} 页")
                page = document[num - 1]
                ir.pages.append(PageInfo(number=num, width=page.rect.width, height=page.rect.height,
                                         rotation=page.rotation, strategy="ocr", reading_order_method="mineru"))
                if style == "legacy":
                    size = record.get("page_size") or (page.rect.width, page.rect.height)
                    nodes = record.get("para_blocks", record.get("preproc_blocks", [])) + [
                        n for n in record.get("discarded_blocks", []) if n.get("type") in {"footnote", "page_footnote"}]
                else:
                    size = (1, 1) if style == "middle" else (1000, 1000)
                    nodes = record["blocks" if style == "middle" else "nodes"]
                if len(size) != 2 or not all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0 for v in size):
                    raise ValueError("MinerU 返回了无效页面尺寸")
                index = 0
                for node in nodes:
                    group = []
                    for kind, leaf, rect in (_flat_nodes(node) if style == "flat" else _leaf_nodes(node)):
                        block = _make_block(kind, leaf, _box(rect, *size), page, block_id(num, index), archive, folder, root)
                        if block is not None:
                            block.order, block.reading_order = index, len(ir.blocks)
                            ir.blocks.append(block)
                            group.append(block)
                            index += 1
                    media = next((b for b in group if b.type in (T.FIGURE, T.TABLE)), None)
                    if media:
                        for caption in (b for b in group if b.type is T.CAPTION):
                            caption.anchor_id, caption.anchor_kind = media.id, media.type.value
            title = next((b for b in ir.blocks if b.type is T.DOCUMENT_TITLE), None)
            if title is None:
                # Older layouts use "title" for both paper and section headings.
                first = next((b for b in ir.blocks if b.page == 1 and b.layout_label == "title"), None)
                if first and not re.match(r"^(?:\d+\b|abstract\b|summary\b|introduction\b|preface\b|contents\b|摘要|引言|序言)", first.content.text, re.I):
                    first.type, first.heading_level = T.DOCUMENT_TITLE, None
                    title = first
            ir.document.title = title.content.text if title else (document.metadata.get("title") or pdf.stem)
    except (BadZipFile, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f"MinerU 返回的结果包不完整或格式异常（{type(exc).__name__}）") from None
    return ir, []


def parse_document(pdf, root, document_id, pages, mode, connection, *, page_count, report):
    from app.parsing.mineru_client import fetch_bundle
    with tempfile.TemporaryDirectory(prefix="mineru-", dir=root / "raw") as staging:
        archive = Path(staging) / "result.zip"
        fetch_bundle(pdf, archive, mode, connection, page_count=page_count, report=report)
        report("normalizing", len(pages), "正在整理 MinerU 段落、图表和 PDF 坐标")
        return import_bundle(archive, pdf, root, document_id, pages, mode)
