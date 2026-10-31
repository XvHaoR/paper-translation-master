"""IR 块 → Markdown 导出（app/rendering/markdown_exporter.py）。

与 html_renderer 保持同一语义映射：标题层级、行内公式、表格、图片。
输出为通用 GitHub 风格 Markdown（公式 `$...$` / `$$...$$`，由查看方渲染）。
"""
from html.parser import HTMLParser

from app.models.ir import Block, BlockType
from app.rendering.markdown_util import clean_latex, strip_math_delimiters, normalize_bare_math

SKIP_TYPES = {
    BlockType.PAGE_NUMBER,
    BlockType.PAGE_HEADER,
    BlockType.PAGE_FOOTER,
    BlockType.UNKNOWN,
}


def heading_level(text: str) -> int:
    """与 html_renderer._heading_level 一致："1." → h2，"1.1" → h3。"""
    import re

    m = re.match(r"^(\d+(?:\.\d+)*)", (text or "").strip())
    depth = m.group(1).count(".") if m else 0
    return min(2 + depth, 6)


class _TableParser(HTMLParser):
    """提取 HTML 表格的二维文本网格；嵌套标签只取文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("tr", "thead", "tbody"):
            if self._row is not None:
                self.rows.append(self._row)
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            if self._row is not None:
                self._row.append("".join(self._cell).strip())
        elif tag == "tr":
            if self._row is not None:
                self.rows.append(self._row)
                self._row = None

    def handle_data(self, data: str) -> None:
        if self._row is not None:
            self._cell.append(data)


def html_table_to_markdown(html: str) -> str | None:
    """清洗后的表格 HTML → GitHub 风格管道表格；失败返回 None（调用方回退原 HTML）。"""
    if not html or "<tr" not in html:
        return None
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001
        return None
    rows = [r for r in parser.rows if any(c for c in r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)

    def esc(cell: str) -> str:
        return str(cell).replace("|", "\\|").replace("\n", " ").strip()

    def fmt(row: list[str]) -> str:
        cells = [esc(row[i]) if i < len(row) else "" for i in range(width)]
        return "| " + " | ".join(cells) + " |"

    header = rows[0]
    lines = [fmt(header), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(fmt(r) for r in rows[1:])
    return "\n".join(lines)


def _render_block(b: Block) -> str | None:
    c = b.content
    t = b.type
    if t is BlockType.DOCUMENT_TITLE:
        return f"# {(c.text or '').strip()}"
    if t is BlockType.HEADING:
        text = (c.text or "").replace("\n", " ").strip()
        return f"{'#' * (b.heading_level or heading_level(text))} {text}"
    if t is BlockType.ABSTRACT:
        body = (c.text or "").strip()
        if body:
            return f"## Abstract\n\n{body}"
        return "## Abstract"
    if t is BlockType.AUTHORS:
        return (c.text or "").strip() or None
    if t is BlockType.PARAGRAPH:
        return (c.text or "").strip() or None
    if t is BlockType.LIST:
        items = c.items or (c.text or "").splitlines()
        lis = [f"- {i.strip()}" for i in items if i.strip()]
        return "\n".join(lis) or None
    if t is BlockType.EQUATION_BLOCK:
        latex = clean_latex(strip_math_delimiters(c.latex or "")).strip()
        if not latex:
            return None
        num = (c.note or "").strip().strip("()")
        if num and num.isdigit():
            return f"$$\n{latex}\n\\tag{{{num}}}\n$$"
        return f"$$\n{latex}\n$$"
    if t is BlockType.EQUATION_INLINE:
        latex = clean_latex(strip_math_delimiters(c.latex or "")).strip()
        return f"${latex}$" if latex else None
    if t is BlockType.TABLE:
        md = html_table_to_markdown(c.html or "")
        if md:
            return md
        # 回退：保留原 HTML 表格块
        return f"```html\n{c.html or ''}\n```"
    if t is BlockType.FIGURE:
        path = (c.image_path or "").strip()
        return f"![图 {b.page}]({path})" if path else None
    if t is BlockType.CAPTION:
        return (c.text or "").strip() or None
    if t is BlockType.CODE:
        return f"```\n{c.text or ''}\n```"
    if t in (BlockType.REFERENCE, BlockType.FOOTNOTE):
        return (c.text or "").strip() or None
    return None


def render_markdown(blocks: list[Block]) -> str:
    """按 (page, order) 顺序渲染全部块为 Markdown 文本。"""
    out: list[str] = []
    prev_page: int | None = None
    for b in sorted(blocks, key=lambda x: (x.page, x.order)):
        if b.type in SKIP_TYPES:
            continue
        if prev_page is not None and b.page != prev_page:
            out.append(f"\n<!-- p.{b.page} -->\n")
        prev_page = b.page
        line = _render_block(b)
        if not line:
            continue
        out.append(line)
        if b.quality.needs_review and b.quality.warnings:
            out.append(f"<!-- ⚠ 需核对：{'；'.join(b.quality.warnings[:2])} -->")
    return normalize_bare_math("\n\n".join(out).strip()) + "\n"
