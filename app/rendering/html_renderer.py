"""HTML 渲染器：IR → 语义化阅读页。

对应方案 §9 与 TOC_FOOTNOTE_MATH_RENDERING_DESIGN.md：
- 正文按自然段组织为单个 <p>，行内公式作为 translate="no" 子节点嵌入
- 标题/图注/脚注/参考文献/列表/目录标签共用统一行内公式渲染入口
- 表格先安全清洗，再只转换文本节点中的 $...$ 公式（§9）
- 脚注按语义锚点放置（章节结束/表格后/图片后，§7）
"""

from __future__ import annotations

import html
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

import bleach
import markdown as markdown_lib

from app.models.ir import Block, BlockType, DocumentIR
from app.rendering.markdown_util import clean_latex, split_inline_latex, strip_math_delimiters, normalize_bare_math

logger = logging.getLogger(__name__)

# 表格 HTML 允许列表（方案 §8.6：禁止脚本与事件属性）
ALLOWED_TAGS = [
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
]
ALLOWED_ATTRS = {
    "td": ["colspan", "rowspan", "align"],
    "th": ["colspan", "rowspan", "align"],
    "table": ["class"],
}
ALLOWED_PROTOCOLS = []

# 不参与翻译的块类型
NO_TRANSLATE_BLOCK_TYPES = {
    BlockType.CODE,
    BlockType.EQUATION_BLOCK,
    BlockType.EQUATION_INLINE,
    BlockType.REFERENCE,
    BlockType.PAGE_HEADER,
    BlockType.PAGE_FOOTER,
    BlockType.PAGE_NUMBER,
}

HEADING_BASE = 2  # document_title 为 h1，heading 从 h2 开始


def _heading_level(text: str) -> int:
    """按标题编号确定层级："1." → h2，"1.1" → h3，无编号 → h2。"""
    m = re.match(r"^(\d+(?:\.\d+)*)", text.strip())
    depth = m.group(1).count(".") if m else 0
    return min(HEADING_BASE + depth, 6)


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _no_translate_attrs() -> str:
    return ' translate="no" class="notranslate"'


def render_inline_math(text: str) -> str:
    """统一行内公式渲染入口（TOC_FOOTNOTE_MATH_RENDERING_DESIGN.md §8）。

    切分普通文本与 ``$...$``：普通文本 HTML 转义，LaTeX 输出 MathJax
    行内节点（``translate="no"``、``data-latex`` 保留原样）。
    """
    if not text:
        return ""
    parts = []
    for kind, content in split_inline_latex(normalize_bare_math(text)):
        if kind == "latex":
            latex = clean_latex(content.strip("$"))
            parts.append(
                f'<span class="math-inline notranslate" translate="no" '
                f'data-latex="{_esc(latex)}">\\({_esc(latex)}\\)</span>'
            )
        else:
            parts.append(_esc(content))
    return "".join(parts)


_OFFICIAL_DISPLAY_MATH_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.DOTALL)
_OFFICIAL_INLINE_MATH_RE = re.compile(
    r"(?<!\\)\$(?!\$)([^$\n]+?)(?<!\\)\$(?!\$)"
)


def _official_math_node(latex: str, *, display: bool) -> str:
    """Render OCR Markdown math without letting Markdown parse its TeX."""
    latex = latex.strip()
    if display:
        return (
            '<div class="equation-wrap math-display notranslate" translate="no" '
            f'data-latex="{_esc(latex)}">\\[{_esc(latex)}\\]</div>'
        )
    return (
        '<span class="math-inline notranslate" translate="no" '
        f'data-latex="{_esc(latex)}">\\({_esc(latex)}\\)</span>'
    )


def render_official_markdown(source_markdown: str) -> str:
    """Convert official Markdown while preserving all display and inline TeX."""
    placeholders: list[tuple[str, str, bool]] = []

    def protect(match: re.Match[str], display: bool) -> str:
        marker = f"PBMATHPLACEHOLDER{len(placeholders)}X"
        placeholders.append((marker, match.group(1), display))
        return marker

    protected = _OFFICIAL_DISPLAY_MATH_RE.sub(
        lambda match: protect(match, True), normalize_bare_math(source_markdown)
    )
    protected = _OFFICIAL_INLINE_MATH_RE.sub(
        lambda match: protect(match, False), protected
    )
    converted = markdown_lib.markdown(protected, extensions=["tables", "fenced_code"])

    for marker, latex, display in placeholders:
        node = _official_math_node(latex, display=display)
        if display:
            converted = re.sub(
                rf"<p>\s*{re.escape(marker)}\s*</p>",
                lambda _match: node,
                converted,
            )
        converted = converted.replace(marker, node)
    return converted


def _abstract_heading_re() -> re.Pattern:
    return re.compile(r"Abstract.*", re.I)


class _TableTextTransformer(HTMLParser):
    """只转换表格文本节点（TOC_FOOTNOTE_MATH_RENDERING_DESIGN.md §9.3）。

    - 不修改标签名/属性/结构；
    - 文本节点中的字面量 ``\\n`` 转为 ``<br>``（程序生成，非 OCR 提供）；
    - ``$...$`` 转为 MathJax 行内节点；
    - script/style 内部内容跳过。
    """

    def __init__(self, transform):
        super().__init__(convert_charrefs=False)
        self._transform = transform
        self._out: list[str] = []
        self._skip = 0

    @property
    def result(self) -> str:
        return "".join(self._out)

    def handle_starttag(self, tag, attrs):
        self._out.append(self.get_starttag_text())
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        self._out.append(self.get_starttag_text())

    def handle_data(self, data):
        if self._skip:
            self._out.append(data)
        else:
            self._out.append(self._transform(data))

    def handle_entityref(self, name):
        self._out.append(f"&{name};")

    def handle_charref(self, name):
        self._out.append(f"&#{name};")


def _table_cell_transform(text: str) -> str:
    """表格文本节点转换：字面量 \\n → <br>，然后统一行内公式渲染。

    用占位符先保护 \\n，避免被 render_inline_math 的 HTML 转义吞掉。
    """
    placeholder = "\x00PB_BR\x00"
    text = text.replace("\\n", placeholder)
    return render_inline_math(text).replace(placeholder, "<br>")


def render_table_text_nodes(cleaned_html: str) -> str:
    """表格 HTML 文本节点公式转换入口（§9.2：清洗之后调用）。"""
    parser = _TableTextTransformer(_table_cell_transform)
    parser.feed(cleaned_html)
    parser.close()
    return parser.result


def iter_outline_entries(ir: DocumentIR):
    """统一目录候选（§6.1）：正文渲染与目录生成共用。

    规则：
    - HEADING 块且文本匹配 Abstract 时跳过（由 ABSTRACT 块表示）。
    - ABSTRACT 块标签固定为 "Abstract"。
    - 返回 (block, label, level)。
    """
    for b in ir.blocks:
        text = (b.content.text or "").strip()
        if b.type is BlockType.HEADING:
            if _abstract_heading_re().match(text):
                continue
            yield b, text, _heading_level(text)
        elif b.type is BlockType.ABSTRACT:
            yield b, "Abstract", HEADING_BASE



def _no_translate_attrs() -> str:
    return ' translate="no" class="notranslate"'


def _truncate_toc_label(label: str, max_len: int = 80) -> str:
    """TOC 标题截断：按 ``$...$`` 分段累积，不切断行内公式。

    超长的后半段整体丢弃（普通文本可截断，公式必须成对）。
    """
    if len(label) <= max_len:
        return label
    out = ""
    for _kind, content in split_inline_latex(label):
        if len(out) + len(content) > max_len:
            break
        out += content
    return out or label[:max_len]


class HtmlRenderer:
    """把 DocumentIR 渲染为单文件阅读页。"""

    def render_document(self, ir: DocumentIR, doc_dir: Path) -> str:
        from app.pipeline.footnote_anchor import arrange_footnotes

        # 脚注按语义锚点重排（无锚点脚注保持原位置，兼容旧 IR）
        arranged = arrange_footnotes(ir.blocks)
        body = self._render_blocks(ir, arranged)
        toc = self._render_toc(ir)
        meta = self._render_meta(ir)
        blocks_json = self._blocks_json(ir)
        mathjax = self._mathjax_tag(doc_dir)
        theme = self._theme_script()

        title = _esc(ir.document.title or "论文阅读页")
        return TEMPLATE.format(
            title=title,
            meta=meta,
            toc=toc,
            body=body,
            blocks_json=blocks_json,
            mathjax=mathjax,
            theme=theme,
        )

    def render_official_document(
        self,
        ir: DocumentIR,
        doc_dir: Path,
        page_markdowns: list[tuple[int, str]],
    ) -> str:
        """渲染 PaddleOCR 官方 Markdown，不应用项目的标题/脚注/图注规则。

        官方 ``save_to_markdown`` 已经确定了标题层级、阅读顺序、表格和图片
        链接。本方法只完成 Markdown 到 HTML 的语法转换，并把图片相对路径从
        单页导出目录定位到文档资产目录。
        """
        body: list[str] = []
        for page, source_markdown in page_markdowns:
            asset_prefix = f"assets/official/page-{page:03d}/"
            source_markdown = source_markdown.replace(
                'src="imgs/', f'src="{asset_prefix}imgs/'
            ).replace(
                "(imgs/", f"({asset_prefix}imgs/"
            )
            converted = render_official_markdown(source_markdown)
            body.append(
                f'<section class="paper-page official-page" data-page="{page}">'
                f'<div class="official-page-content" data-page="{page}">{converted}</div>'
                "</section>"
            )

        title = _esc(ir.document.title or Path(ir.document.source_pdf).stem)
        # The Markdown remains the sole content source.  This small, local
        # script only annotates already-rendered DOM nodes so the normal
        # PaperLoom reader can provide its TOC, search, translation, image,
        # annotation and table-export affordances without changing text/order.
        body.append(self._official_feature_wiring(ir))
        return TEMPLATE.format(
            title=title,
            meta="",
            toc=self._render_toc(ir),
            body="\n".join(body),
            blocks_json=self._blocks_json(ir),
            mathjax=self._mathjax_tag(doc_dir),
            theme=self._theme_script(),
        )

    def _official_feature_wiring(self, ir: DocumentIR) -> str:
        """Attach PaperLoom interaction metadata to official Markdown HTML.

        This is deliberately browser-side and non-destructive: it assigns IDs
        and CSS classes to tags already emitted by PaddleOCR's Markdown.  It
        never inserts, moves, joins, edits, or reorders recognised content.
        """
        bindings: list[dict[str, str | int]] = []
        for b in ir.blocks:
            text = (b.content.text or b.content.latex or "").strip()
            if b.type is BlockType.FIGURE:
                kind = "image"
            elif b.type is BlockType.TABLE:
                kind = "table"
            elif b.type in (BlockType.EQUATION_BLOCK, BlockType.EQUATION_INLINE):
                kind = "equation"
            elif b.type in (BlockType.DOCUMENT_TITLE, BlockType.HEADING, BlockType.ABSTRACT):
                kind = "heading"
            elif not text:
                continue
            else:
                kind = "text"
            bindings.append(
                {
                    "id": b.id,
                    "page": b.page,
                    "type": b.type.value,
                    "kind": kind,
                    "text": re.sub(r"\s+", " ", text)[:160],
                }
            )
        payload = json.dumps(bindings, ensure_ascii=False).replace("<", "\\u003c")
        return f"""<script>
(function () {{
  var bindings = {payload};
  var used = new Set();
  function norm(value) {{ return (value || '').replace(/\\s+/g, ' ').trim(); }}
  function candidates(page, kind) {{
    var root = document.querySelector('.paper-page[data-page="' + page + '"]');
    if (!root) return [];
    if (kind === 'heading') return Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6'));
    if (kind === 'table') return Array.from(root.querySelectorAll('table'));
    if (kind === 'image') return Array.from(root.querySelectorAll('img'));
    if (kind === 'equation') return Array.from(root.querySelectorAll('.equation-wrap'));
    return Array.from(root.querySelectorAll('p,li,div,pre')).filter(function (el) {{
      return !el.classList.contains('official-page-content') && !el.querySelector('p,li,table,h1,h2,h3,h4,h5,h6');
    }});
  }}
  function compatible(el, wanted, kind) {{
    if (!wanted) return true;
    var actual = norm(el.textContent);
    if (kind === 'equation') {{
      actual = actual.replace(/\\$+/g, '').replace(/\\\\[\\[\\]\\(\\)]/g, '').trim();
      wanted = norm(wanted).replace(/\\$+/g, '').replace(/\\\\[\\[\\]\\(\\)]/g, '').trim();
    }}
    if (!actual) return false;
    var probe = wanted.slice(0, 72);
    return actual.indexOf(probe) === 0 || probe.indexOf(actual.slice(0, 72)) === 0;
  }}
  bindings.forEach(function (binding) {{
    var options = candidates(binding.page, binding.kind);
    var element = options.find(function (node) {{ return !used.has(node) && compatible(node, binding.text, binding.kind); }});
    if (!element && (binding.kind === 'image' || binding.kind === 'table')) {{
      element = options.find(function (node) {{ return !used.has(node); }});
    }}
    if (!element) return;
    used.add(element);
    if (binding.kind === 'image') {{
      var wrapper = element.parentElement || element;
      wrapper.classList.add('block', 'block-figure', 'figure-block');
      wrapper.id = binding.id;
      wrapper.setAttribute('data-block-id', binding.id);
      wrapper.setAttribute('data-block-type', binding.type);
      return;
    }}
    element.id = binding.id;
    element.classList.add('block', 'block-' + binding.type);
    element.setAttribute('data-block-id', binding.id);
    element.setAttribute('data-block-type', binding.type);
  }});
}})();
</script>"""

    # ------------------------------------------------------------------

    def _render_blocks(self, ir: DocumentIR, arranged_blocks: list[Block] | None = None) -> str:
        out: list[str] = []
        current_page = 0
        heading_level = 0  # 用于 heading 层级递增
        blocks = arranged_blocks if arranged_blocks is not None else ir.blocks
        # 图注上提:两栏版面中左栏文字常插在 figure 与 Fig.N 图注之间,
        # 渲染时把同页第一个 "Fig. N" 图注紧跟到 figure 之后
        hoisted: set[str] = set()
        has_abstract_body = any(
            b.type is BlockType.ABSTRACT
            and (b.content.text or "").strip()
            and not re.fullmatch(r"Abstract.*", (b.content.text or "").strip(), re.I)
            for b in ir.blocks
        )
        for i, b in enumerate(blocks):
            if b.id in hoisted:
                continue  # 已被上提到 figure 后
            if b.type is BlockType.DOCUMENT_TITLE:
                continue  # 由 meta 区渲染
            if b.type is BlockType.HEADING and has_abstract_body and re.fullmatch(
                r"Abstract.*", (b.content.text or "").strip(), re.I
            ):
                continue  # 已有 abstract 正文块，避免重复 "Abstract" 标题
            if b.type in NO_TRANSLATE_BLOCK_TYPES:
                cls_extra = " notranslate"
                attr_extra = _no_translate_attrs()
            else:
                cls_extra = ""
                attr_extra = ""
            if b.page != current_page:
                if current_page != 0:
                    out.append("</section>")
                out.append(f'<section class="paper-page" data-page="{b.page}">')
                current_page = b.page

            badge = self._review_badge(b)
            marker = self._page_marker(b)
            html_block = self._render_block_html(b, heading_level)
            cls = f"block block-{b.type.value}{cls_extra}"
            out.append(
                f'<div id="{_esc(b.id)}" class="{cls}" data-page="{b.page}" '
                f'data-block-id="{_esc(b.id)}" data-block-type="{b.type.value}"{attr_extra}>'
                f"{marker}{badge}{html_block}</div>"
            )
            if b.type is BlockType.HEADING:
                heading_level += 1
            if b.type is BlockType.FIGURE:
                for nb in blocks[i + 1:]:
                    if nb.page != b.page or nb.type is BlockType.FIGURE:
                        break
                    if (
                        nb.type is BlockType.CAPTION
                        and re.match(r"Fig\.?\s*\d", (nb.content.text or "").strip())
                    ):
                        hoisted.add(nb.id)
                        cbadge = self._review_badge(nb)
                        cmarker = self._page_marker(nb)
                        out.append(
                            f'<div id="{_esc(nb.id)}" class="block block-caption" '
                            f'data-page="{nb.page}" data-block-id="{_esc(nb.id)}" '
                            f'data-block-type="caption">{cmarker}{cbadge}'
                            f"{self._render_block_html(nb, heading_level)}</div>"
                        )
                        break
        if current_page != 0:
            out.append("</section>")
        return "\n".join(out)

    def _render_block_html(self, b: Block, heading_level: int) -> str:
        c = b.content
        t = b.type
        if t is BlockType.AUTHORS:
            authors = (c.text or "").splitlines()
            return (
                '<p class="authors">' + ", ".join(f"<span>{_esc(a)}</span>" for a in authors if a.strip()) + "</p>"
            )
        if t is BlockType.ABSTRACT:
            body = (c.text or "").strip()
            # “Abstract”标题行本身不重复渲染内容
            if re.fullmatch(r"Abstract\s*[—–-]?\s*", body, re.I):
                return '<h2 class="abstract-title">Abstract</h2>'
            return f'<h2 class="abstract-title">Abstract</h2><p class="abstract">{render_inline_math(body)}</p>'
        if t is BlockType.HEADING:
            level = _heading_level(c.text or "")
            text = (c.text or "").replace(chr(10), " ")
            return f"<h{level}>{render_inline_math(text)}</h{level}>"
        if t is BlockType.PARAGRAPH:
            return f"<p>{render_inline_math(c.text or '')}</p>"
        if t is BlockType.LIST:
            items = c.items or (c.text or "").splitlines()
            lis = "".join(f"<li>{render_inline_math(i)}</li>" for i in items if i.strip())
            return f"<ul>{lis}</ul>"
        if t in (BlockType.EQUATION_BLOCK, BlockType.EQUATION_INLINE):
            return self._equation_html(b)
        if t is BlockType.TABLE:
            return self._table_html(b)
        if t is BlockType.FIGURE:
            src = _esc(c.image_path or "")
            return (
                f'<figure class="figure-block"><img loading="lazy" src="{src}" '
                f'alt="Figure on page {b.page}">'
                f'<a class="figure-open" href="{src}" target="_blank" rel="noopener">查看原图 ↗</a></figure>'
            )
        if t is BlockType.CAPTION:
            return f'<p class="caption">{render_inline_math(c.text or "")}</p>'
        if t is BlockType.CODE:
            return f'<pre><code>{_esc(c.text or "")}</code></pre>'
        if t is BlockType.REFERENCE:
            return f'<p class="reference">{render_inline_math(c.text or "")}</p>'
        if t in (BlockType.FOOTNOTE, BlockType.UNKNOWN, BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER, BlockType.PAGE_NUMBER):
            return f'<p class="minor">{render_inline_math(c.text or "")}</p>'
        return f"<p>{render_inline_math(c.text or '')}</p>"

    def _para(self, text: str) -> str:
        """段落渲染：行内公式转为 translate=no 子节点（统一入口）。"""
        return render_inline_math(text)

    def _equation_html(self, b: Block) -> str:
        latex = clean_latex(strip_math_delimiters(b.content.latex or ""))
        crop = b.source.crop
        wrap = "math-display" if b.type is BlockType.EQUATION_BLOCK else "math-inline"
        open_t, close_t = "\\[", "\\]"
        if b.type is BlockType.EQUATION_INLINE:
            open_t, close_t = "\\( ", "\\)"
        crop_html = (
            f'<a class="crop-link" data-crop="{_esc(crop)}" title="查看原始区域">原图</a>'
            if crop
            else ""
        )
        number_html = (
            f'<span class="eq-number">{_esc(b.content.note or "")}</span>'
            if b.content.note
            else ""
        )
        return (
            f'<span class="equation-wrap {wrap}">{open_t}{_esc(latex)}{close_t}'
            f"{number_html}"
            f"<span class=\"eq-tools\">{crop_html}</span></span>"
        )

    def _table_html(self, b: Block) -> str:
        c = b.content
        html_table = bleach.clean(
            c.html or "",
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRS,
            protocols=ALLOWED_PROTOCOLS,
            strip=True,
        )
        # 清洗之后才转换文本节点公式（§9.2 顺序：清洗 → 文本节点转换）
        html_table = render_table_text_nodes(html_table)
        crop = b.source.crop
        note = ""
        if not html_table.strip():
            note = '<p class="table-warn">表格结构未能自动恢复，请查看原图。</p>'
        crop_html = (
            f'<a class="crop-link" data-crop="{_esc(crop)}" title="查看原始表格">查看原图</a>'
            if crop
            else ""
        )
        return (
            f'<div class="table-wrap">{note}{html_table}<div class="table-tools">{crop_html}</div></div>'
        )

    def _review_badge(self, b: Block) -> str:
        if not b.quality.needs_review:
            return ""
        warns = "；".join(b.quality.warnings[:2])
        return (
            f'<span class="review-badge" title="{_esc(warns)}">⚠ 需核对'
            f"<span class=\"review-hint\">{_esc(warns)}</span></span>"
        )

    def _page_marker(self, b: Block) -> str:
        return f'<span class="page-marker" data-page="{b.page}">p.{b.page}</span>'

    # ------------------------------------------------------------------

    def _render_toc(self, ir: DocumentIR) -> str:
        """目录：与正文共用标题候选（§6.1），Abstract 只出现一次（§6.2）。"""
        items: list[str] = []
        seen_labels: set[str] = set()
        for b, label, _level in iter_outline_entries(ir):
            # 目录键标准化去重（§6.3）
            key = re.sub(r"\s+", " ", label).strip().casefold()
            if key in seen_labels:
                continue
            seen_labels.add(key)
            items.append(
                f'<li><a href="#{_esc(b.id)}" data-target="{_esc(b.id)}">'
                f"{render_inline_math(_truncate_toc_label(label))}</a>"
                f'<span class="toc-page">{b.page}</span></li>'
            )
        if not items:
            return ""
        return "<ul>" + "".join(items) + "</ul>"

    def _render_meta(self, ir: DocumentIR) -> str:
        d = ir.document
        title = d.title or Path(d.source_pdf).stem
        authors = ", ".join(d.authors) if d.authors else ""
        authors_html = f'<p class="authors">{_esc(authors)}</p>' if authors else ""
        strategy = _doc_strategy_summary(ir)
        return (
            f'<header class="doc-meta"><h1>{_esc(title)}</h1>'
            f'{authors_html}'
            f'<p class="doc-info">来源：{_esc(d.source_pdf)} · 共 {d.page_count} 页 · {strategy}</p>'
            "</header>"
        )

    def _blocks_json(self, ir: DocumentIR) -> str:
        data = [
            {
                "id": b.id,
                "page": b.page,
                "type": b.type.value,
                "text": (b.content.text or "")[:120],
                "latex": b.content.latex or "",
                "crop": b.source.crop or "",
                "confidence": round(b.quality.confidence, 2),
                "needs_review": b.quality.needs_review,
                "warnings": b.quality.warnings[:3],
            }
            for b in ir.blocks
        ]
        return json.dumps(data, ensure_ascii=False)

    def _mathjax_tag(self, doc_dir: Path) -> str:
        """MathJax 本地资源优先，缺失时回退 CDN。

        使用 tex-chtml-full.js（全部扩展内嵌）：\boldsymbol 等宏无需动态加载扩展，
        避免单文件包 + 外置扩展版本不一致导致部分公式渲染为原始 LaTeX。
        """
        local = doc_dir / "assets" / "mathjax" / "tex-chtml-full.js"
        if local.is_file():
            return (
                '<script>window.MathJax={tex:{inlineMath:[["\\\\(","\\\\)"],["$","$"]],'
                'displayMath:[["\\\\[","\\\\]"],["$$","$$"]],processEscapes:true},'
                'options:{enableMenu:false,enableAssistiveMml:false}};</script>\n'
                '<script src="assets/mathjax/tex-chtml-full.js?v=6" id="MathJax-script" async></script>'
            )
        logger.warning("未找到本地 MathJax（assets/mathjax/tex-chtml-full.js），回退 CDN")
        return (
            '<script src="https://cdn.jsdelivr.net/npm/mathjax@4.0.0-beta.7/es5/tex-mml-chtml.js" '
            'id="MathJax-script" async></script>'
        )

    def _theme_script(self) -> str:
        return """<script>
(function () {
  var t = localStorage.getItem('pb-theme');
  if (t === 'dark') document.documentElement.classList.add('dark');
  else if (t === 'light') document.documentElement.classList.remove('dark');
})();
</script>"""


def _doc_strategy_summary(ir: DocumentIR) -> str:
    strategies = {}
    for p in ir.pages:
        strategies[p.strategy] = strategies.get(p.strategy, 0) + 1
    if not strategies:
        return "解析策略：未知"
    parts = [f"{k}: {v}页" for k, v in sorted(strategies.items())]
    return "解析策略：" + "，".join(parts)


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 论文翻译大师</title>
<link rel="stylesheet" href="assets/reader.css?v=16">
{theme}
</head>
<body>
<a class="skip-link" href="#content">跳转到正文</a>
<div class="toolbar">
  <a class="tb-brand" href="/" title="返回首页">论文翻译大师</a>
  <button id="tb-toc" title="目录">目录</button>
  <button id="tb-find" title="全文搜索">搜索</button>
  <button id="tb-theme" title="切换亮暗色">🌓</button>
  <button id="tb-font" title="调整字号">Aa</button>
  <button id="tb-figures" title="隐藏/显示图片">🖼</button>
  <button id="tb-refs" title="折叠参考文献">📚</button>
  <button id="tb-tables" title="导出表格为 Excel">📊</button>
  <button id="tb-annotate" title="显示/隐藏批注">🖍</button>
  <button id="tb-translate" title="翻译">译</button>
  <a class="tb-link" href="export.md" download title="导出 Markdown">⬇ MD</a>
  <span id="tb-status" class="tb-status"></span>
</div>
<div id="toc-panel" class="toc-panel" hidden>
  <h3>目录</h3>
  {toc}
</div>
<div id="find-panel" class="find-panel" hidden>
  <input id="find-input" type="search" placeholder="搜索正文…" autocomplete="off">
  <ul id="find-results"></ul>
</div>
<main id="content" class="reader">
  {meta}
  {body}
</main>
<footer class="reader-footer">
  <p>由论文翻译大师生成 · 公式与表格可点击"原图"核对原始 PDF 区域</p>
</footer>

<!-- 原图核对弹层 -->
<div id="crop-modal" class="crop-modal" hidden>
  <div class="crop-modal-box">
    <button id="crop-close" class="crop-close" aria-label="关闭">×</button>
    <h4 id="crop-title">原始区域</h4>
    <div id="crop-body"></div>
  </div>
</div>

<script type="application/json" id="blocks-data">{blocks_json}</script>
<script src="assets/table_excel.js" defer></script>
<script src="assets/reader.js?v=17" defer></script>
{mathjax}
</body>
</html>
"""


OFFICIAL_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - 论文翻译大师</title>
<link rel="stylesheet" href="assets/reader.css?v=official-1">
{theme}
</head>
<body>
<main id="content" class="reader official-reader">
{body}
</main>
{mathjax}
</body>
</html>
"""
