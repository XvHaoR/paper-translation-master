"""Synthetic reading-order and translation-export regressions; no user papers."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
from app.models.ir import Block, BlockContent, BlockType, DocumentIR, DocumentMeta, SourceInfo
from app.parsing.reading_flow import reconstruct, join_text
from app.rendering.markdown_exporter import render_markdown
from app.services.translation_export import create_ocr_export, create_translation, reader_page
from app.services.translator import TranslateCache
from app.services.ocr_edit import import_source, prepare_or_save

def block(ident, text, page=1, bbox=(.1,.7,.45,.9), kind=BlockType.PARAGRAPH):
    return Block(id=ident, type=kind, page=page, bbox=list(bbox), content=BlockContent(text=text),
                 source=SourceInfo(text=text, regions=[{'page':page,'bbox':list(bbox)}]))

class Reading(unittest.TestCase):
    def test_join_across_page_and_float_with_notes(self):
        first=block('a','The method learns an open-')
        table=block('t','',2,(.1,.03,.45,.16),BlockType.TABLE)
        caption=block('c','TABLE 1. Evaluation results.',2,(.1,.17,.45,.20))
        note=block('n','1 An independent explanatory note.',1,(.1,.92,.45,.97),BlockType.FOOTNOTE)
        tail=block('b','vocabulary representation for the experiments.',2,(.1,.23,.45,.40))
        blocks=reconstruct([first,note,table,caption,tail])
        self.assertEqual(blocks[0].content.text,'The method learns an open-vocabulary representation for the experiments.')
        self.assertEqual({r['page'] for r in blocks[0].source.regions},{1,2})
        self.assertEqual(blocks[-1].type,BlockType.FOOTNOTE)
        cap=next(b for b in blocks if b.id=='c')
        self.assertEqual(cap.type,BlockType.CAPTION)
        self.assertEqual(cap.anchor_id,'t')
        page=reader_page('Synthetic document','en',render_markdown(blocks),blocks=blocks)
        self.assertIn('role="doc-endnotes"',page)
        self.assertLess(page.index('open-vocabulary'),page.index('data-block-id="t"'))

    def test_heading_and_finished_sentence_are_boundaries(self):
        a=block('a','A complete statement.')
        b=block('b','another unrelated paragraph',2,(.1,.1,.45,.3))
        self.assertEqual(len(reconstruct([a,b])),2)
        h=block('h','Results',2,(.1,.04,.45,.08),BlockType.HEADING)
        a.content.text='An incomplete statement'
        self.assertEqual(len(reconstruct([a,h,b])),3)
        self.assertEqual(join_text('under-','standing'),'understanding')
        self.assertEqual(join_text('ground-and-','refer'),'ground-and-refer')

    def test_translation_export_preserves_source_regions(self):
        with tempfile.TemporaryDirectory(prefix='paperloom-export-test-') as tmp:
            root=Path(tmp)
            blocks=reconstruct([block('a','The representation continues'),
                                block('b','on another page.',2,(.1,.1,.45,.3))])
            picture=block('p003-b001','',3,kind=BlockType.EQUATION_BLOCK)
            picture.content=BlockContent(image_path='assets/crops/p003-b001.png')
            blocks.append(picture)
            ir=DocumentIR(document=DocumentMeta(id='synthetic-document',title='Synthetic document',source_pdf='generated.pdf',
                          parser={'name':'pymupdf-layout','auto_parser_version':'native-layout-v5-native-only'}),blocks=blocks)
            (root/'document.json').write_text(ir.model_dump_json(),encoding='utf-8')
            (root/'assets/crops').mkdir(parents=True)
            from PIL import Image
            Image.new('RGB',(12,12),'white').save(root/picture.content.image_path)
            result=create_ocr_export(root)
            saved=root/'ocr'/result['id']
            self.assertTrue((saved/'source.json').is_file())
            manifest=json.loads((saved/'manifest.json').read_text(encoding='utf-8'))
            import_source(saved/'source.zip',root/'imported',manifest['source_revision'])
            self.assertTrue((root/'imported'/picture.content.image_path).is_file())
            edited=prepare_or_save(saved,root/'edited',root,{'base_revision':manifest.get('edit_revision'),'save':False})
            self.assertTrue((root/'edited'/picture.content.image_path).is_file())
            import_source(root/'edited/source.zip',root/'reimported',edited['source_revision'])
            def translate(_directory, ids, _settings, _cache):
                return {'results':{bid:{'text':'这是一段跨页的测试译文。'} for bid in ids}}
            with patch('app.services.translator.translate_batch',side_effect=translate):
                result=create_translation(root,{'llm_base_url':'http://127.0.0.1:1/v1','llm_model':'synthetic','lang_to':'zh'},TranslateCache(root/'cache.json'))
            state=json.loads((root/'translations'/result['id']/'translation-state.json').read_text(encoding='utf-8'))
            self.assertEqual([b['id'] for b in state['originals']],[b['id'] for b in state['translated']])
            self.assertEqual(state['originals'][0]['source']['regions'],state['translated'][0]['source']['regions'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
