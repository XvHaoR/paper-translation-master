"""Meaningful regressions for the real two-paper extraction failures."""
from pathlib import Path
import json
import shutil
import subprocess

from bs4 import BeautifulSoup

from app.models.ir import Block, BlockContent, BlockType
from app.services.paper_semantics import prepare_blocks, formula_issues
from app.services.offline_reader import render_sections
from app.services.translation_export import _safe_html


def block(ident, kind="paragraph", text="text", bbox=None, **kwargs):
    return Block(id=ident,type=BlockType(kind),page=1,bbox=bbox or [.1,.1,.4,.3],content=BlockContent(text=text),**kwargs)


def test_official_empty_continuation_keeps_its_own_rectangle(tmp_path):
    blocks=[block("first"),block("empty",text=" ",bbox=[.55,.1,.9,.2])]
    entries=[{"block_id":i,"group_id":0,"block_bbox":[n*100 for n in b.bbox]} for i,b in enumerate(blocks)]
    result=prepare_blocks(blocks,{},tmp_path,{"page_001":{"width":100,"height":100,"parsing_res_list":entries}})
    assert result[0].source.regions==[{"page":1,"bbox":blocks[0].bbox},{"page":1,"bbox":blocks[1].bbox}]
    assert not blocks[0].source.regions  # immutable original evidence
    page=BeautifulSoup(render_sections(result,None,_safe_html),"html.parser")
    assert '0.55' in page.select_one('.pl-block')['data-regions']


def test_multicolumn_abstract_has_one_heading_and_does_not_consume_main_text(tmp_path):
    blocks=[block("a","abstract","Abstract. First paragraph."),block("b",text="Middle paragraph."),
            block("c","abstract","Continuation."),block("h","heading","1 Introduction"),block("body",text="Main body")]
    result=prepare_blocks(blocks,{},tmp_path)
    assert [b.type.value for b in result]==['abstract','abstract','abstract','heading','paragraph']
    soup=BeautifulSoup(render_sections(result,None,_safe_html),"html.parser")
    assert [h.get_text() for h in soup.select('h2')].count('Abstract')==1
    assert soup.get_text().count('Abstract')==1
    assert soup.select_one('[data-block-id=body]')['data-role']=='paragraph'


def test_html_table_preserves_spans_breaks_and_math():
    b=block("t","table");b.content.html=r'<table><tr><th colspan="2">Group</th></tr><tr><td rowspan="2">$x_{i}$</td><td>A\nB</td></tr><tr><td>C</td></tr></table>'
    source=render_sections([b],None,_safe_html)
    soup=BeautifulSoup(source,"html.parser")
    assert soup.select_one('th')['colspan']=='2' and soup.select_one('td')['rowspan']=='2'
    assert soup.select_one('br') and r'\n' not in soup.get_text()
    assert soup.select_one('[data-latex]')['data-latex']=='x_{i}'


def test_semantic_preparation_preserves_ir_reading_order_for_later_inserted_caption(tmp_path):
    table=block('table','table',order=20);table.content.html='<table><tr><td>1</td></tr></table>'
    caption=block('caption','caption','Table 1. Caption',order=19,layout_label='table_title')
    result=prepare_blocks([table,caption],{},tmp_path)
    assert [b.id for b in result]==['caption','table']
    assert [b.order for b in result]==[0,1]
    assert caption.order==19 and table.order==20


def test_empty_header_without_pdf_evidence_is_never_silently_deleted(tmp_path):
    b=block("table","table");b.content.html='<table><tr><td>A</td><td>B</td><td>1</td><td></td></tr><tr><td>v</td><td>unit</td><td>4</td><td>4</td></tr></table>'
    result=prepare_blocks([b],{},tmp_path)[0]
    assert result.content.html==b.content.html
    assert result.quality.needs_review


def test_formula_numbers_can_be_alphanumeric_and_cannot_cross_columns(tmp_path):
    equation=block("eq","equation_block",bbox=[.1,.1,.2,.15]);equation.content=BlockContent(latex='x=1')
    number=block("num","unknown","（C12）",bbox=[.46,.1,.49,.14],layout_label='formula_number')
    wrong=block("wrong","unknown","D4",bbox=[.89,.1,.93,.14],layout_label='formula_number')
    result=prepare_blocks([equation,number,wrong],{},tmp_path)
    assert result[0].content.note=='C12'
    page=render_sections(result,None,_safe_html)
    assert '(C12)' in page and 'D4' not in page
    assert BeautifulSoup(page,'html.parser').select_one('[data-latex]')['data-latex']=='x=1'


def test_bad_math_is_marked_instead_of_being_hidden():
    b=block("eq","equation_block");b.content=BlockContent(latex=r'雞x=1\]')
    assert len(formula_issues(b.content.latex))==2
    b.quality.needs_review=True;b.quality.warnings=formula_issues(b.content.latex)
    soup=BeautifulSoup(render_sections([b],None,_safe_html),'html.parser')
    assert soup.select_one('.paper-warning') and '雞' in soup.get_text()


def test_caption_association_skips_subplot_labels_but_never_body_text():
    node=shutil.which('node')
    if not node:
        import pytest
        pytest.skip('Node required for caption contract')
    source=(Path(__file__).resolve().parents[2]/'zotero/content/paper-layout.mjs').read_text(encoding='utf-8')
    source=source.replace('export { enhance, exportPage };','')
    source += '''
const assert=require('node:assert/strict');
const b=(role,text='',page='1')=>({dataset:{role,page},querySelector:()=>({textContent:text})});
const image=b('figure'),smallB=b('figure_caption','(b)'),smallC=b('figure_caption','(c)'),caption=b('figure_caption','Fig. 9. Complete caption');
assert.equal(captionOwner(caption,3,[image,smallB,smallC,caption]),image);
assert.equal(captionOwner(smallC,2,[image,smallB,smallC,caption]),image);
assert.equal(captionOwner(caption,2,[image,b('paragraph','Body text'),caption]),null);
assert.equal(captionOwner(caption,1,[b('figure','','2'),caption]),null);
'''
    result=subprocess.run([node,'-'],input=source,text=True,encoding='utf-8',capture_output=True)
    assert result.returncode==0,result.stderr


def test_region_retry_is_bounded_and_never_replaces_with_an_invalid_candidate(tmp_path):
    import fitz
    from app.parsing.region_quality import repair_formula_regions
    pdf=fitz.open();pdf.new_page();pdf.save(tmp_path/'original.pdf');pdf.close()
    good=block('good','equation_block');good.content=BlockContent(latex='y=2')
    bad=block('bad','equation_block');bad.content=BlockContent(latex=r'雞x=1\]')
    invalid=block('invalid','equation_block');invalid.content=BlockContent(latex=r'擴z=3\]')
    class Recognizer:
        calls=[]
        def recognize_region(self,image,kind):
            self.calls.append((image.name,kind))
            return '$$x=1$$' if image.name.startswith('bad') else r'\frac{'
    recognizer=Recognizer()
    result=repair_formula_regions([good,bad,invalid],tmp_path/'original.pdf',recognizer,tmp_path/'retries')
    assert len(recognizer.calls)==2 and [r['accepted'] for r in result]==[True,False]
    assert good.content.latex=='y=2' and bad.content.latex=='x=1'
    assert bad.source.text==r'雞x=1\]' and invalid.content.latex==r'擴z=3\]'


def test_degenerate_region_output_stops_further_requests(tmp_path):
    import fitz
    from app.parsing.region_quality import repair_formula_regions
    pdf=fitz.open();pdf.new_page();pdf.save(tmp_path/'original.pdf');pdf.close()
    blocks=[block(str(i),'equation_block') for i in range(3)]
    for b in blocks:b.content=BlockContent(latex=r'雞x=1\]')
    class Recognizer:
        calls=0
        def recognize_region(self,*args):
            self.calls+=1;return '\ufffd'*200
    recognizer=Recognizer()
    result=repair_formula_regions(blocks,tmp_path/'original.pdf',recognizer,tmp_path/'retries')
    assert recognizer.calls==1 and len(result)==1 and 'error' in result[0]
    assert all(b.content.latex==r'雞x=1\]' for b in blocks)


def test_native_region_fallback_reuses_its_model_without_touching_servers():
    from app.parsing.paddleocr_vl import PaddleOcrVlParser
    parser=PaddleOcrVlParser(vl_backend='fastdeploy-server',vl_server_url='http://localhost:8185/v1')
    class Native:
        calls=0
        def recognize_region(self,image,kind):
            self.calls+=1;return '$$x=1$$'
    parser._native_regions=Native();parser._prefer_native_regions=True
    assert parser.recognize_region(Path('a.png'),'formula')=='$$x=1$$'
    assert parser.recognize_region(Path('b.png'),'formula')=='$$x=1$$'
    assert parser._native_regions.calls==2 and not parser._load_attempted
