"""Markdown / LaTeX 工具：表格转换、行内公式切分、LaTeX 安全检查。"""

from __future__ import annotations

import re

# 危险 TeX 命令：渲染前禁用（方案 §11.4）
DANGEROUS_TEX_PATTERNS = [
    re.compile(r"\\input\b"),
    re.compile(r"\\include\b"),
    re.compile(r"\\write\b"),
    re.compile(r"\\openout\b"),
    re.compile(r"\\closeout\b"),
    re.compile(r"\\read\b"),
    re.compile(r"\\def\s*\\[a-zA-Z]+"),
    re.compile(r"\\usepackage"),
    re.compile(r"\\href\s*\{"),
    re.compile(r"\\url\s*\{"),
]

# PaddleOCR-VL 输出常见瑕疵（渲染层修正，不改原始 IR）：
# 1) \inR^{C} —— 漏写 \mathbb，应为 \in\mathbb{R}^{C}
# 2) \mathrm{r g b} / \operatorname{F u s e} —— 命令体内单字符分词空格
_LATEX_INR_RE = re.compile(r"\\inR(?![a-zA-Z])")
_LATEX_MATHCMD_SPACE_RE = re.compile(r"\\(mathrm|operatorname)\{([^{}]*)\}")

_BARE_SCRIPT_RE = re.compile(
    r"(?<![\w\\/$])(?:\d+\s*/\s*)?(?:\\[A-Za-z]+|[A-Za-z][A-Za-z0-9]{0,15})"
    r"(?:[_^]\{(?:[^{}\n]|\{[^{}\n]*\}){1,80}\})+"
)
_BARE_NUMERIC_SCRIPT_RE = re.compile(
    r"(?<![\w\\/$])(?P<base>\\[A-Za-z]+|[A-Za-z][A-Za-z0-9]{0,15})"
    r"(?P<scripts>(?:[_^][-+]?\d{1,3})+)(?![\w_^{}])"
)
_MARKDOWN_PROTECTED_RE = re.compile(
    r"(```.*?```|~~~.*?~~~|`+[^`\n]*`+|\$\$.*?\$\$|(?<!\\)\$(?!\$)[^$\n]+\$"
    r"|\\\(.*?\\\)|\\\[.*?\\\]|!?\[[^\]\n]*\]\([^\n)]*\)"
    r"|https?://[^\s<>]+|<(?:pre|code)\b[^>]*>.*?</(?:pre|code)>"
    r"|<(?:!--.*?--|/?[A-Za-z][A-Za-z0-9:-]*(?=[\s/>])[^>]*|![A-Za-z][^>]*)>"
    r"|(?<!\\)\$[^\n$]*)",
    re.DOTALL | re.IGNORECASE,
)


def markdown_protected_ranges(text: str) -> list[tuple[int, int]]:
    """Ranges whose literal contents must not be interpreted as missing math."""
    return [(match.start(), match.end()) for match in _MARKDOWN_PROTECTED_RE.finditer(text)]


def normalize_bare_math(text: str) -> str:
    """Restore missing math delimiters around explicit TeX scripts.

    Preserve existing math, code, links, HTML attributes and ordinary filenames.
    Only exported text changes; OCR source and PDF remain untouched.
    """
    pieces = _MARKDOWN_PROTECTED_RE.split(text)
    for index in range(0, len(pieces), 2):
        def repair(match: re.Match) -> str:
            value = match.group(0)
            if re.match(r"\.[A-Za-z0-9]{1,8}\b", pieces[index][match.end():]):
                return value  # e.g. a literal filename template LAI_{month}.tif
            depth = 0
            scripts = []
            for char in value:
                if char in "_^" and not depth:
                    scripts.append(char)
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
            if scripts.count("_") > 1 or scripts.count("^") > 1:
                return value  # Repeated scripts are not a valid TeX atom.
            return f"${value}$"
        pieces[index] = _BARE_SCRIPT_RE.sub(repair, pieces[index])
        def numeric(match: re.Match) -> str:
            base, scripts = match.group('base', 'scripts')
            if re.match(r"\.[A-Za-z0-9]{1,8}\b", pieces[index][match.end():]):
                return match.group(0)
            # Short variables/units and chemical abbreviations are explicit
            # enough; ordinary snake_case identifiers are not math evidence.
            chemical = re.fullmatch(r"[a-z]?[A-Z][A-Z0-9]{0,7}", base)
            if not (base.startswith('\\') or len(base)<=2 or chemical):
                return match.group(0)
            if scripts.count('_')>1 or scripts.count('^')>1:
                return match.group(0)
            scripts = re.sub(r"([_^])([-+]?\d+)", r"\1{\2}", scripts)
            return '$'+base+scripts+'$'
        pieces[index] = _BARE_NUMERIC_SCRIPT_RE.sub(numeric, pieces[index])
    return "".join(pieces)


def strip_math_delimiters(latex: str) -> str:
    """IR may contain PaddleOCR's already-delimited TeX; unwrap before export."""
    value = latex.strip()
    pairs = (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$"))
    for _ in range(8):
        for opening, closing in pairs:
            if value.startswith(opening) and value.endswith(closing) and len(value) >= len(opening) + len(closing):
                value = value[len(opening):-len(closing)].strip()
                break
        else:
            break
    return value


def clean_latex(latex: str) -> str:
    """清洗 PaddleOCR-VL 输出的常见 LaTeX 瑕疵（幂等，安全命令不受影响）。"""
    if not latex:
        return latex
    out = _LATEX_INR_RE.sub(r"\\in\\mathbb{R}", latex)

    def _fix_cmd(m: re.Match) -> str:
        cmd, body = m.group(1), m.group(2)
        # 仅当全部 token 为单字符（如 "G P P"、"U p"）时去掉空格，
        # 多字符词（如 "kg m/s"）保留，避免误伤合法内容。
        if body and all(len(tok) == 1 for tok in body.split()):
            body = body.replace(" ", "")
        return f"\\{cmd}{{{body}}}"

    return _LATEX_MATHCMD_SPACE_RE.sub(_fix_cmd, out)


def check_latex_safe(latex: str) -> list[str]:
    """检查 LaTeX 安全性，返回警告列表。括号不配对也会告警。"""
    warnings: list[str] = []
    if not latex:
        return warnings
    for pat in DANGEROUS_TEX_PATTERNS:
        if pat.search(latex):
            warnings.append("检测到危险 TeX 命令，已禁止渲染")
            break
    # \left...\right 族（含 \biggl...\biggr 等变体）独立配对
    left_cmds = re.findall(r"\\(?:left|bigl|Bigl|biggl|Biggl)", latex)
    right_cmds = re.findall(r"\\(?:right|bigr|Bigr|biggr|Biggr)", latex)
    if len(left_cmds) != len(right_cmds):
        warnings.append(r"\left/\right 括号不配对")
    # 普通括号配对：剔除 \leftX / \rightX 标记后再统计
    cleaned = re.sub(
        r"\\(?:left|bigl|Bigl|biggl|Biggl|right|bigr|Bigr|biggr|Biggr)([()\[\]{}|])",
        "",
        latex,
    )
    for open_ch, close_ch in (("{", "}"), ("(", ")"), ("[", "]")):
        if cleaned.count(open_ch) != cleaned.count(close_ch):
            warnings.append(f"括号 {open_ch}{close_ch} 不配对")
            break
    return warnings


def check_latex_renderable(latex: str) -> bool:
    """MathJax 可渲染性的轻量预检：存在至少一个可渲染符号。"""
    stripped = latex.strip()
    if not stripped:
        return False
    if stripped in ("\\", "{", "}"):
        return False
    return True


def markdown_table_to_html(text: str) -> str | None:
    """把 Markdown 表格转换为语义化 HTML 表格；不是表格返回 None。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    if not all(ln.startswith("|") and ln.endswith("|") for ln in lines):
        # 允许无首尾竖线的表格
        if not all(ln.count("|") >= 2 for ln in lines):
            return None
    sep_line = lines[1]
    if not re.match(r"^[\s|:\-]+$", sep_line):
        return None

    def cells(ln: str) -> list[str]:
        c = ln.strip().strip("|").split("|")
        return [x.strip() for x in c]

    rows = [cells(lines[0])]
    rows += [cells(ln) for ln in lines[2:]]
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    out = ["<table>", "<thead><tr>"]
    for c in rows[0]:
        out.append(f"<th>{_escape_html(c)}</th>")
    for _ in range(ncols - len(rows[0])):
        out.append("<th></th>")
    out.append("</tr></thead><tbody>")
    for r in rows[1:]:
        out.append("<tr>")
        for c in r:
            out.append(f"<td>{_escape_html(c)}</td>")
        for _ in range(ncols - len(r)):
            out.append("<td></td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


_INLINE_MATH_RE = re.compile(r"(\$[^$\n]+\$|\$\$[^$]+\$\$)")

# 金额/数字模式：$100、$100.5、$25% 及 "$100 and $200" 的成对误匹配
_AMOUNT_RE = re.compile(r"^\d+(?:\.\d+)?%?$")
_AMOUNT_PHRASE_RE = re.compile(r"^\d+(?:\.\d+)?\s+(?:and|to|per|a|an|of)\b", re.I)


def _is_amount(matched: str) -> bool:
    """判断 $...$ 匹配是否为金额而非公式（§8.4）。"""
    content = matched[1:-1].strip()
    if not content:
        return True
    if _AMOUNT_RE.match(content):
        return True
    if _AMOUNT_PHRASE_RE.match(content):
        return True
    return False


def split_inline_latex(text: str) -> list[tuple[str, str]]:
    """把正文切分为 (类型, 内容) 片段，类型 ∈ text | latex。

    ``$...$`` 和 ``$$...$$`` 视为行内公式（LaTeX 部分）；
    金额（``$100``、``$100 and $200``）不误判为公式（§8.4）。
    """
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in _INLINE_MATH_RE.finditer(text):
        if _is_amount(m.group(1)):
            continue  # 金额保持为文本
        if m.start() > pos:
            parts.append(("text", text[pos : m.start()]))
        parts.append(("latex", m.group(1)))
        pos = m.end()
    if pos < len(text):
        parts.append(("text", text[pos:]))
    return parts or [("text", text)]


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
