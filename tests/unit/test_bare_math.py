import pytest

from app.models.ir import Block, BlockContent, BlockType
from app.rendering.html_renderer import render_inline_math, render_official_markdown
from app.rendering.markdown_exporter import render_markdown
from app.rendering.markdown_util import normalize_bare_math


@pytest.mark.parametrize("source,expected", [
    ("LAI_{young}, LAI_{mature}, LAI_{old}.", "$LAI_{young}$, $LAI_{mature}$, $LAI_{old}$."),
    ("CO_{2} and x^{2}_{i}", "$CO_{2}$ and $x^{2}_{i}$"),
    (r"T_{\mathrm{air}}", r"$T_{\mathrm{air}}$"),
    ("file_name.pdf item_id", "file_name.pdf item_id"),
    ("LAI_{month}.tif", "LAI_{month}.tif"),
    ("LAI_{leaf age}_{spatial resolution}_{month/year-month}.tif", "LAI_{leaf age}_{spatial resolution}_{month/year-month}.tif"),
    ("$LAI_{young}$ and $$x^{2}$$", "$LAI_{young}$ and $$x^{2}$$"),
    ("value $V_{cmax} and more text", "value $V_{cmax} and more text"),
    ("`LAI_{young}`\n```python\nx_{i}\n```", "`LAI_{young}`\n```python\nx_{i}\n```"),
    ("[source](https://example.com/x_{i})", "[source](https://example.com/x_{i})"),
    ('<span data-id="x_{i}">LAI_{young}</span>', '<span data-id="x_{i}">$LAI_{young}$</span>'),
])
def test_explicit_bare_scripts_are_repaired_without_changing_code_or_links(source, expected):
    assert normalize_bare_math(source) == expected
    assert normalize_bare_math(expected) == expected


def test_caption_is_math_in_both_markdown_and_html_without_mutating_ir():
    text = "Figure 5. LAI_{young}, LAI_{mature}, and LAI_{old}."
    block = Block(id="caption", type=BlockType.CAPTION, content=BlockContent(text=text))
    markdown = render_markdown([block])
    assert "$LAI_{young}$" in markdown
    assert render_official_markdown(markdown).count('class="math-inline') == 3
    assert render_inline_math(text).count('class="math-inline') == 3
    assert block.content.text == text
