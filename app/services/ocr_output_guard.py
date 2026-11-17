"""Keep the original PDF region when OCR loops on empty LaTeX commands."""
from __future__ import annotations

import hashlib
from html import unescape
import math
import re

from app.models.ir import BlockContent, BlockType, SourceMethod

_WRAPPER = re.compile(r"\\(?:mathrm|mathbf|mathit|text|operatorname)\b")
_PLACEHOLDER = "此区域的 OCR 结果异常，请对照 PDF 原文。"
_WARNING = "识别返回大量重复的空公式命令，已保留原 PDF 区域"


def repetitive_empty_math(value: str | None) -> bool:
    r"""Detect a run of formatting commands with almost no actual content.

    Long chemical equations can contain many \mathrm groups with meaningful
    arguments, so the number of macros alone is insufficient.
    """
    if not value or len(value) < 240:
        return False
    text = unescape(re.sub(r"<[^<>]*>", "", value))
    rest, count = _WRAPPER.subn("", text)
    if count < 24:
        return False
    meaningful = re.sub(r"[\s{}~$\\_^()\[\]&,.|;:]+", "", rest)
    return len(meaningful) <= max(12, len(text) // 100)


def _capture(page, box, doc_dir):
    import fitz

    if len(box) != 4 or not all(math.isfinite(value) for value in box):
        return None
    width, height = page.rect.width, page.rect.height
    clip = fitz.Rect(box[0] * width, box[1] * height, box[2] * width, box[3] * height) & page.rect
    if clip.is_empty or min(clip.width, clip.height) < 1:
        return None

    # For raster figures, trust the PDF's image rectangle when it agrees with
    # the OCR region. This keeps the edges and axes of a composite plot intact.
    best, best_overlap = clip, .55
    for item in page.get_image_info():
        original = (fitz.Rect(item["bbox"]) * page.rotation_matrix) & page.rect
        overlap = (original & clip).get_area()
        union_area = original.get_area() + clip.get_area() - overlap
        if union_area and overlap / union_area > best_overlap:
            best, best_overlap = original, overlap / union_area
    scale = min(2.5, 2400 / max(best.width, best.height))
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=best, alpha=False)
    data = pixmap.tobytes("png")
    path = doc_dir / "assets" / "crops" / (hashlib.sha256(data).hexdigest()[:24] + ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path.relative_to(doc_dir).as_posix(), [best.x0 / width, best.y0 / height, best.x1 / width, best.y1 / height]


def guard_ocr_blocks(blocks, document, doc_dir):
    """Replace only clearly degenerate blocks; the raw OCR JSON is untouched."""
    for block in blocks:
        if not any(repetitive_empty_math(value) for value in
                   (block.content.latex, block.content.text, block.content.html)):
            continue
        recovered = None
        if document is not None and 1 <= block.page <= len(document):
            try:
                recovered = _capture(document[block.page - 1], block.bbox, doc_dir)
            except (OSError, RuntimeError, ValueError):
                # Unreadable PDF regions should not fail an otherwise complete paper.
                recovered = None
        block.quality.needs_review = True
        block.source.text = None
        if recovered:
            relative, box = recovered
            block.type = BlockType.FIGURE
            block.bbox = box
            block.content = BlockContent(image_path=relative)
            block.source.method = SourceMethod.MIXED
            block.source.crop = relative
            block.source.regions = [{"page": block.page, "bbox": box}]
            block.quality.warnings.append(_WARNING)
        else:
            block.type = BlockType.PARAGRAPH
            block.content = BlockContent(text=_PLACEHOLDER)
            block.quality.warnings.append("识别返回大量重复的空公式命令，未能裁取原 PDF 区域")
        block.layout_label = "ocr_region_fallback"
    return blocks
