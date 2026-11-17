"""质量检查：丢页、重复文本、非法 LaTeX、危险 HTML、资源完整性。"""

from __future__ import annotations

import logging
import re
from collections import Counter

from app.models.ir import Block, BlockType, DocumentIR, QualityInfo
from app.rendering.markdown_util import check_latex_renderable, check_latex_safe

logger = logging.getLogger(__name__)

DUPLICATE_MIN_LEN = 40  # 重复检测最小长度
DUPLICATE_MAX_REPEATS = 2  # 同一文本允许出现的最多次数

# 下载水印/页眉特征：跨页重复属正常，不算 OCR 幻觉
_SKIP_DUP_PATTERNS = (
    re.compile(r"downloaded from", re.I),
    re.compile(r"onlinelibrary|wiley", re.I),
    re.compile(r"doi\.", re.I),
    re.compile(r"^\d{5,},\s*\d{4}", re.I),  # "13653040, 2002, 9, ..."
)


def _is_watermark(text: str) -> bool:
    return any(p.search(text) for p in _SKIP_DUP_PATTERNS)


def check_duplicate_text(blocks: list[Block]) -> list[str]:
    """检测连续重复句 / 重复正文（OCR 幻觉信号）。"""
    warnings: list[str] = []
    counts: Counter[str] = Counter()
    for b in blocks:
        t = (b.content.text or "").strip()
        if len(t) >= DUPLICATE_MIN_LEN and not _is_watermark(t):
            counts[t] += 1
    for text, c in counts.items():
        if c > DUPLICATE_MAX_REPEATS:
            warnings.append(f"文本重复出现 {c} 次（可能为 OCR 幻觉）：{text[:60]}…")
    # 段内连续重复句
    for b in blocks:
        t = (b.content.text or "").strip()
        if not t:
            continue
        for m in re.finditer(r"(.{30,}?)\1{1,}", t):
            if len(m.group(1).strip()) >= 20:
                warnings.append(f"段内重复句（{b.id}）：{m.group(1)[:40]}…")
                break
    return warnings


def check_latex_blocks(blocks: list[Block]) -> list[str]:
    """检查公式块：LaTeX 语法安全与可渲染性。"""
    warnings: list[str] = []
    for b in blocks:
        latex = b.content.latex
        if not latex:
            continue
        for w in check_latex_safe(latex):
            warnings.append(f"{b.id}: {w}")
            b.quality.warnings.append(w)
        if not check_latex_renderable(latex):
            b.quality.warnings.append("公式不可渲染，将回退原图")
            b.quality.needs_review = True
    return warnings


def check_html_safety(blocks: list[Block]) -> list[str]:
    """检查表格 HTML 危险内容。"""
    warnings: list[str] = []
    for b in blocks:
        html = b.content.html
        if not html:
            continue
        if re.search(r"<\s*(script|iframe|object|embed|link|meta)", html, re.I):
            warnings.append(f"{b.id}: 表格 HTML 含危险标签，已标记需人工复核")
            b.quality.needs_review = True
        if re.search(r"\son\w+\s*=", html, re.I):
            warnings.append(f"{b.id}: 表格 HTML 含事件属性，已标记需人工复核")
            b.quality.needs_review = True
    return warnings


def check_missing_pages(ir: DocumentIR) -> list[str]:
    """检测缺失页面。"""
    present = {b.page for b in ir.blocks}
    expected = set(range(1, ir.document.page_count + 1))
    missing = expected - present
    if missing:
        return [f"以下页面没有任何内容块：{sorted(missing)}"]
    return []


def check_resources(ir: DocumentIR, doc_dir) -> list[str]:
    """检查图片/裁剪资源文件是否存在。"""
    import os

    warnings: list[str] = []
    for b in ir.blocks:
        for rel in (b.content.image_path, b.source.crop):
            if not rel:
                continue
            if not (doc_dir / rel).is_file():
                warnings.append(f"{b.id}: 资源缺失 {rel}")
    return warnings


def check_reading_order(ir: DocumentIR) -> list[str]:
    """排序质量检查（READING_ORDER_TECHNICAL_DESIGN.md §9.5）。

    - 模型顺序覆盖率过低 / 几何回退使用情况
    - 双栏页面高频左右振荡（正文按块序列交替超过阈值）
    - 同一页无顺序正文块过多
    """
    warnings: list[str] = []
    osc_threshold = 0.40  # 双栏页左右交替比例上限
    for page in ir.pages:
        page_no = page.number
        blocks = sorted(ir.blocks_by_page(page_no), key=lambda b: b.order)
        if not blocks:
            continue
        if page.reading_order_method == "geometric_fallback":
            warnings.append(f"第 {page_no} 页使用几何回退排序（模型顺序不可用）")
        elif page.reading_order_method and page.reading_order_coverage < 0.8:
            warnings.append(
                f"第 {page_no} 页模型顺序覆盖率仅 {page.reading_order_coverage:.0%}"
            )
        # 左右振荡检测：仅当页面被判定为多栏时才有意义
        if page.detected_columns >= 2:
            seq = [
                "L" if (b.bbox and (b.bbox[0] + b.bbox[2]) / 2 < 0.5) else "R"
                for b in blocks
                if len(b.bbox) == 4
            ]
            if len(seq) >= 8:
                switches = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
                ratio = switches / (len(seq) - 1)
                if ratio > osc_threshold:
                    warnings.append(
                        f"第 {page_no} 页阅读顺序左右交替频繁（{ratio:.0%}），"
                        "疑似双栏顺序错误"
                    )
    return warnings


def check_unrendered_math(blocks: list[Block]) -> list[str]:
    """渲染层前置检查：文本型块中的 $...$ 由 render_inline_math 统一转换，
    此处仅保留告警入口（渲染后的可见定界符由 check_html_unrendered_math 检查）。
    """
    return []


def check_html_unrendered_math(html: str) -> list[str]:
    """渲染后检查：页面中仍存在可见的完整 $...$ 公式定界符（§13.3）。

    排除 script/style 内容（blocks-data JSON 保留原始文本供搜索，非可见内容）
    与已转换的 math-inline 节点。
    """
    warnings: list[str] = []
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    body = re.sub(r'<span class="math-inline[^"]*".*?</span>', "", body, flags=re.S)
    matches = re.findall(r"\$[^$\n]{2,}\$", body)
    if matches:
        warnings.append(f"渲染后仍存在 {len(matches)} 处可见 LaTeX 定界符（未转换）")
    return warnings


def check_footnote_anchors(blocks: list[Block]) -> list[str]:
    """检查无锚点脚注与锚点缺失（§13.2）。"""
    warnings: list[str] = []
    known_ids = {b.id for b in blocks}
    for b in blocks:
        if b.type is not BlockType.FOOTNOTE:
            continue
        if not b.anchor_id and not b.placement:
            warnings.append(f"{b.id}: 普通脚注没有语义锚点")
        elif b.anchor_id and b.anchor_id not in known_ids:
            warnings.append(f"{b.id}: 脚注锚点不存在（{b.anchor_id}）")
    return warnings


def run_quality_checks(ir: DocumentIR, doc_dir=None) -> tuple[list[str], list[str]]:
    """运行全部质量检查，返回 (warnings, errors)。

    幂等：先重置所有块的质量标记，再基于当前 IR 重新检查。
    """
    for b in ir.blocks:
        b.quality.warnings = []
        b.quality.needs_review = False
    warnings: list[str] = []
    errors: list[str] = []
    warnings += check_duplicate_text(ir.blocks)
    warnings += check_latex_blocks(ir.blocks)
    warnings += check_html_safety(ir.blocks)
    warnings += check_missing_pages(ir)
    warnings += check_reading_order(ir)
    warnings += check_unrendered_math(ir.blocks)
    warnings += check_footnote_anchors(ir.blocks)
    if doc_dir is not None:
        warnings += check_resources(ir, doc_dir)
    # 汇总到文档级质量标记
    for b in ir.blocks:
        if b.quality.warnings:
            b.quality.needs_review = True
    return warnings, errors


def check_rendered_html(html: str) -> list[str]:
    """渲染后 HTML 检查（orchestrator 渲染完成时调用）。"""
    return check_html_unrendered_math(html)
