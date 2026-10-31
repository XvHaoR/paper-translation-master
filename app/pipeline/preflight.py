"""PDF 预检：加密/损坏检查、逐页文本可提取性与处理策略。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import fitz

from app.parsing.native_pdf import NativePdfExtractor

logger = logging.getLogger(__name__)

# 判定阈值
OCR_PAGE_CHARS = 40  # 字符数低于此值视为无法提取文本
OCR_PAGE_IMAGE_RATIO = 0.30  # 图片占比高于此值加强 OCR 倾向


@dataclass
class PagePreflight:
    number: int
    chars: int
    image_ratio: float
    rotation: int
    width: float
    height: float
    strategy: str = "native"  # native | ocr | hybrid

    @property
    def has_text(self) -> bool:
        return self.chars >= OCR_PAGE_CHARS


@dataclass
class PreflightResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page_count: int = 0
    pdf_hash: str = ""
    source_name: str = ""
    pages: list[PagePreflight] = field(default_factory=list)
    text_page_ratio: float = 1.0  # 可提取文本页占比

    @property
    def document_type(self) -> str:
        """native | scanned | hybrid"""
        if self.page_count == 0:
            return "unknown"
        r = self.text_page_ratio
        if r >= 0.85:
            return "native"
        if r <= 0.15:
            return "scanned"
        return "hybrid"


def run_preflight(pdf_path: str, ocr_mode: str = "auto") -> PreflightResult:
    """预检 PDF 并输出每页策略。

    ocr_mode:
      - auto：按文本可提取性决定每页策略
      - native：全部原生（不调用模型）
      - full：全部走 OCR 流水线
      - hybrid：原生页只对公式/表格区域走模型（由编排器实现）
    """
    result = PreflightResult()
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:  # noqa: BLE001
        result.ok = False
        result.errors.append(f"无法打开 PDF：{e}")
        return result

    try:
        if doc.needs_pass:
            result.ok = False
            result.errors.append("PDF 已加密，需要密码")
            return result
        result.page_count = doc.page_count
        result.source_name = str(pdf_path)
        result.pdf_hash = NativePdfExtractor.content_hash(pdf_path)

        extractor = NativePdfExtractor(pdf_path)
        try:
            with_text = 0
            for i in range(doc.page_count):
                page_no = i + 1
                stats = extractor.page_text_stats(page_no)
                w, h = extractor.page_size(page_no)
                strategy = "native"
                if ocr_mode in ("official", "full"):
                    strategy = "ocr"
                elif ocr_mode == "auto":
                    if stats["chars"] < OCR_PAGE_CHARS:
                        strategy = "ocr"
                    elif (
                        stats["chars"] < OCR_PAGE_CHARS * 5
                        and stats["image_ratio"] > OCR_PAGE_IMAGE_RATIO
                    ):
                        strategy = "hybrid"
                elif ocr_mode == "native":
                    strategy = "native"
                if stats["chars"] >= OCR_PAGE_CHARS:
                    with_text += 1
                result.pages.append(
                    PagePreflight(
                        number=page_no,
                        chars=stats["chars"],
                        image_ratio=stats["image_ratio"],
                        rotation=stats["rotation"],
                        width=w,
                        height=h,
                        strategy=strategy,
                    )
                )
            result.text_page_ratio = (
                with_text / result.page_count if result.page_count else 0.0
            )
        finally:
            extractor.close()

        if result.document_type == "scanned":
            result.warnings.append("检测为扫描件：全文依赖 OCR")
        elif result.document_type == "hybrid":
            result.warnings.append("检测为混合文档：部分页面依赖 OCR")
    finally:
        doc.close()
    return result
