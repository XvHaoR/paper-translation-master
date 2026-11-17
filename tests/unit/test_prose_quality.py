import fitz

from app.models.ir import Block,BlockContent,BlockType
from app.services.prose_quality import repair_pdf_citations


def native_pdf():
    doc=fitz.open();page=doc.new_page()
    page.insert_htmlbox(fitz.Rect(40,40,550,200),
        '<p>Global inputs included N deposition<sup>61,62</sup> and P deposition<sup>63</sup>. '
        'Atmospheric conditions were held constant across the complete experiment.</p>')
    return doc


def block(text):
    return Block(id='body',type=BlockType.PARAGRAPH,page=1,bbox=[0,0,1,1],content=BlockContent(text=text))


def test_damaged_references_are_recovered_from_pdf_superscripts_not_guessed():
    text='Global inputs included N deposition��6¹,⁶² and P deposition�����³. Atmospheric conditions were held constant across the complete experiment.'
    b=block(text)
    with native_pdf() as pdf:repair_pdf_citations([b],pdf)
    assert b.content.text==text.replace('��6¹,⁶²','$^{61,62}$').replace('�����³','$^{63}$')
    assert b.source.text==text and len(b.content.text_repairs)==2
    assert [r['native'] for r in b.content.text_repairs]==['61,62','63']
    assert not b.quality.needs_review


def test_mismatched_pdf_text_keeps_damage_visible_and_flags_review():
    b=block('A completely unrelated paragraph has damage��3 followed by different words and conclusions.')
    with native_pdf() as pdf:repair_pdf_citations([b],pdf)
    assert '��3' in b.content.text and not b.content.text_repairs
    assert b.quality.needs_review and b.quality.warnings


def test_without_native_layer_does_not_invent_reference_numbers():
    b=block('The evidence includes deposition��³ and another independent experiment.')
    repair_pdf_citations([b],None)
    assert b.content.text=='The evidence includes deposition��³ and another independent experiment.'
    assert b.quality.needs_review


def test_existing_math_and_code_stay_literal():
    text='Global inputs included N deposition$^{61,62}$ and P deposition`��63`. Atmospheric conditions were held constant across the complete experiment.'
    b=block(text)
    with native_pdf() as pdf:repair_pdf_citations([b],pdf)
    assert b.content.text==text and not b.content.text_repairs


def test_bare_word_citation_uses_verified_native_number_without_italicizing_word():
    b=block('Global inputs included N deposition^61,62 and P deposition^63. Atmospheric conditions were held constant across the complete experiment.')
    with native_pdf() as pdf:repair_pdf_citations([b],pdf)
    assert 'deposition$^{61,62}$ and P deposition$^{63}$' in b.content.text
    assert b.content.text_repairs[0]['native']=='61,62'


def test_pdf_without_citation_typography_does_not_fabricate_superscripts():
    b=block('Global inputs included N deposition��6¹,⁶² and P deposition�����³. Atmospheric conditions were held constant across the complete experiment.')
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40,60),'Global inputs included N deposition61,62 and P deposition63.')
        repair_pdf_citations([b],pdf)
    assert not b.content.text_repairs and b.quality.needs_review
