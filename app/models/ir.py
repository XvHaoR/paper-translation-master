"""版本化中间格式（IR）：全项目最重要的长期资产。

OCR 后端原始输出不直接进入模板，先统一为 IR。schema 破坏性变更时递增
``SCHEMA_VERSION``。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0"


class BlockType(str, Enum):
    DOCUMENT_TITLE = "document_title"
    AUTHORS = "authors"
    ABSTRACT = "abstract"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    EQUATION_INLINE = "equation_inline"
    EQUATION_BLOCK = "equation_block"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    CODE = "code"
    ALGORITHM = "algorithm"
    FOOTNOTE = "footnote"
    REFERENCE = "reference"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    UNKNOWN = "unknown"


class SourceMethod(str, Enum):
    NATIVE = "native"  # PDF 内嵌文本
    OCR = "ocr"  # 模型识别
    MIXED = "mixed"  # 原生 + OCR 合并


class SourceInfo(BaseModel):
    """块来源信息：方法、页码、原始裁剪图。"""

    method: SourceMethod = SourceMethod.NATIVE
    page: int = 0
    crop: Optional[str] = None  # assets/crops/... 相对路径
    raw_ref: Optional[str] = None  # 后端原始输出调试引用
    text: Optional[str] = None  # 人工修改前的原文，用于 PDF 定位


class QualityInfo(BaseModel):
    """质量信息：低可信度结果必须可见，不能假装完全正确。"""

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    needs_review: bool = False


class BlockContent(BaseModel):
    """内容载荷：不同类型块使用不同字段，至少一个非空。

    字段刻意保持宽松，以便同一块同时保存原生文本与 OCR 文本（``native_text``）。
    """

    text: Optional[str] = None  # 正文 / 标题 / 图注 / 参考文献
    native_text: Optional[str] = None  # 仅用于比较，不用于渲染
    latex: Optional[str] = None  # 公式
    display: bool = False  # equation_inline=False / equation_block=True
    html: Optional[str] = None  # 表格清洗后的 HTML
    image_path: Optional[str] = None  # 图片相对路径
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    items: Optional[list[str]] = None  # 列表项
    rows: Optional[int] = None  # 表格行数
    cols: Optional[int] = None  # 表格列数
    note: Optional[str] = None  # 块级备注（如 "OCR 回退"）

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "BlockContent":
        if not any(
            v is not None
            for v in (
                self.text,
                self.latex,
                self.html,
                self.image_path,
                self.items,
            )
        ):
            raise ValueError("block content 必须至少包含 text/latex/html/image_path/items 之一")
        return self


class Block(BaseModel):
    """内容块。坐标统一为页面宽高归一化后的 0～1。"""

    id: str
    type: BlockType = BlockType.UNKNOWN
    layout_label: str = ""  # 保留 OCR 原始版面分类，不将图注/表注信息丢弃
    heading_level: Optional[int] = Field(default=None, ge=1, le=6)
    page: int = 0
    order: int = 0
    bbox: list[float] = Field(default_factory=list)  # [x0, y0, x1, y1] 归一化
    content: BlockContent = Field(default_factory=BlockContent)
    source: SourceInfo = Field(default_factory=SourceInfo)
    quality: QualityInfo = Field(default_factory=QualityInfo)
    # 脚注语义锚定（TOC_FOOTNOTE_MATH_RENDERING_DESIGN.md §7.1）
    anchor_id: Optional[str] = None  # 归属的表格/图片/章节标题块 id
    anchor_kind: Optional[str] = None  # section | table | figure | document
    placement: Optional[str] = None  # after | group_end | section_end | document_end

    def render_text(self) -> str:
        """块的可读文本摘要，用于日志与搜索。"""
        c = self.content
        if c.text:
            return c.text
        if c.latex:
            return f"[公式] {c.latex}"
        if c.html:
            return f"[表格 {c.rows}x{c.cols}]"
        if c.image_path:
            return f"[图片] {c.image_path}"
        if c.items:
            return f"[列表 {len(c.items)} 项]"
        return ""


class PageInfo(BaseModel):
    """单页元信息。"""

    number: int = 0  # 1-based
    width: float = 0.0  # 点
    height: float = 0.0
    rotation: int = 0
    strategy: Literal["native", "ocr", "hybrid"] = "native"
    preview: Optional[str] = None  # 低分辨率预览相对路径
    # 阅读顺序诊断（READING_ORDER_TECHNICAL_DESIGN.md §6.3）
    reading_order_method: str = ""  # model | model_with_unordered_tail | geometric_fallback | native_geometric
    reading_order_coverage: float = 0.0
    detected_columns: int = 1
    removed_containers: int = 0


class DocumentMeta(BaseModel):
    """文档元信息。"""

    id: str  # 内容哈希前缀
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    source_pdf: str = ""  # 原始文件名
    page_count: int = 0
    language: str = "en"
    parser: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    duration_ms: Optional[int] = None


class DocumentIR(BaseModel):
    """顶层 IR 结构。"""

    schema_version: str = SCHEMA_VERSION
    document: DocumentMeta = Field(
        default_factory=lambda: DocumentMeta(id="")
    )
    pages: list[PageInfo] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)

    def blocks_by_page(self, page: int) -> list[Block]:
        return [b for b in self.blocks if b.page == page]

    def add_block(self, block: Block) -> Block:
        self.blocks.append(block)
        return block


def block_id(page: int, order: int) -> str:
    """稳定块 ID：p003-b012。"""
    return f"p{page:03d}-b{order:03d}"
