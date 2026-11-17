"""Integration checks: native layouts, scan previews and saved reader anchors."""
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from app.parsing import auto_pdf
from app.pipeline.orchestrator import Orchestrator, ConvertOptions
from app.services import translation_export, translator
import pymupdf


def paragraph(label):
    return label + ". This research investigates reliable document understanding. " \
        "The experiments compare different methods using carefully designed data. " \
        "Our results show that spatial information helps recover the intended reading sequence. " \
        "The following discussion explains the evaluation and its limitations."


class AutoParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="paperloom-native-test-")
        cls.root = pathlib.Path(cls.directory.name)
        cls.pdf = cls.root / "layouts.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        for rect, label in [((50, 70, 550, 210), "SINGLEFIRST"), ((50, 240, 550, 410), "SINGLESECOND")]:
            assert page.insert_textbox(rect, paragraph(label), fontsize=11) >= 0
        page = doc.new_page(width=600, height=800)
        for rect, label in [((40, 70, 280, 370), "LEFTFIRST"), ((320, 70, 560, 370), "RIGHTSECOND")]:
            assert page.insert_textbox(rect, paragraph(label), fontsize=11) >= 0
        page = doc.new_page(width=600, height=800)
        for rect, label in [((30, 70, 195, 390), "THREELEFT"), ((218, 70, 383, 390), "THREEMIDDLE"),
                            ((405, 70, 570, 390), "THREERIGHT"), ((30, 460, 570, 600), "FULLWIDTHLAST")]:
            assert page.insert_textbox(rect, paragraph(label), fontsize=10) >= 0
        doc.save(cls.pdf)
        raster = doc[0].get_pixmap().tobytes("png")
        doc.close()
        scan = pymupdf.open()
        page = scan.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=raster)
        cls.scan = cls.root / "scan.pdf"
        scan.save(cls.scan)
        scan.close()
        cls.orch = Orchestrator(cls.root / "workspace")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_01_native_layout_order_without_ocr(self):
        with patch("httpx.Client.request", side_effect=AssertionError("Native parsing must not use network")):
            result = self.orch.convert(self.pdf)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(result.ir.pages), 3)
        for page, labels in [(1, ["SINGLEFIRST", "SINGLESECOND"]), (2, ["LEFTFIRST", "RIGHTSECOND"]),
                             (3, ["THREELEFT", "THREEMIDDLE", "THREERIGHT", "FULLWIDTHLAST"])]:
            text = " ".join(b.content.text or "" for b in result.ir.blocks if b.page == page)
            positions = [text.index(label) for label in labels]
            self.assertEqual(positions, sorted(positions), text)
        self.__class__.result = result

    def test_02_scanned_page_is_visible_and_incomplete(self):
        with patch("httpx.Client.request", side_effect=AssertionError("Native parsing must not use network")):
            result = self.orch.convert(self.scan)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "needs_review")
        self.assertTrue((result.doc_dir / result.ir.blocks[0].content.image_path).is_file())
        self.assertFalse(result.ir.blocks[0].content.text)



    def test_05_unicode_quality_and_wraps(self):
        self.assertTrue(auto_pdf.readable_text("这是可以提取的中文正文。"))
        self.assertFalse(auto_pdf.readable_text("\ufffd" * 300))
        self.assertFalse(auto_pdf.readable_text("\ue000\ue001" * 30))
        self.assertEqual(auto_pdf.join_lines("under-\nstanding 中文\n正文"), "understanding 中文正文")

    def test_06_rotated_page_keeps_text_and_display_coordinates(self):
        path = self.root / "rotated.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        page.insert_textbox((50, 60, 550, 300), paragraph("ROTATEDTEST") * 2, fontsize=12)
        page.set_rotation(90)
        doc.save(path)
        doc.close()
        result = self.orch.convert(path)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.ir.pages[0].rotation, 90)
        block = next(b for b in result.ir.blocks if "ROTATEDTEST" in (b.content.text or ""))
        self.assertGreater(block.bbox[0], .5)
        self.assertEqual(block.source.regions[0]["bbox"], block.bbox)

    def test_07_cache_tracks_native_parser_version(self):
        result = self.orch.convert(self.pdf)
        with patch.object(auto_pdf, 'parse_document', side_effect=AssertionError('Current cache should be reused')):
            cached = self.orch.convert(self.pdf)
        self.assertTrue(cached.ok, cached.errors)
        source = result.doc_dir / 'document.json'
        data = json.loads(source.read_text('utf-8'))
        data['document']['parser']['auto_parser_version'] = 'native-layout-v3'
        source.write_text(json.dumps(data), 'utf-8')
        with patch.object(auto_pdf, 'parse_document', wraps=auto_pdf.parse_document) as parse:
            updated = self.orch.convert(self.pdf)
        self.assertTrue(updated.ok, updated.errors)
        parse.assert_called_once()
        self.assertEqual(updated.ir.document.parser['auto_parser_version'], auto_pdf.PARSER_VERSION)



if __name__ == "__main__":
    unittest.main(verbosity=2)
