"""Recover prose, rather than hiding standalone formulas in otherwise empty regions."""
import fitz

from app.models.ir import Block, BlockContent, BlockType
from app.services.inline_semantics import recover_inline_prose


def source(tmp_path):
    document=fitz.open();page=document.new_page()
    text='The simulated experiment x=1 had a positive effect on plant growth. This missing paragraph contains substantial context which must remain available to the reader.'
    page.insert_textbox(fitz.Rect(45,60,500,150),text,fontsize=11)
    path=tmp_path/'paper.pdf';document.save(path);document.close()
    doc=fitz.open(path);page=doc[0];box=page.search_for('x=1')[0]
    formula=Block(id='inline-original',type=BlockType.EQUATION_INLINE,page=1,order=2,
                  bbox=[box.x0/page.rect.width,box.y0/page.rect.height,box.x1/page.rect.width,box.y1/page.rect.height],
                  content=BlockContent(latex='$$x=1$$'))
    return doc,formula,text


def test_missing_native_prose_is_restored_with_inline_formula_and_original_identity(tmp_path):
    doc,formula,text=source(tmp_path)
    try:result=recover_inline_prose([formula],doc)
    finally:doc.close()
    assert len(result)==1 and result[0].type is BlockType.PARAGRAPH
    assert '$x=1$' in result[0].content.text and 'positive effect on plant growth' in result[0].content.text
    assert 'must remain available to the reader.' in result[0].content.text
    assert result[0].content.inline_math[0]['id']=='inline-original'
    assert result[0].content.native_text and result[0].source.regions


def test_existing_prose_is_not_duplicated_or_overwritten(tmp_path):
    doc,formula,text=source(tmp_path)
    paragraph=Block(id='existing',type=BlockType.PARAGRAPH,page=1,bbox=[0,0,1,1],content=BlockContent(text=text))
    try:result=recover_inline_prose([paragraph,formula],doc)
    finally:doc.close()
    assert result==[paragraph,formula] and paragraph.content.text==text


def test_scanned_page_keeps_unmatched_formula_instead_of_silently_deleting_it(tmp_path):
    doc=fitz.open();doc.new_page()
    formula=Block(id='only',type=BlockType.EQUATION_INLINE,page=1,bbox=[.1,.1,.2,.2],content=BlockContent(latex='x=1'))
    try:assert recover_inline_prose([formula],doc)==[formula]
    finally:doc.close()


def test_proven_duplicate_formula_is_consumed_only_when_tex_is_already_in_prose(tmp_path):
    doc,formula,text=source(tmp_path)
    paragraph=Block(id='existing',type=BlockType.PARAGRAPH,page=1,bbox=[0,0,1,1],content=BlockContent(text=text.replace('x=1','$x=1$')))
    try:result=recover_inline_prose([paragraph,formula],doc)
    finally:doc.close()
    assert result==[paragraph] and paragraph.content.text.count('$x=1$')==1
    assert paragraph.content.inline_math[0]['id']==formula.id
