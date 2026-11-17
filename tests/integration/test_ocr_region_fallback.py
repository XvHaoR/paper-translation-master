"""Protect saved readers from degenerate OCR without hiding good equations."""
import json
from pathlib import Path

import pytest

from app.parsing.paddleocr_vl import OfficialPageResult
from app.pipeline.orchestrator import ConvertOptions, Orchestrator
from app.services.translation_export import create_ocr_export

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_native.pdf"
GARBAGE = r"\mathrm { ~ ~ } \mathrm { } " * 80
GOOD_FORMULA = "+".join(r"\mathrm{H_2O}" for _ in range(30))


@pytest.mark.parametrize("label", ["chart", "display_formula", "table"])
def test_ocr_loop_preserves_pdf_region_caption_and_valid_latex(tmp_path, label):
    value = f"<table><tr><td>{GARBAGE}</td></tr></table>" if label == "table" else GARBAGE

    class Recognizer:
        supports_page_selection = True

        def recognize_document(self, path, output, *, pages):
            assert pages == [1]
            return [OfficialPageResult(1, {"res": {"width": 1000, "height": 1000,
                "parsing_res_list": [
                    {"block_label": label, "block_bbox": [100, 150, 900, 450], "block_content": value},
                    {"block_label": "figure_title", "block_bbox": [100, 470, 900, 500],
                     "block_content": "Fig. 2. A preserved caption."},
                    {"block_label": "display_formula", "block_bbox": [100, 530, 400, 560],
                     "block_content": GOOD_FORMULA},
                ]}}, GARBAGE, output)]

    result = Orchestrator(tmp_path, recognizer=Recognizer()).convert(FIXTURE, ConvertOptions(pages="1"))
    assert result.ok, result.errors
    recovered, caption, formula = result.ir.blocks
    assert recovered.type.value == "figure"
    assert recovered.quality.needs_review
    assert recovered.source.regions[0]["bbox"] == recovered.bbox
    assert (result.doc_dir / recovered.content.image_path).read_bytes().startswith(b"\x89PNG")
    assert caption.content.text == "Fig. 2. A preserved caption."
    assert formula.content.latex == GOOD_FORMULA
    assert GARBAGE in json.loads((result.doc_dir / "raw/parser-output.json").read_text(encoding="utf-8"))["page_001"]["res"]["parsing_res_list"][0]["block_content"]
    assert r"\mathrm { ~ ~ }" not in (result.doc_dir / "index.html").read_text(encoding="utf-8")

    artifact = create_ocr_export(result.doc_dir)
    saved = result.doc_dir / "ocr" / artifact["id"]
    md = (saved / "ocr.md").read_text(encoding="utf-8")
    assert "![图 1](assets/media/" in md and "Fig. 2. A preserved caption." in md
    assert GOOD_FORMULA in md and r"\mathrm { ~ ~ }" not in md
