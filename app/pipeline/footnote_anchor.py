"""脚注语义锚定（TOC_FOOTNOTE_MATH_RENDERING_DESIGN.md §7）。

锚定优先级：表格 → 图片 → 章节 → 文档末尾（§7.3）。
来源位置（Block.page / SourceInfo）与显示位置（placement）分离，
移动脚注不修改原始页码、裁剪与 bbox。

锚点信息写入 IR 的 ``anchor_id / anchor_kind / placement`` 字段；
渲染层按 placement 重排显示顺序。
"""

from __future__ import annotations

import logging
import re

from app.models.ir import Block, BlockType, DocumentIR

logger = logging.getLogger(__name__)

# 表格脚注匹配阈值（§7.4）
TABLE_H_OVERLAP_MIN = 0.35  # 横向重叠率
TABLE_GAP_RANGE = (-0.02, 0.30)  # 脚注顶部与表格底部的垂直间距（页面高度占比）

# 文档级脚注特征（收稿信息/版权等，§7.7）
DOCUMENT_NOTE_RE = re.compile(
    r"^(Received|Accepted|Revised|Copyright|©|\uf0a9)", re.I
)


def _h_overlap(a: list[float], b: list[float]) -> float:
    """两框横向重叠率（对较窄框）。"""
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    lo = max(a[0], b[0])
    hi = min(a[2], b[2])
    overlap = max(0.0, hi - lo)
    width = min(a[2] - a[0], b[2] - b[0])
    return overlap / width if width > 0 else 0.0


def _nearby_tables(fn: Block, ir: DocumentIR) -> list[tuple[Block, float]]:
    """同页、位于表格下方附近的表格（含表题），按垂直距离排序。"""
    cands = []
    fn_top = fn.bbox[1] if len(fn.bbox) == 4 else 1.0
    for b in ir.blocks:
        if b.type not in (BlockType.TABLE, BlockType.CAPTION):
            continue
        if b.page != fn.page or len(b.bbox) < 4:
            continue
        overlap = _h_overlap(fn.bbox, b.bbox)
        if overlap < TABLE_H_OVERLAP_MIN:
            continue
        gap = fn_top - b.bbox[3]
        if not (TABLE_GAP_RANGE[0] <= gap <= TABLE_GAP_RANGE[1]):
            continue
        cands.append((b, gap))
    cands.sort(key=lambda x: x[1])
    return cands


def _nearby_figures(fn: Block, ir: DocumentIR) -> list[tuple[Block, float]]:
    """同页、位于图片或图注下方附近的图片组。"""
    cands = []
    fn_top = fn.bbox[1] if len(fn.bbox) == 4 else 1.0
    for b in ir.blocks:
        if b.type not in (BlockType.FIGURE, BlockType.CAPTION):
            continue
        if b.page != fn.page or len(b.bbox) < 4:
            continue
        # 图片脚注：位于图片下方，横向重叠或空间距离最近
        overlap = _h_overlap(fn.bbox, b.bbox)
        gap = fn_top - b.bbox[3]
        if gap < -0.02 or gap > 0.35:
            continue
        if overlap < 0.2 and gap > 0.12:
            continue
        cands.append((b, gap))
    cands.sort(key=lambda x: x[1])
    return cands


def _previous_section(fn: Block, ir: DocumentIR) -> Block | None:
    """绑定 fn 之前最近的章节标题（§7.6）。"""
    anchor = None
    for b in ir.blocks:
        if b.order > fn.order:
            break
        if b.type in (BlockType.HEADING, BlockType.ABSTRACT):
            anchor = b
    return anchor


def _is_document_note(fn: Block) -> bool:
    text = (fn.content.text or "").strip()
    return bool(DOCUMENT_NOTE_RE.match(text))


def resolve_footnote_anchors(ir: DocumentIR) -> list[str]:
    """为无锚点的脚注推断语义归属，写入 anchor 字段。

    返回告警列表。无法可靠归属的脚注保持原位置并标记需核对（§7.8）。
    """
    warnings: list[str] = []
    for fn in ir.blocks:
        if fn.type is not BlockType.FOOTNOTE or fn.anchor_id:
            continue
        anchor = _find_anchor(fn, ir)
        if anchor is None:
            fn.quality.needs_review = True
            fn.quality.warnings.append("脚注未找到可靠内容锚点，保持原位置")
            warnings.append(f"{fn.id}: 脚注未找到可靠内容锚点")
        else:
            fn.anchor_id, fn.anchor_kind, fn.placement = anchor
    return warnings


def _find_anchor(fn: Block, ir: DocumentIR) -> tuple[str, str, str] | None:
    """按优先级返回 (anchor_id, anchor_kind, placement) 或 None。"""
    # 1. 表格（§7.4）
    for tbl, _gap in _nearby_tables(fn, ir):
        return tbl.id, "table", "group_end"
    # 2. 图片（§7.5）
    for fig, _gap in _nearby_figures(fn, ir):
        return fig.id, "figure", "group_end"
    # 3. 文档级（§7.7）
    if _is_document_note(fn):
        return "", "document", "document_end"
    # 4. 章节（§7.6）
    sec = _previous_section(fn, ir)
    if sec is not None:
        return sec.id, "section", "section_end"
    return None


# ---------------------------------------------------------------------------
# 渲染层：按 placement 计算显示位置
# ---------------------------------------------------------------------------


def arrange_footnotes(blocks: list[Block]) -> list[Block]:
    """按脚注锚点重排显示顺序（幂等，无锚点脚注保持原位置）。

    - section_end：插入到所属章节结束处（下一个同级或更高级标题之前）
    - table/figure group_end：紧跟表格/图片组之后（含同页紧随的图注/表题）
    - document_end：文档末尾
    """
    ordered = list(blocks)
    for fn in list(ordered):
        if fn.type is not BlockType.FOOTNOTE or not fn.placement:
            continue
        if fn.placement == "document_end":
            ordered.remove(fn)
            ordered.append(fn)
            continue
        # 先移除自身，再计算插入位置（避免索引偏移）
        ordered.remove(fn)
        target = _placement_index(ordered, fn)
        if target is None:
            # 锚点缺失：恢复到原位置附近（末尾追加，标记由质量检查处理）
            ordered.append(fn)
            continue
        ordered.insert(target, fn)
    return ordered


def _placement_index(blocks: list[Block], fn: Block) -> int | None:
    """计算脚注插入位置（下标）。"""
    anchor_idx = None
    for i, b in enumerate(blocks):
        if b.id == fn.anchor_id:
            anchor_idx = i
            break
    if anchor_idx is None:
        return None
    if fn.anchor_kind == "section":
        # 下一个同级或更高级标题之前（允许跨页，§7.6）
        anchor_level = _section_level(blocks[anchor_idx])
        for i in range(anchor_idx + 1, len(blocks)):
            b = blocks[i]
            if b.type is BlockType.HEADING:
                if _heading_level_of(b) <= anchor_level:
                    return i
            elif b.type in (BlockType.ABSTRACT, BlockType.DOCUMENT_TITLE):
                return i
        return len(blocks)
    if fn.anchor_kind in ("table", "figure"):
        # 锚点块之后，跳过同页紧随的图注/表题
        i = anchor_idx + 1
        while i < len(blocks):
            b = blocks[i]
            if b.type is BlockType.CAPTION and b.page == blocks[anchor_idx].page:
                i += 1
                continue
            break
        return i
    return None


def _heading_level_of(b: Block) -> int:
    from app.rendering.html_renderer import _heading_level

    return _heading_level(b.content.text or "")


def _section_level(b: Block) -> int:
    if b.type is BlockType.ABSTRACT:
        return 2
    return _heading_level_of(b)
