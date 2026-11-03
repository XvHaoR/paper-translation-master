"""Bounded element retries, separate from semantic repair and HTML rendering."""
from __future__ import annotations

import re
from pathlib import Path

from app.models.ir import BlockType
from app.rendering.markdown_util import strip_math_delimiters
from app.services.paper_semantics import formula_issues


def repair_formula_regions(blocks, pdf_path: Path, recognizer, output: Path, progress=None):
    """Retry only structurally suspicious formula crops, retaining raw evidence.

    This runs during OCR, never when opening a saved page. A candidate replaces
    old text only after basic syntax checks; it is not a mathematical proof.
    """
    import fitz
    output.mkdir(parents=True,exist_ok=True)
    repairs = []
    with fitz.open(pdf_path) as pdf:
        candidates = [b for b in blocks if b.type is BlockType.EQUATION_BLOCK and formula_issues(b.content.latex or "")]
        for index,block in enumerate(candidates[:24],1):
            if len(block.bbox)!=4 or not 1<=block.page<=len(pdf):
                continue
            if progress: progress(index,len(candidates),block.id)
            page = pdf[block.page-1]
            rect = fitz.Rect(*[block.bbox[i]*[page.rect.width,page.rect.height][i%2] for i in range(4)])
            rect = (rect + (-2,-2,2,2)) & page.rect
            image = output / (block.id+".png")
            page.get_pixmap(matrix=fitz.Matrix(4,4),clip=rect,alpha=False).save(image)
            original = block.content.latex or ""
            try:
                candidate = strip_math_delimiters(recognizer.recognize_region(image,"formula"))
                fallback = getattr(recognizer,"recognize_region_native",None)
                if (formula_issues(candidate) or candidate.count('\ufffd')>=3) and getattr(recognizer,"vl_backend","") and callable(fallback):
                    candidate = strip_math_delimiters(fallback(image,"formula"))
                if candidate.count('\ufffd')>=3 or re.search(r'(\S.{0,12})\1{20,}',candidate):
                    raise RuntimeError("OCR 推理输出退化，已停止局部复核；原结果未改写")
                # Edge delimiter damage can be removed without changing symbols.
                candidate = re.sub(r"^(?:\\\[|\\\()\s*|\s*(?:\\\]|\\\))$", "", candidate).strip()
                accepted = bool(candidate) and not formula_issues(candidate) and len(candidate)<max(800,len(original)*4)
                row = {"block_id":block.id,"page":block.page,"before":original,"candidate":candidate,"accepted":accepted}
                if accepted:
                    if block.source.text is None: block.source.text = original
                    block.content.latex = candidate
                    block.quality.warnings = [w for w in block.quality.warnings if w not in formula_issues(original)]
                    block.quality.needs_review = bool(block.quality.warnings)
                else:
                    block.quality.needs_review = True
                repairs.append(row)
            except Exception as error:
                repairs.append({"block_id":block.id,"before":original,"accepted":False,"error":str(error)})
                if "局部复核中止："+str(error) not in block.quality.warnings:
                    block.quality.warnings.append("局部复核中止："+str(error))
                block.quality.needs_review = True
                break  # Circuit breaker: don't spend a full document on an unhealthy backend.
    return repairs
