"""原生文本提取：PyMuPDF 读取 PDF 内嵌文本与图片。

只做“读取”与“排序”，不做语义归并（归并在规范化层）。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class NativeLine:
    """页面内的一行文本。"""

    text: str
    bbox: tuple[float, float, float, float]  # 点坐标
    size: float
    font: str
    flags: int = 0  # PyMuPDF span flags：bit 0 加粗、bit 3 等宽等
    superscript: bool = False


@dataclass
class NativeBlock:
    """页面内的一块原生文本。"""

    bbox: tuple[float, float, float, float]
    lines: list[NativeLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)

    def column_index(self, columns: list[float]) -> int:
        """按块中心 x 归入某一列（列边界列表）。"""
        cx = (self.bbox[0] + self.bbox[2]) / 2
        for i in range(len(columns) - 1):
            if cx < (columns[i] + columns[i + 1]) / 2:
                return i
        return len(columns) - 1


def detect_columns(blocks: list[NativeBlock], page_width: float) -> list[float]:
    """检测页面列边界。

    按块中心 x 聚类：中心 x 之间差距 > 页面宽度 18% 视为不同列。
    """
    if not blocks:
        return [0.0, page_width]
    centers = sorted((b.bbox[0] + b.bbox[2]) / 2 for b in blocks)
    clusters: list[list[float]] = [[centers[0]]]
    gap = page_width * 0.18
    for c in centers[1:]:
        if c - clusters[-1][-1] > gap:
            clusters.append([c])
        else:
            clusters[-1].append(c)
    bounds = [0.0]
    for i in range(len(clusters) - 1):
        bounds.append((clusters[i][-1] + clusters[i + 1][0]) / 2)
    bounds.append(page_width)
    return bounds


def sort_blocks_reading_order(
    blocks: list[NativeBlock], columns: list[float], page_width: float
) -> list[NativeBlock]:
    """按阅读顺序排序：列主序 + 通栏块按纵向位置插入。

    双栏页的正确顺序是“左栏从上到下，再右栏从上到下”（列主序）；
    通栏块（标题、跨栏公式）按其 y 位置插入到第一个纵向位置更靠后的
    栏内块之前。
    """
    if not blocks:
        return []

    span_min_width = page_width * 0.6

    def col_of(bl: NativeBlock) -> int:
        cx = (bl.bbox[0] + bl.bbox[2]) / 2
        for i in range(len(columns) - 1):
            if columns[i] <= cx <= columns[i + 1]:
                return i
        return len(columns) - 1

    span_blocks = [b for b in blocks if (b.bbox[2] - b.bbox[0]) >= span_min_width]
    col_blocks = [b for b in blocks if (b.bbox[2] - b.bbox[0]) < span_min_width]
    col_blocks.sort(key=lambda b: (col_of(b), b.bbox[1]))
    span_blocks.sort(key=lambda b: b.bbox[1])

    out: list[NativeBlock] = list(col_blocks)
    for sb in span_blocks:
        insert_at = 0
        for i, cb in enumerate(out):
            if cb.bbox[1] > sb.bbox[1] + 1e-6:
                insert_at = i
                break
        else:
            insert_at = len(out)
        out.insert(insert_at, sb)
    return out


def _line_from_span(span: dict, line_bbox: tuple) -> NativeLine:
    bbox = tuple(float(v) for v in line_bbox)
    return NativeLine(
        text=span["text"],
        bbox=bbox,
        size=float(span.get("size", 0)),
        font=span.get("font", ""),
        flags=int(span.get("flags", 0)),
        superscript=bool(int(span.get("flags", 0)) & 1),
    )


class NativePdfExtractor:
    """从 PDF 提取原生文本块、图片与页面信息。"""

    def __init__(self, pdf_path: str | Path) -> None:
        self.pdf_path = str(pdf_path)
        self.doc = fitz.open(self.pdf_path)
        if self.doc.needs_pass:
            raise ValueError("PDF 已加密，需要密码")

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "NativePdfExtractor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def page_size(self, page_no: int) -> tuple[float, float]:
        """第 page_no 页（1-based）的点尺寸。"""
        page = self.doc[page_no - 1]
        r = page.rect
        return float(r.width), float(r.height)

    def page_text_stats(self, page_no: int) -> dict:
        """页面文本可提取性统计，供预检使用。"""
        page = self.doc[page_no - 1]
        text = page.get_text("text")
        chars = len(text.strip())
        d = page.get_text("dict")
        image_area = 0.0
        total_area = max(page.rect.width * page.rect.height, 1.0)
        for block in d.get("blocks", []):
            if block.get("type") == 1:  # 图片块
                image_area += abs(
                    (block["bbox"][2] - block["bbox"][0])
                    * (block["bbox"][3] - block["bbox"][1])
                )
        return {
            "chars": chars,
            "image_ratio": image_area / total_area,
            "rotation": int(page.rotation or 0),
        }

    def extract_text_blocks(self, page_no: int) -> tuple[list[NativeBlock], list[float]]:
        """提取第 page_no 页文本块，按列、列内按 y 排序。"""
        page = self.doc[page_no - 1]
        d = page.get_text("dict")
        blocks: list[NativeBlock] = []
        for b in d.get("blocks", []):
            if b.get("type") != 0:  # 只处理文本块
                continue
            lines: list[NativeLine] = []
            for line in b.get("lines", []):
                line_bbox = line["bbox"]
                for span in line.get("spans", []):
                    if not span["text"].strip():
                        continue
                    lines.append(_line_from_span(span, line_bbox))
            if lines:
                blocks.append(
                    NativeBlock(bbox=tuple(float(v) for v in b["bbox"]), lines=lines)
                )
        if not blocks:
            return [], [0.0, float(page.rect.width)]

        columns = detect_columns(blocks, float(page.rect.width))
        blocks = sort_blocks_reading_order(blocks, columns, float(page.rect.width))
        return blocks, columns

    def extract_images(self, page_no: int, out_dir: Path) -> list[dict]:
        """提取页面内嵌图片并保存，返回元信息列表。"""
        page = self.doc[page_no - 1]
        results: list[dict] = []
        seen: set[int] = set()
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                img = page.extract_image(xref)
            except Exception:
                logger.warning("页面 %d 图片 xref=%d 提取失败", page_no, xref)
                continue
            ext = (img.get("ext") or "png").lower()
            if ext not in ("png", "jpg", "jpeg", "webp", "bmp"):
                ext = "png"
            name = f"p{page_no:03d}-img{xref}.{ext}"
            (out_dir / name).write_bytes(img["image"])
            results.append(
                {
                    "path": name,
                    "xref": xref,
                    "width": img.get("width", 0),
                    "height": img.get("height", 0),
                    "page": page_no,
                }
            )
        return results

    @staticmethod
    def content_hash(pdf_path: str | Path) -> str:
        """PDF 内容哈希（用于文档 ID 与缓存）。"""
        h = hashlib.sha256()
        with open(pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
