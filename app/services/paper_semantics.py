"""Recover paper semantics from saved evidence. Never calls OCR or a provider.

Official JSON stays immutable. Coordinates come from its group_id, heading
splits from PDF typography, and table corrections require native corroboration.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

from app.models.ir import Block, BlockType
from app.parsing.page_strategy import normalize_bbox


def _normalized(text):
    return "".join(c.lower() for c in unicodedata.normalize("NFKD", text) if c.isalnum())


def _warn(block, message):
    if message not in block.quality.warnings:
        block.quality.warnings.append(message)
    block.quality.needs_review = True


def formula_issues(latex):
    """Conservative trigger for region re-recognition, not mathematical validation."""
    issues = []
    if re.search(r"[\u4e00-\u9fff]", latex):
        issues.append("公式含异常汉字，请核对原图")
    if re.search(r"\\[\[\]()]", latex):
        issues.append("公式内部残留数学定界符")
    if len(re.findall(r"(?<!\\)\{", latex)) != len(re.findall(r"(?<!\\)\}", latex)):
        issues.append("公式花括号不配对")
    if re.search(r"(?<!\\)\\left\b", latex) and latex.count(r"\left") != latex.count(r"\right"):
        issues.append("公式伸缩括号不配对")
    return issues


def _groups(blocks, raw):
    lookup = {}
    for key, value in raw.items():
        number = re.search(r"(\d+)$", key)
        page = value.get("res", value)
        if not number or not page.get("width") or not page.get("height"):
            continue
        groups = defaultdict(list)
        entries = page.get("parsing_res_list", [])
        for entry in entries:
            bbox = entry.get("block_bbox", entry.get("bbox", []))
            if len(bbox) != 4:
                continue
            box = normalize_bbox(bbox, page["width"], page["height"])
            group = entry.get("group_id", entry.get("block_id"))
            if group is not None:
                groups[group].append({"page": int(number[1]), "bbox": box})
        for entry in entries:
            box = entry.get("block_bbox", entry.get("bbox", []))
            group = entry.get("group_id", entry.get("block_id"))
            if len(box) == 4 and group in groups:
                normalized = normalize_bbox(box, page["width"], page["height"])
                lookup[(int(number[1]), tuple(round(v, 5) for v in normalized))] = groups[group]
    for block in blocks:
        key = (block.page, tuple(round(v, 5) for v in block.bbox))
        if not block.source.regions and key in lookup:
            block.source.regions = lookup[key]


def _formula_numbers(blocks):
    for number in blocks:
        if number.layout_label != "formula_number" or len(number.bbox) != 4:
            continue
        label = unicodedata.normalize("NFKC",number.content.text or "").strip().strip("() ")
        if not re.fullmatch(r"[A-Za-z]?\d+[A-Za-z]?", label):
            continue
        candidates = []
        for formula in blocks:
            if formula.type is not BlockType.EQUATION_BLOCK or formula.page != number.page or len(formula.bbox) != 4:
                continue
            overlap = min(formula.bbox[3], number.bbox[3]) - max(formula.bbox[1], number.bbox[1])
            # Reject another column or a formula on a different line.
            gap = max(0, number.bbox[0] - formula.bbox[2], formula.bbox[0] - number.bbox[2])
            same_column = (number.bbox[0] < .5) == ((formula.bbox[0]+formula.bbox[2])/2 < .5)
            if overlap >= -.006 and gap < .42 and same_column and number.bbox[2] > (formula.bbox[0]+formula.bbox[2])/2:
                candidates.append((gap + abs((formula.bbox[1]+formula.bbox[3]-number.bbox[1]-number.bbox[3])/2), formula))
        if candidates:
            closest = min(candidates, key=lambda item: item[0])[1]
            if not closest.content.note:
                closest.content.note = label


def _abstracts(blocks):
    positions = [i for i,b in enumerate(blocks) if b.type is BlockType.ABSTRACT]
    if len(positions) < 2:
        return
    start, end = positions[0], positions[-1]
    between = blocks[start:end+1]
    if len({b.page for b in between}) == 1 and all(b.type in (BlockType.ABSTRACT, BlockType.PARAGRAPH) for b in between):
        for block in between:
            block.type = BlockType.ABSTRACT


def _split_heading(block, page):
    if block.type is not BlockType.PARAGRAPH or len(block.bbox) != 4 or not (block.content.text or "").strip():
        return [block]
    import fitz
    rect = fitz.Rect(*[block.bbox[i]*[page.rect.width,page.rect.height][i%2] for i in range(4)])
    lines = [line for region in page.get_text("dict", clip=rect).get("blocks", []) for line in region.get("lines", [])]
    heading = []
    for line in lines:
        spans = [s for s in line["spans"] if s["text"].strip()]
        bold = spans and sum(len(s["text"]) for s in spans if s["flags"] & 16) >= sum(len(s["text"]) for s in spans)*.8
        if not bold:
            break
        heading.append(line)
    if not heading or len(heading) == len(lines):
        return [block]
    text = " ".join(s["text"] for line in heading for s in line["spans"]).strip()
    wanted = _normalized(text)
    if not 8 <= len(wanted) <= 180:
        return [block]
    original = block.content.text or ""
    mapped = []
    chars = []
    for i,char in enumerate(original):
        for normalized in _normalized(char):
            chars.append(normalized); mapped.append(i)
    if not "".join(chars).startswith(wanted) or len(chars) < len(wanted)+40:
        return [block]
    boundary = mapped[len(wanted)-1]+1
    title = block.model_copy(deep=True)
    title.id += "-heading"
    title.type = BlockType.HEADING
    title.layout_label = "pdf_verified_heading"
    title.heading_level = 2
    title.content.text = original[:boundary].strip()
    title.bbox = [rect.x0/page.rect.width,min(l["bbox"][1] for l in heading)/page.rect.height,
                  rect.x1/page.rect.width,max(l["bbox"][3] for l in heading)/page.rect.height]
    title.source.regions = [{"page": block.page, "bbox": title.bbox}]
    title.source.text = title.content.text
    block.content.text = original[boundary:].lstrip(" \n\r")
    block.source.text = block.content.text
    block.bbox = [*block.bbox[:1],title.bbox[3],*block.bbox[2:]]
    if len(block.source.regions) == 1:
        block.source.regions = [{"page": block.page, "bbox": block.bbox}]
    return [title, block]


def _table(block, page):
    if block.type is not BlockType.TABLE or not block.content.html:
        return
    try:
        table = ET.fromstring(block.content.html)
    except ET.ParseError:
        return
    rows = list(table.iter("tr"))
    cells = [[c for c in row if c.tag in ("td", "th")] for row in rows]
    values = [["".join(c.itertext()).strip() for c in row] for row in cells]
    if not values or len(values[0]) < 4:
        return
    width = max(map(len,values))
    expected_header = values[0][:-1] if len(values[0]) == width and not values[0][-1] else values[0]
    if width != len(expected_header)+1 or any(len(row)!=width for row in values[1:]):
        return
    if any(c.attrib.get("colspan") or c.attrib.get("rowspan") for row in cells for c in row):
        return
    # Never delete a legitimate repeated data column based on equal values alone.
    # Require the source PDF's first row to match every remaining header token.
    if page is None or len(block.bbox) != 4:
        _warn(block, "表格末列疑似重复，缺少 PDF 文本证据，未自动删除")
        return
    import fitz
    box = block.bbox
    rectangle = fitz.Rect(box[0]*page.rect.width, box[1]*page.rect.height,
                          box[2]*page.rect.width, (box[1]+(box[3]-box[1])/len(rows)*1.1)*page.rect.height)
    words = page.get_text("words", clip=rectangle)
    if not words:
        _warn(block, "表格末列疑似重复，未找到可验证的 PDF 表头")
        return
    top = min(w[1] for w in words)
    header = [w[4] for w in sorted((w for w in words if w[1] < top+4),key=lambda w:w[0])]
    if [_normalized(v) for v in header] != [_normalized(v) for v in expected_header]:
        _warn(block, "表格末列疑似重复，PDF 表头不一致，未自动删除")
        return
    # Extract independently from native PDF positions, retaining OCR's rich
    # parameter/unit cells. Require *every* numeric row to differ by exactly one
    # duplicated adjacent cell. A blank header alone is never permission to drop.
    found = page.find_tables(clip=fitz.Rect(box[0]*page.rect.width,box[1]*page.rect.height,box[2]*page.rect.width,box[3]*page.rect.height),strategy="text")
    evidence = None
    for candidate in found.tables:
        extracted = candidate.extract()
        nonempty = [i for i,row in enumerate(extracted) if any(v for v in row)]
        grid = [extracted[i] for i in nonempty]
        if len(grid) != len(values) or any(len(row) != len(header) for row in grid):
            continue
        if [_normalized(v or "") for v in grid[0]] != [_normalized(v) for v in header]:
            continue
        compact = lambda row: [re.sub(r"\s+","",v or "") for v in row]
        verified = True
        for row_index,(old,new) in enumerate(zip(values[1:],grid[1:]),1):
            left,right = compact(old[2:]),compact(new[2:])
            def duplicate_only(target):
                return any(left[:i]+left[i+1:]==target and ((i>0 and left[i]==left[i-1]) or (i+1<len(left) and left[i]==left[i+1])) for i in range(len(left)))
            if not duplicate_only(right):
                # Long values such as 9.8 (2.9) can cross inferred native cell
                # boundaries. Validate them against the complete native row.
                native_row = candidate.rows[nonempty[row_index]].bbox
                row_words = sorted((w for w in page.get_text("words") if native_row[1] <= (w[1]+w[3])/2 <= native_row[3] and rectangle.x0<=w[0]<=rectangle.x1),key=lambda w:w[0])
                tokens = re.findall(r"[-−]?\d+(?:\.\d+)?(?:\s*\(\s*\d+(?:\.\d+)?\s*\))?", " ".join(w[4] for w in row_words))
                right = compact(tokens[-(len(header)-2):])
            if not duplicate_only(right):
                verified = False; break
            grid[row_index] = [*new[:2],*right]
        if verified:
            evidence = grid; break
    if evidence is None:
        _warn(block,"表格列数异常，原 PDF 数据无法逐行验证，未自动修正")
        return
    for i,(row,row_cells) in enumerate(zip(rows,cells)):
        if i == 0:
            if len(row_cells)>len(header): row.remove(row_cells[-1])
            continue
        for cell in row_cells[2:]:
            row.remove(cell)
        for value in evidence[i][2:]:
            cell = ET.SubElement(row,"td"); cell.text = value or ""
    block.content.html = ET.tostring(table,encoding="unicode",short_empty_elements=False)
    block.content.note = "已用原 PDF 表头和逐行数值验证并修正重复列"


def prepare_blocks(blocks: list[Block], data: dict, doc_dir: Path, raw: dict | None = None) -> list[Block]:
    blocks = [b.model_copy(deep=True) for b in sorted(blocks,key=lambda b:(b.page,b.order))]
    if raw is None:
        path = doc_dir / "raw/parser-output.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    _groups(blocks, raw)
    _formula_numbers(blocks)
    _abstracts(blocks)
    document = None
    try:
        if (doc_dir / "original.pdf").is_file():
            import fitz
            document = fitz.open(doc_dir / "original.pdf")
        from app.services.inline_semantics import recover_inline_prose
        blocks = recover_inline_prose(blocks,document)
        from app.services.prose_quality import repair_pdf_citations
        blocks = repair_pdf_citations(blocks,document)
        output = []
        for block in blocks:
            page = document[block.page-1] if document and 1 <= block.page <= len(document) else None
            if block.type is BlockType.TABLE:
                _table(block,page)
            if block.type in (BlockType.EQUATION_BLOCK,BlockType.EQUATION_INLINE):
                for issue in formula_issues(block.content.latex or ""):
                    _warn(block, issue)
            output.extend(_split_heading(block,page) if page else [block])
        for order, block in enumerate(output):
            block.order = order
        return output
    finally:
        if document:
            document.close()
