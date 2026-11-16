"""Local layout parsing from the PDF text layer, with no 解析 backend.

Coordinates, stable block IDs and original text use the existing reader IR, so
translation and both directions of navigation retain their source anchors.
"""
from __future__ import annotations

import html
import json
import math
import re
import time
import unicodedata
from pathlib import Path

import pymupdf

from app.models.ir import (Block, BlockContent, BlockType, DocumentIR, DocumentMeta,
                           PageInfo, QualityInfo, SourceInfo, SourceMethod, block_id)

from app.parsing.reading_flow import PARSER_VERSION
_SKIP = {"page-header", "page-footer"}
_TYPES = {"title": BlockType.DOCUMENT_TITLE, "section-header": BlockType.HEADING,
          "text": BlockType.PARAGRAPH, "caption": BlockType.CAPTION,
          "list-item": BlockType.LIST, "footnote": BlockType.FOOTNOTE,
          "table": BlockType.TABLE, "formula": BlockType.EQUATION_BLOCK,
          "picture": BlockType.FIGURE, "code": BlockType.CODE}


def readable_text(text: str) -> bool:
    """Assess actual Unicode, not just selectable glyphs or a page character count."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    bad = sum(c == "\ufffd" or unicodedata.category(c) in {"Co", "Cs", "Cc"} for c in chars)
    letters = sum(unicodedata.category(c)[0] in {"L", "N"} for c in chars)
    return bad / len(chars) < .03 and letters >= max(2, len(chars) * .25)


def join_lines(text: str, lexicon=None) -> str:
    text = text.replace("\u00ad", "")
    from app.parsing.reading_flow import join_text
    result = ''
    for line in text.splitlines():
        result = join_text(result, line, lexicon)
    return re.sub(r"\s+", " ", result).strip()


def _rect(page, box):
    values = [float(box[k]) for k in ("x0", "y0", "x1", "y1")]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("版面坐标无效")
    return pymupdf.Rect(*values) & page.rect


def _bbox(page, rect):
    return [max(0., min(1., value / scale)) for value, scale in
            zip(rect, (page.rect.width, page.rect.height) * 2)]


def _crop(page, rect, root, name):
    rect = (rect + (-1, -1, 1, 1)) & page.rect
    relative = f"assets/crops/{name}.png"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    # Bound rendering size even for posters and unusually large PDF pages.
    scale = min(2., 3000 / max(rect.width, rect.height, 1))
    page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False).save(target)
    return relative


def _table_html(page, table):
    rows = table.get("extract") or table.get("rows")
    if not isinstance(rows, list):
        rows = [[page.get_textbox(pymupdf.Rect(cell)) if cell else "" for cell in row]
                for row in table.get("cells", [])]
    if not rows or not all(isinstance(row, list) for row in rows):
        return None
    if not any(str(cell or "").strip() for row in rows for cell in row):
        return None
    return "<table>" + "".join("<tr>" + "".join(
        "<td>" + html.escape(str(cell or "")).replace("\n", "<br>") + "</td>"
        for cell in row) + "</tr>" for row in rows) + "</table>"


def _block(page, box, root, index, lexicon=None):
    label = box.get("boxclass", "text")
    if label in _SKIP:
        return None
    rect = _rect(page, box)
    if rect.is_empty:
        return None
    number = page.number + 1
    bid = block_id(number, index)
    kind = _TYPES.get(label, BlockType.PARAGRAPH)
    # get_textbox reconstructs inter-span spaces which JSON span concatenation loses.
    text = join_lines(page.get_textbox((rect + (-.7, -.7, .7, .7)) & page.rect), lexicon)
    content = BlockContent(text=text)
    quality = QualityInfo()
    if kind in {BlockType.FIGURE, BlockType.EQUATION_BLOCK}:
        content = BlockContent(image_path=_crop(page, rect, root, bid),
                               note="原 PDF 公式" if kind is BlockType.EQUATION_BLOCK else None)
    elif kind is BlockType.TABLE:
        table = box.get("table") or {}
        content = BlockContent(html=_table_html(page, table),
                               image_path=_crop(page, rect, root, bid),
                               rows=table.get("row_count"), cols=table.get("col_count"))
    elif not text:
        return None
    elif not readable_text(text) and len(text) > 20:
        quality = QualityInfo(needs_review=True, warnings=["此区域文字层异常，已保留原始图像；请对照 PDF 核对。"])
        kind = BlockType.FIGURE
        content = BlockContent(image_path=_crop(page, rect, root, bid))
    return Block(id=bid, type=kind, page=number, order=index, bbox=_bbox(page, rect),
                 layout_label=label, heading_level=min(6, max(2, box.get("header_level") or 2))
                 if kind is BlockType.HEADING else None, content=content, quality=quality,
                 source=SourceInfo(method=SourceMethod.NATIVE, page=number, text=text, raw_ref="native-layout",
                                   regions=[{"page": number, "bbox": _bbox(page, rect)}]))


def _preview(page, root, warning):
    bid = block_id(page.number + 1, 0)
    return Block(id=bid, type=BlockType.FIGURE, layout_label="page-preview",
                 page=page.number + 1, order=0, bbox=[0., 0., 1., 1.],
                 content=BlockContent(image_path=_crop(page, page.rect, root, bid + "-page")),
                 source=SourceInfo(page=page.number + 1),
                 quality=QualityInfo(needs_review=True, warnings=[warning]))


def _coverage(page, boxes):
    """Find text lines omitted by layout detection, instead of silently dropping them."""
    regions = [_rect(page, b) for b in boxes]
    missing = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            rect = pymupdf.Rect(line["bbox"])
            center = (rect.tl + rect.br) / 2
            if any(center in r for r in regions):
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            if len(text) > 1:
                missing.append({"x0": rect.x0, "y0": rect.y0, "x1": rect.x1,
                                "y1": rect.y1, "boxclass": "text"})
    return missing


def _insert_missing(boxes, missing):
    # Preserve the layout engine's region/column order. Anchor recovered text to
    # the nearest vertically preceding block in its own horizontal region.
    boxes = list(boxes)
    for extra in sorted(missing, key=lambda b: (b["y0"], b["x0"])):
        candidates = [(i, b) for i, b in enumerate(boxes)
                      if min(b["x1"], extra["x1"]) - max(b["x0"], extra["x0"]) > 0
                      and b["y1"] <= extra["y0"] + 2]
        index = max(candidates, key=lambda pair: pair[1]["y1"])[0] + 1 if candidates else 0
        boxes.insert(index, extra)
    return boxes


def parse_document(pdf_path, root, document_id, pages, *, report=lambda *args: None):
    ir = DocumentIR(document=DocumentMeta(id=document_id, source_pdf=Path(pdf_path).name,
        parser={"name": "pymupdf-layout", "version": pymupdf.VersionBind,
                "auto_parser_version": PARSER_VERSION,
                "settings": {"ocr_mode": "auto", "pages": pages}},
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
    warnings, difficult, rotations = [], [], {}
    with pymupdf.open(pdf_path) as document:
        from app.parsing.line_numbers import remove_line_numbers
        from app.parsing.reading_flow import reconstruct, vocabulary
        # Filter before both layout inference and omitted-line recovery. Doing
        # this after translation cannot repair the polluted paragraph boundaries.
        filtered = remove_line_numbers(document, pages)
        ir.document.parser["line_number_filter"] = {"version": 1, "pages": filtered,
                                                    "removed": sum(filtered.values())}
        lexicon = vocabulary('\n'.join(document[n-1].get_text() for n in pages))
        ir.document.page_count = len(document)
        for number in pages:
            report("layout_analysis", number, f"自动解析第 {number}/{len(document)} 页：文字与版面")
            page = document[number - 1]
            rotation = page.rotation
            width, height = page.rect.width, page.rect.height
            rotations[number] = rotation
            # Analyze the original text coordinate system; map anchors back to
            # the displayed PDF orientation after parsing. Never save this edit.
            if rotation:
                page.set_rotation(0)
            native = page.get_text()
            chars = len(native.strip())
            image_area = sum((pymupdf.Rect(i["bbox"]) & page.rect).get_area() for i in page.get_image_info())
            scan = image_area > page.rect.get_area() * .5 and chars < 100
            if (chars and not readable_text(native)) or scan or (not chars and (page.get_images() or page.get_drawings())):
                difficult.append(number)
                blocks = [_preview(page, root, "此页没有可靠文字层，已保留原页；本版本不识别图片中的文字。")]
            else:
                try:
                    data = json.loads(document.to_json(pages=[number - 1], use_ocr=False))
                    boxes = data["pages"][0]["boxes"]
                    missing = _coverage(page, boxes)
                    boxes = _insert_missing(boxes, missing)
                    boxes = [b for b in boxes if b.get("boxclass") != "footnote"] + [b for b in boxes if b.get("boxclass") == "footnote"]
                    blocks = [b for i, box in enumerate(boxes) if (b := _block(page, box, root, i, lexicon))]
                    if any(b.quality.needs_review for b in blocks):
                        difficult.append(number)
                    if chars and not blocks:
                        raise ValueError("未提取到正文区域")
                    if missing:
                        warnings.append(f"第 {number} 页补回 {len(missing)} 行未归类文字，请核对局部顺序。")
                        for block in blocks:
                            block.quality.needs_review = True
                            block.quality.warnings = ["此页补回了未归类文字，请核对局部顺序。"]
                except Exception as error:
                    # Keep the source visible and make the incomplete parse explicit.
                    blocks = [_preview(page, root, "此页版面分析未完成，已保留原页，请对照 PDF 阅读。")]
                    difficult.append(number)
                    warnings.append(f"第 {number} 页版面分析异常：{type(error).__name__}")
            ir.pages.append(PageInfo(number=number, width=width, height=height,
                                     rotation=rotation, strategy="native",
                                     reading_order_method="pymupdf-layout"))
            ir.blocks.extend(blocks)
        if difficult:
            warnings.append("以下页面含无法提取的内容，已保留原页或区域图像：" + "、".join(map(str, difficult)))
        ir.blocks = reconstruct(ir.blocks, lexicon)
    def rotate(box, rotation):
        if not rotation:
            return box
        points = [(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
        transformed = [(1-y, x) if rotation == 90 else (1-x, 1-y) if rotation == 180 else (y, 1-x) for x, y in points]
        return [min(x for x,y in transformed), min(y for x,y in transformed), max(x for x,y in transformed), max(y for x,y in transformed)]
    for block in ir.blocks:
        block.bbox = rotate(block.bbox, rotations.get(block.page, 0))
        for region in block.source.regions:
            region["bbox"] = rotate(region["bbox"], rotations.get(region['page'], 0))
    # First-page title detection is based on the layout heading and its font size,
    # not a publisher template. Existing document titles always take precedence.
    if not any(b.type is BlockType.DOCUMENT_TITLE for b in ir.blocks):
        first = next((b for b in ir.blocks if b.page == 1 and b.type is BlockType.HEADING), None)
        if first and not re.match(r"^(abstract|摘要|\d+[.\s])", first.content.text or "", re.I):
            first.type, first.heading_level = BlockType.DOCUMENT_TITLE, None
    title = next((b.content.text for b in ir.blocks if b.type is BlockType.DOCUMENT_TITLE), None)
    ir.document.title = title or Path(pdf_path).stem
    return ir, warnings
