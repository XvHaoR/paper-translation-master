"""规范化：去页眉页脚、合并断行、修复连字符、合并跨页段落、语义归并。"""

from __future__ import annotations

import logging
import re

from app.models.ir import (
    Block,
    BlockContent,
    BlockType,
    QualityInfo,
    SourceInfo,
    SourceMethod,
    block_id,
)
from app.parsing.native_pdf import NativeBlock, NativeLine

logger = logging.getLogger(__name__)

# 换词连字符：仅当断行处前后都是小写字母且不是 "e.g." 类缩写时删除
_ABBR_TAIL_RE = re.compile(r"([A-Za-z]\.)$")

# 页眉页脚启发式
HEADER_FOOTER_MAX_Y = 0.06  # 距顶/底 6% 内的短行（保守过滤）
HEADER_FOOTER_COLLECT_Y = 0.08  # 收集跨页重复文本的区域上限（可略宽）
HEADER_FOOTER_MAX_CHARS = 90
HEADER_FOOTER_COLLECT_CHARS = 220  # 下载水印等长页眉的收集上限


def is_header_or_footer(
    bbox: list[float], text: str, repeats: set[str] | None = None
) -> bool:
    """判断文本块是否为页眉/页脚。

    - 有跨页重复集合时：位于页面顶部/底部 6% 区域、长度受限且重复出现的短行。
    - 无重复信息时（保守）：只丢弃纯页码，避免误删标题等正文。
    """
    if not bbox or len(bbox) < 4:
        return False
    text = text.strip()
    if not text:
        return False
    # 纯页码无论位置都丢弃
    if re.fullmatch(r"[-–—]?\s*\d{1,3}\s*[-–—]?", text):
        return True
    # 页码水印（"Page 1 of 46"）与投稿元信息（Journal/Manuscript 行）
    if re.fullmatch(r"Page\s*\d+\s*of\s*\d+", text, re.I):
        return True
    if re.match(r"^(Journal|Manuscript|Submission)\s*[:：]", text, re.I):
        return True
    y0, y1 = bbox[1], bbox[3]
    near_edge = y0 <= HEADER_FOOTER_MAX_Y or y1 >= 1.0 - HEADER_FOOTER_MAX_Y
    if not near_edge:
        return False
    if len(text) > HEADER_FOOTER_MAX_CHARS:
        return False
    if repeats:
        # 有重复集合：非重复短行不算页眉页脚（可能是正文标题）
        return text in repeats
    # 无重复信息：保守处理，只有明显的卷期/页眉模式才丢弃
    if re.fullmatch(r"(Vol\.?\s*\d+[\s,]*No\.?\s*\d*|Page\s*\d+|PAGE\s*\d+)", text, re.I):
        return True
    return False


def collect_repeated_short_lines(pages_text: list[list[tuple[list[float], str]]]) -> set[str]:
    """收集在多页相同位置出现的短文本（页眉/页脚候选）。"""
    from collections import Counter

    counts: Counter[str] = Counter()
    for page_lines in pages_text:
        seen_this_page = set()
        for bbox, text in page_lines:
            if not bbox:
                continue
            y0, y1 = bbox[1], bbox[3]
            if y0 <= HEADER_FOOTER_COLLECT_Y or y1 >= 1.0 - HEADER_FOOTER_COLLECT_Y:
                t = text.strip()
                if 3 <= len(t) <= HEADER_FOOTER_COLLECT_CHARS:
                    seen_this_page.add(t)
        for t in seen_this_page:
            counts[t] += 1
    return {t for t, c in counts.items() if c >= max(2, len(pages_text) // 3)}


def dehyphenate(text: str) -> str:
    """合并段落内换行并修复换词连字符。"""
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        line = line.rstrip()
        if not line:
            continue
        if not out:
            out.append(line)
            continue
        prev = out[-1]
        # 换词连字符：前一行以 "abc-" 结尾且下一行以小写字母开头
        m = re.search(r"([a-z])-$", prev)
        if m and re.match(r"^[a-z]", line) and not _ABBR_TAIL_RE.search(prev):
            out[-1] = prev[:-1] + line        # 当前行是参考文献/编号项（"[1] ..."、"1. ..."）时保持换行
        elif re.match(r"^\s*(\[\d+\]|\d{1,3}[.)]\s|[a-z][.)]\s)", line):
            out.append(line)
        # 前一行为编号或列表项（"1." / "[1]"）时保持换行
        elif re.match(r"^\s*(\d{1,3}[.)]|\[\d+\]|[a-z]\.[)\]])\s*$", prev):
            out.append(line)
        else:
            out[-1] = prev + " " + line
    return "\n".join(out).strip()


# 列表项 bullet 符号:无序符号 / 数字 / 字母编号
_BULLET_RE = re.compile(r"^\s*(?:[-•*▪◦·]|\d{1,2}[.)]|\([a-z]\))\s+")


def is_list_text(text: str) -> bool:
    """判断文本是否带项目符号列表特征(任一行以 bullet 开头)。"""
    return any(
        ln.strip() and _BULLET_RE.match(ln) for ln in (text or "").splitlines()
    )


def split_list_items(text: str) -> list[str]:
    """按 bullet 行拆分列表项;无 bullet 的续行合并到前一项。"""
    items: list[str] = []
    cur = ""
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln:
            continue
        m = _BULLET_RE.match(ln)
        if m:
            if cur:
                items.append(cur)
            cur = ln[m.end():].strip()
        else:
            cur = (cur + " " + ln).strip()
    if cur:
        items.append(cur)
    return [it for it in items if it]


def assemble_paragraphs(lines: list[NativeLine]) -> list[list[NativeLine]]:
    """把行聚类为自然段：按垂直间隙与字号变化切分。"""
    if not lines:
        return []
    paragraphs: list[list[NativeLine]] = [[lines[0]]]
    for ln in lines[1:]:
        prev = paragraphs[-1][-1]
        gap = ln.bbox[1] - prev.bbox[3]
        size_shift = abs(ln.size - prev.size) > 0.5
        # 间隙超过行高 1.2 倍视为新段；字号变化也切段（标题/公式等）
        line_height = max(prev.bbox[3] - prev.bbox[1], 1.0)
        if gap > line_height * 1.2 or size_shift:
            paragraphs.append([ln])
        else:
            paragraphs[-1].append(ln)
    return paragraphs


def classify_native_paragraph(
    lines: list[NativeLine], page_number: int
) -> tuple[BlockType, str]:
    """按字号/字体启发式给原生段落分类（标题、段落、图注、参考文献等）。"""
    text = " ".join(ln.text.strip() for ln in lines if ln.text.strip())
    if not text:
        return BlockType.UNKNOWN, ""
    sizes = [ln.size for ln in lines if ln.size]
    max_size = max(sizes) if sizes else 0
    mean_size = sum(sizes) / len(sizes) if sizes else 0
    bold = any(ln.flags & 16 for ln in lines) or any(
        "Bold" in ln.font or "bold" in ln.font for ln in lines
    )

    if re.fullmatch(r"\d{1,3}", text):
        return BlockType.PAGE_NUMBER, text
    # 作者行：如 "Alice Zhang and Bob Li" / "A. Zhang, B. Li, and C. Wang"
    if re.match(
        r"^[A-Z][A-Za-z.]+ [A-Z][A-Za-z.]+(\s*,\s*[A-Z][A-Za-z.]+ [A-Z][A-Za-z.]+)*\s*(and|&)\s*[A-Z][A-Za-z.]+ [A-Z][A-Za-z.]+$",
        text,
    ) and len(text) < 160:
        return BlockType.AUTHORS, text
    # 图注/表注：行首 "Figure N" / "Fig. N" / "Table N" / "TABLE N"
    if re.match(r"^(Figure|Fig\.|Table|TABLE|TABLE\s?\d)", text):
        return BlockType.CAPTION, text
    if re.match(r"^(Abstract|Abstract\s?—?\s?|ABSTRACT)", text) and max_size >= mean_size:
        return BlockType.ABSTRACT, text
    # 标题优先于参考文献：章节标题（"1. Introduction"）字号更大/加粗
    if max_size >= mean_size + 1.0 or (bold and len(text) < 160 and max_size > 10):
        if text.isupper() or bold:
            return BlockType.HEADING, text
    if mean_size >= 13.5 and len(text) < 200:
        return BlockType.HEADING, text
    # 参考文献：行首 [n] 或 n.，小字号单行
    if re.match(r"^\[?\d{1,3}\]?[\.\s]", text) and (
        (len(lines) == 1 and mean_size <= 9.5) or re.match(r"^\[\d+\]", text)
    ):
        return BlockType.REFERENCE, text
    return BlockType.PARAGRAPH, text


def native_block_to_ir(
    block: NativeBlock,
    page_number: int,
    order: int,
    page_w: float,
    page_h: float,
    header_footer_texts: set[str],
) -> Block | None:
    """把原生文本块转为 IR 块；页眉页脚/页码过滤后返回 None。"""
    paragraphs = assemble_paragraphs(block.lines)
    blocks: list[Block] = []
    for i, para_lines in enumerate(paragraphs):
        text = dehyphenate("\\n".join(ln.text for ln in para_lines))
        if not text:
            continue
        bbox = [
            min(ln.bbox[0] for ln in para_lines) / page_w,
            min(ln.bbox[1] for ln in para_lines) / page_h,
            max(ln.bbox[2] for ln in para_lines) / page_w,
            max(ln.bbox[3] for ln in para_lines) / page_h,
        ]
        if is_header_or_footer(bbox, text, header_footer_texts):
            continue
        btype, clean = classify_native_paragraph(para_lines, page_number)
        if btype is BlockType.UNKNOWN:
            continue
        blocks.append(
            Block(
                id=block_id(page_number, order + i),
                type=btype,
                page=page_number,
                order=order + i,
                bbox=bbox,
                content=BlockContent(text=clean),
                source=SourceInfo(method=SourceMethod.NATIVE, page=page_number),
                quality=QualityInfo(),
            )
        )
    return blocks[0] if len(blocks) == 1 else blocks


def merge_cross_page_paragraphs(ir_blocks: list[Block]) -> list[Block]:
    """合并跨页段落：页尾非完整段 + 下页小写开头的段落。

    规则（保守）：上一页最后一个正文段不以句末标点结尾、下一页第一个正文段
    以小写字母开头且两段都属于同一类正文块时合并。
    """
    out: list[Block] = []
    i = 0
    while i < len(ir_blocks):
        b = ir_blocks[i]
        if (
            b.type is BlockType.PARAGRAPH
            and out
            and out[-1].type is BlockType.PARAGRAPH
            and out[-1].page == b.page - 1
            and out[-1].content.text
            and b.content.text
            and not re.search(r"[.!?;:]$", out[-1].content.text.strip())
            and re.match(r"^[a-z0-9(\\[{]", b.content.text.strip())
        ):
            merged_text = (out[-1].content.text.strip() + " " + b.content.text.strip()).strip()
            merged = out[-1]
            merged.content.text = merged_text
            merged.quality.warnings.append("跨页段落已合并")
            merged.quality.needs_review = True
            out[-1] = merged
        else:
            out.append(b)
        i += 1
    return out
