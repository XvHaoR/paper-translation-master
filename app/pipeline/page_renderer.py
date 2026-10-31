"""页面渲染：按 DPI 渲染页面、保存预览、区域裁剪，页面哈希用于缓存。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)


class PageRenderer:
    """基于 PyMuPDF 的页面渲染器。"""

    def __init__(
        self,
        pdf_path: str,
        render_dpi: int = 220,
        high_dpi: int = 300,
        preview_dpi: int = 72,
        max_pixels: int = 40_000_000,
    ) -> None:
        self.pdf_path = pdf_path
        self.render_dpi = render_dpi
        self.high_dpi = high_dpi
        self.preview_dpi = preview_dpi
        self.max_pixels = max_pixels
        self._doc = fitz.open(pdf_path)

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> "PageRenderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _dpi_for(self, page_no: int) -> int:
        """普通页 220 DPI；公式密集或小字号页可提升（当前由选项控制）。"""
        return self.render_dpi

    def render_page(self, page_no: int, dpi: int | None = None) -> bytes:
        """渲染整页为 PNG 字节。"""
        dpi = dpi or self._dpi_for(page_no)
        page = self._doc[page_no - 1]
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        if pix.width * pix.height > self.max_pixels:
            # 超大页面限制，按最大像素等比缩小
            scale = (self.max_pixels / (pix.width * pix.height)) ** 0.5
            pix = page.get_pixmap(
                matrix=fitz.Matrix(zoom * scale, zoom * scale), alpha=False
            )
        return pix.tobytes("png")

    def render_preview(self, page_no: int) -> bytes:
        """低分辨率预览。"""
        return self.render_page(page_no, self.preview_dpi)

    def crop_region(
        self, page_no: int, bbox: list[float], dpi: int | None = None
    ) -> bytes:
        """按归一化 bbox [x0,y0,x1,y1] 裁剪区域为 PNG。"""
        dpi = dpi or self.high_dpi
        page = self._doc[page_no - 1]
        pw, ph = page.rect.width, page.rect.height
        rect = fitz.Rect(
            bbox[0] * pw, bbox[1] * ph, bbox[2] * pw, bbox[3] * ph
        ).intersect(page.rect)
        if rect.is_empty:
            raise ValueError(f"页面 {page_no} 裁剪区域为空：{bbox}")
        zoom = dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False
        )
        return pix.tobytes("png")

    def save_page_render(
        self, page_no: int, out_dir: Path, prefix: str = "page"
    ) -> str:
        """渲染整页并保存，返回相对文件名（用于缓存）。"""
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{prefix}-{page_no:03d}.png"
        (out_dir / name).write_bytes(self.render_page(page_no))
        return name

    @staticmethod
    def image_hash(png_bytes: bytes) -> str:
        return hashlib.sha256(png_bytes).hexdigest()[:16]
