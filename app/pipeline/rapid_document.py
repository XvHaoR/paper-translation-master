"""Adapt the complete RapidDoc output to the stable, coordinate-preserving IR.

No text-only shortcut or silent fallback that drops formulas and tables.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import time
import fitz

from app.models.ir import Block, BlockContent, BlockType, DocumentIR, DocumentMeta, PageInfo, SourceInfo, SourceMethod, block_id
from app.parsing.rapidocr_backend import RapidDocumentRecognizer
from app.parsing.page_strategy import map_label_to_block_type
from app.pipeline.page_renderer import PageRenderer

CACHE_VERSION = "rapidocr-full-layout-formula-table-v1"
LIMITATIONS = "RapidOCR 完整版已启用版面、行内/行间公式、表格结构识别；识别准确度仍需结合原文核对。"
DROP = {"discarded", "header", "footer", "page_number", "number"}
TYPE_MAP = {"text": BlockType.PARAGRAPH, "title": BlockType.HEADING, "list": BlockType.LIST,
    "index": BlockType.PARAGRAPH, "interline_equation": BlockType.EQUATION_BLOCK,
    "image": BlockType.FIGURE, "image_body": BlockType.FIGURE, "table": BlockType.TABLE,
    "table_body": BlockType.TABLE, "image_caption": BlockType.CAPTION, "table_caption": BlockType.CAPTION,
    "image_footnote": BlockType.FOOTNOTE, "table_footnote": BlockType.FOOTNOTE, "footnote": BlockType.FOOTNOTE}

def normalize_box(box, size):
    if len(box or []) != 4 or min(size) <= 0:
        return []
    return [max(0., min(1., float(value) / size[i % 2])) for i, value in enumerate(box)]

def spans(block):
    return [span for line in block.get("lines", []) for span in line.get("spans", [])]

def paragraph_text(block):
    lines = []
    for line in block.get("lines", []):
        parts = []
        for span in line.get("spans", []):
            text = str(span.get("content", "")).strip()
            if span.get("type") in {"inline_equation", "interline_equation"} and text:
                text = "$" + text.strip("$") + "$"
            if text:
                parts.append(text)
        lines.append(" ".join(parts))
    return re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", "\n".join(lines)).replace("\n", " ").strip()

class RapidDocumentConverter:
    def __init__(self, recognizer=None):
        self.recognizer = recognizer or RapidDocumentRecognizer()

    def convert(self, pdf_path, doc_dir, pre, pages, opts, report):
        ir = DocumentIR(document=DocumentMeta(id=pre.pdf_hash, source_pdf=Path(pdf_path).name,
            page_count=pre.page_count, parser={"name": "rapidocr", "version": self.recognizer.version,
                "cache_version": CACHE_VERSION, "features": ["layout", "inline-math", "display-math", "tables", "figures"],
                "settings": {"ocr_mode": "rapidocr", "render_dpi": opts.render_dpi, "pages": pages}},
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
        with fitz.open(pdf_path) as pdf, PageRenderer(str(pdf_path), render_dpi=opts.render_dpi) as renderer:
            # Small windows give cancellation/progress boundaries without
            # reloading models. Map sliced pages back to their original numbers.
            for offset in range(0, len(pages), 4):
                window = pages[offset:offset+4]
                report("recognizing", window[0], f"RapidOCR 完整识别 · 第 {window[0]}–{window[-1]}/{pre.page_count} 页（含公式与表格）")
                with fitz.open() as batch:
                    for number in window:
                        batch.insert_pdf(pdf, from_page=number-1, to_page=number-1)
                    output = self.recognizer.recognize(batch.tobytes())
                page_data = (output.middle_json or {}).get("pdf_info", [])
                if len(page_data) != len(window):
                    raise RuntimeError("完整识别结果缺页，未将不完整结果标记为成功")
                image_map = self.save_images(output.images, doc_dir)
                for local, page_result in enumerate(page_data):
                    number = window[local]
                    source_page = pdf[number-1]
                    size = page_result.get("page_size") or [source_page.rect.width, source_page.rect.height]
                    preview = renderer.save_page_render(number, doc_dir / "assets/page-previews", "preview")
                    ir.pages.append(PageInfo(number=number, width=source_page.rect.width, height=source_page.rect.height,
                        rotation=source_page.rotation, strategy="hybrid", preview=f"assets/page-previews/{preview}", reading_order_method="rapid-layout-model"))
                    (doc_dir / f"raw/rapid-{number:03d}.json").write_text(json.dumps(page_result, ensure_ascii=False), encoding="utf-8")
                    page_blocks = []
                    for raw in page_result.get("para_blocks", []):
                        page_blocks.extend(self.blocks(raw, number, size, image_map))
                    if not page_blocks and source_page.get_text().strip():
                        raise RuntimeError(f"第 {number} 页没有版面内容，未回退到缺少公式/表格的纯文本结果")
                    for block in page_blocks:
                        block.order = len(ir.blocks)
                        block.id = block_id(number, block.order)
                        ir.blocks.append(block)
                report("recognizing", window[-1], f"RapidOCR 已完成 {offset+len(window)}/{len(pages)} 页")
        titles = [b.content.text for b in ir.blocks if b.type == BlockType.DOCUMENT_TITLE and b.content.text]
        ir.document.title = " ".join(titles) or Path(pdf_path).stem
        return ir

    @staticmethod
    def save_images(images, doc_dir):
        mapping = {}
        for name, data in images.items():
            suffix = Path(name).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            relative = f"assets/figures/{hashlib.sha256(data).hexdigest()[:24]}{suffix}"
            (doc_dir / relative).write_bytes(data)
            mapping[name.replace("\\", "/")] = relative
        return mapping

    def blocks(self, raw, page, size, images):
        kind = raw.get("type", "")
        if kind in DROP:
            return []
        if raw.get("blocks"):
            result = []
            for child in raw["blocks"]:
                result.extend(self.blocks(child, page, size, images))
            return result
        label = raw.get("original_label", "")
        block_type = TYPE_MAP.get(kind, BlockType.PARAGRAPH)
        semantic = map_label_to_block_type(label)
        if label and semantic != "unknown":
            block_type = BlockType(semantic)
        if block_type in {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER, BlockType.PAGE_NUMBER}:
            return []
        entries = spans(raw)
        text = paragraph_text(raw)
        box = normalize_box(raw.get("bbox"), size)
        content = None
        if block_type == BlockType.EQUATION_BLOCK:
            latex = " ".join(str(s.get("content", "")) for s in entries).strip().strip("$")
            if not latex:
                raise RuntimeError(f"第 {page} 页公式未返回 LaTeX；请重试或使用 VL 版复核")
            content = BlockContent(latex=latex, display=True)
        elif block_type == BlockType.TABLE:
            markup = next((s.get("html") for s in entries if s.get("html")), "")
            if not markup or "<table" not in markup.lower():
                raise RuntimeError(f"第 {page} 页表格未返回结构化结果；未以截图替代可导出的表格")
            content = BlockContent(html=markup)
        elif block_type == BlockType.FIGURE:
            image = next((s.get("image_path") for s in entries if s.get("image_path")), "")
            relative = images.get(image.replace("\\", "/")) or images.get("images/"+image)
            if not relative:
                raise RuntimeError(f"第 {page} 页图片资源缺失")
            content = BlockContent(image_path=relative)
        elif text:
            if block_type == BlockType.LIST:
                content = BlockContent(text=text, items=[paragraph_text({"lines": [line]}) for line in raw.get("lines", [])])
            else:
                content = BlockContent(text=text)
            if kind == "title" and (label == "doc_title" or not label and raw.get("level", 1) == 1 and page == 1):
                block_type = BlockType.DOCUMENT_TITLE
        if content is None:
            return []
        return [Block(id="", type=block_type, layout_label=label or kind, page=page, bbox=box, content=content,
            heading_level=max(1, min(6, int(raw.get("level", 2)))) if block_type == BlockType.HEADING else None,
            source=SourceInfo(method=SourceMethod.MIXED, page=page))]
