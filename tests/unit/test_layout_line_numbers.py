"""Generated review copies; never include private papers in release fixtures."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.parsing.line_numbers import remove_line_numbers
from app.parsing.auto_pdf import parse_document
import pymupdf


def column(page, x, counter_x, start=49, count=10, step=1, color=(.5, .5, 1)):
    for i in range(count):
        y = 100 + i * 16
        page.insert_text((x, y), 'Evidence supports spatial prediction and data [6].', fontsize=10)
        page.insert_text((counter_x, y), f'{start + i * step:03}', fontsize=8, color=color)


class LineNumbers(unittest.TestCase):
    def test_outer_margins_short_page_and_rotated_gutter(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            column(page, 60, 25)
            column(page, 320, 575, 102)
            page = doc.new_page(width=600, height=800)
            column(page, 60, 25, 112, count=3)
            page = doc.new_page(width=600, height=800)
            column(page, 320, 285, 5, step=5, color=(0, 0, 0))
            page.set_rotation(90)
            counts = remove_line_numbers(doc, [1, 2, 3])
            self.assertEqual(counts, {'1': 20, '2': 3, '3': 10})
            self.assertEqual(doc[2].rotation, 90)
            for page in doc:
                self.assertNotIn('049', page.get_text())
                self.assertIn('[6]', page.get_text())

    def test_legitimate_numbers_lists_tables_and_axes_stay(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            for i in range(10):
                y = 80 + i * 16
                page.insert_text((25, y), str(i + 1), fontsize=10)
                page.insert_text((60, y), 'A numbered list describes the experiment.', fontsize=10)
                # Small chart/table numbers form a sequence but have no prose.
                page.insert_text((330, y), str(i + 1), fontsize=8)
                page.insert_text((365, y), f'{80+i}.3  {90+i}.5', fontsize=8)
            page.insert_text((60, 320), 'In 2026, 7,404 images and 10,081 queries [16] were used.', fontsize=10)
            page.insert_text((60, 350), 'We compare datasets 049 and 050 in Table 1.', fontsize=10)
            page.insert_text((60, 380), '1. A paragraph begins with an enumeration.', fontsize=10)
            before = page.get_text()
            self.assertEqual(remove_line_numbers(doc, [1]), {})
            self.assertEqual(page.get_text(), before)

    def test_nonsequential_margin_digits_stay(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            for i, n in enumerate([5, 1, 9, 3, 8, 2, 6, 4]):
                page.insert_text((25, 100 + i * 16), f'{n:03}', fontsize=8)
                page.insert_text((60, 100 + i * 16), 'A marginal reference belongs to this statement.', fontsize=10)
            self.assertEqual(remove_line_numbers(doc, [1]), {})

    def test_counter_in_same_pdf_line_is_removed(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            for i in range(10):
                # Same font: zero-padding plus sequence and separation give the
                # evidence. These spans can be coalesced by the PDF text engine.
                page.insert_text((20, 100 + i * 16), f'{i+1:03}      Evidence supports spatial prediction [6].', fontsize=10)
            counts = remove_line_numbers(doc, [1])
            self.assertEqual(counts, {'1': 10})
            self.assertEqual(page.get_text().count('[6]'), 10)

    @unittest.skipUnless(hasattr(pymupdf.Document, "to_json"), "requires the layout extra")
    def test_parse_has_clean_text_and_unchanged_source_anchors(self):
        with tempfile.TemporaryDirectory(prefix='paperloom-lines-') as tmp:
            root = Path(tmp)
            pdf = root / 'review.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page(width=600, height=800)
                column(page, 60, 25)
                page.insert_text((60, 300), 'In 2026 we collected 7,404 images and 10,081 queries [16].', fontsize=10)
                # Pending user redactions must never be applied by our filter.
                page.add_redact_annot((55, 280, 400, 310))
                original_anchor = page.search_for('Evidence')[0]
                doc.save(pdf)
            digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
            with patch('httpx.Client.request', side_effect=AssertionError('Parsing is offline')):
                ir, _ = parse_document(pdf, root, 'synthetic', [1])
            self.assertEqual(hashlib.sha256(pdf.read_bytes()).hexdigest(), digest)
            text = ' '.join((b.content.text or '') + ' ' + (b.source.text or '') for b in ir.blocks)
            self.assertNotIn('049', text)
            self.assertNotIn('058', text)
            for number in ['2026', '7,404', '10,081', '[16]', '[6]']:
                self.assertIn(number, text)
            self.assertEqual(ir.document.parser['line_number_filter']['removed'], 10)
            body = next(b for b in ir.blocks if 'Evidence' in (b.content.text or ''))
            self.assertAlmostEqual(body.bbox[0] * 600, original_anchor.x0, delta=2)
            self.assertEqual(body.source.regions[0]['bbox'], body.bbox)


if __name__ == '__main__':
    unittest.main(verbosity=2)
