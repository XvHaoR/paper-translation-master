"""LaTeX 清洗测试（markdown_util.clean_latex 及渲染接入）。"""

from __future__ import annotations

from app.rendering.html_renderer import render_inline_math
from app.rendering.markdown_util import clean_latex


def test_inr_fixed_to_mathbb():
    assert clean_latex(r"\beta_{m}\inR^{C}") == r"\beta_{m}\in\mathbb{R}^{C}"
    assert clean_latex(r"x\inR") == r"x\in\mathbb{R}"
    assert clean_latex(r"x\inR,y") == r"x\in\mathbb{R},y"


def test_mathrm_space_artifact_removed():
    assert clean_latex(r"E_{\mathrm{r g b}}(x)") == r"E_{\mathrm{rgb}}(x)"
    assert clean_latex(r"\operatorname{U p}(z)") == r"\operatorname{Up}(z)"


def test_multi_char_words_kept():
    # 多字符 token(如单位 "kg m/s")不误伤
    assert clean_latex(r"\mathrm{kg m/s}") == r"\mathrm{kg m/s}"


def test_valid_latex_untouched():
    src = r"\mathbf{Z}^{s}+\left(\mathbf{Z}^{s}\odot\boldsymbol{\gamma}\right),"
    assert clean_latex(src) == src
    # 幂等
    assert clean_latex(clean_latex(src)) == src


def test_render_inline_math_applies_cleanup():
    html = render_inline_math(r"where $\beta_{m}\inR^{C}$ are learnable")
    assert "\\in\\mathbb{R}^{C}" in html
    assert "\\inR" not in html


def test_equation_html_uses_cleaned_latex():
    from app.models.ir import Block, BlockContent, BlockType

    b = Block(
        id="e1",
        type=BlockType.EQUATION_BLOCK,
        page=5,
        order=64,
        content=BlockContent(latex=r"\mathbf{P}_{\mathrm{r g b}}^{s}=\mathbf{Z}^{s}"),
    )
    from app.rendering.html_renderer import HtmlRenderer

    html = HtmlRenderer()._equation_html(b)
    assert "\\mathrm{rgb}" in html
    assert "\\mathrm{r g b}" not in html
