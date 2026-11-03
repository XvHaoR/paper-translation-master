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
    ('eCO_2-induced biomass, CO_2 and x_1^2.', '$eCO_{2}$-induced biomass, $CO_{2}$ and $x_{1}^{2}$.'),
    ('5g C m^-2 yr^-1 to 140g C m^-2 yr^-1', '5g C $m^{-2}$ $yr^{-1}$ to 140g C $m^{-2}$ $yr^{-1}$'),
    ('P^-1 ha^-1 and x^12', '$P^{-1}$ $ha^{-1}$ and $x^{12}$'),
    ('sample_2 input_12.npy batch_3 file_name.pdf', 'sample_2 input_12.npy batch_3 file_name.pdf'),
    ('CO_2.tif x_2.npy', 'CO_2.tif x_2.npy'),
    ('https://example.com/CO_2 `CO_2`', 'https://example.com/CO_2 `CO_2`'),
    ('Vitousek^30 and word^12', 'Vitousek^30 and word^12'),
    ('$eCO_2$ and \\(m^-2\\)', '$eCO_2$ and \\(m^-2\\)'),
    ('x_1_2', 'x_1_2'),
    (r'1/\mu_{leaf}, 1/n_{max,leaf}', r'$1/\mu_{leaf}$, $1/n_{max,leaf}$'),
    ('x_{n} < x_{p}, or 1-x_{np} if x_{n} > x_{p}', '$x_{n}$ < $x_{p}$, or 1-$x_{np}$ if $x_{n}$ > $x_{p}$'),
    ('1/LAI_{month}.tif https://example.com/1/x_{i}', '1/LAI_{month}.tif https://example.com/1/x_{i}'),
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


def test_nature_body_unbraced_scripts_enter_math_nodes_without_changing_values():
    text='eCO_2-induced biomass from 5g C m^-2 yr^-1 to 140g C m^-2 yr^-1.'
    rendered=render_official_markdown(text)
    assert 'data-latex="eCO_{2}"' in rendered
    assert rendered.count('data-latex="m^{-2}"')==2
    assert rendered.count('data-latex="yr^{-1}"')==2
    assert '140g C' in rendered and '5g C' in rendered


def test_comparison_symbols_do_not_hide_caption_math_as_html_tags():
    rendered=render_official_markdown('x_{n} < x_{p}, or 1-x_{np} if x_{n} > x_{p}')
    assert rendered.count('data-latex=')==5
    assert '&lt;' in rendered and '&gt;' in rendered
