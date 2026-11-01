"""Full RapidOCR contracts: no formulas/tables/coordinates lost in adaptation."""
from pathlib import Path
from types import SimpleNamespace
import json
from zipfile import ZipFile
import ast
import shutil
import subprocess

import fitz
import pytest

from app.models.ir import BlockType
from app.parsing.rapidocr_backend import RapidDocumentRecognizer
from app.pipeline.rapid_document import RapidDocumentConverter, paragraph_text
from app.pipeline.orchestrator import Orchestrator, ConvertOptions
from app.services.translation_export import create_ocr_export


def raw(kind, content="text", **extra):
    return {"type": kind, "bbox": [20,30,200,80], "lines": [{"spans": [{"type":"text", "content":content}]}], **extra}


def table():
    return raw("table_body", lines=[{"spans":[{"type":"table", "html":"<table><tr><td>A</td><td>42</td></tr></table>"}]}])


class FakeRecognizer:
    version = "test-full"
    def __init__(self, missing=False):
        self.calls = 0
        self.missing = missing
    def recognize(self, data):
        self.calls += 1
        with fitz.open(stream=data, filetype="pdf") as pdf:
            count = len(pdf)
        page = {"page_size":[600,800], "para_blocks":[raw("title", "Paper", level=1),
            raw("text", "Some original text"),
            raw("interline_equation", lines=[{"spans":[{"type":"interline_equation","content":r"\frac{x^2}{a^2}=1"}]}]),
            {"type":"table","bbox":[20,30,200,100],"blocks":[table(),raw("table_caption", "Table 1. Scores")]}
        ]}
        return SimpleNamespace(images={}, middle_json={"pdf_info":[page]*(0 if self.missing else count)})


def source_pdf(path, count=1):
    with fitz.open() as pdf:
        for i in range(count):
            pdf.new_page(width=600,height=800).insert_text((30,40), f"Native page {i+1} has enough text to verify that native content never bypasses formula and table analysis.")
        pdf.save(path)


def test_equations_tables_captions_keep_original_boxes():
    converter = RapidDocumentConverter(FakeRecognizer())
    equation = converter.blocks(raw("interline_equation", "x^2"), 6, [600,800], {})[0]
    assert equation.content.latex == "x^2" and equation.type == BlockType.EQUATION_BLOCK
    assert equation.page == 6 and equation.bbox == [20/600,30/800,200/600,80/800]
    blocks = converter.blocks({"type":"table","blocks":[raw("table_caption", "Table 2"),table()]}, 3, [600,800], {})
    assert [b.type for b in blocks] == [BlockType.CAPTION, BlockType.TABLE]
    assert "<td>42</td>" in blocks[1].content.html


@pytest.mark.parametrize("kind", ["interline_equation", "table_body"])
def test_missing_structured_math_or_table_fails_instead_of_screenshot_fallback(kind):
    with pytest.raises(RuntimeError, match="LaTeX|结构化"):
        RapidDocumentConverter(FakeRecognizer()).blocks(raw(kind, ""), 1, [600,800], {})


def test_inline_math_is_not_flattened_into_plain_text():
    block = raw("text", lines=[{"spans":[{"type":"text","content":"net rate"},
        {"type":"inline_equation","content":r"W_c"},{"type":"text","content":"changes"}]}])
    assert paragraph_text(block) == r"net rate $W_c$ changes"


def test_original_layout_labels_keep_captions_and_first_page_section_headings():
    converter=RapidDocumentConverter(FakeRecognizer())
    caption=converter.blocks(raw("text","Figure 1. A composite figure",original_label="figure_title"),1,[600,800],{})[0]
    assert caption.type==BlockType.CAPTION and caption.layout_label=="figure_title"
    heading=converter.blocks(raw("title","1 Introduction",original_label="paragraph_title"),1,[600,800],{})[0]
    assert heading.type==BlockType.HEADING


def test_image_output_never_writes_an_upstream_path(tmp_path):
    (tmp_path / "assets/figures").mkdir(parents=True)
    images = RapidDocumentConverter.save_images({"../../outside.png":b"image-data"}, tmp_path)
    assert len(images)==1
    assert next(iter(images.values())).startswith("assets/figures/")
    assert not (tmp_path.parent / "outside.png").exists()


def test_real_pdf_selection_and_cache_keep_original_pages_and_export_features(tmp_path):
    source = tmp_path / "paper.pdf"
    source_pdf(source, 5)
    recognizer = FakeRecognizer()
    orch = Orchestrator(tmp_path / "workspace")
    orch._rapid_converter = RapidDocumentConverter(recognizer)
    options = ConvertOptions(ocr_mode="rapidocr",pages="2,5")
    result = orch.convert(source, options)
    assert result.ok, result.errors
    assert [p.number for p in result.ir.pages] == [2,5]
    assert {b.page for b in result.ir.blocks} == {2,5}
    assert len({b.id for b in result.ir.blocks}) == len(result.ir.blocks)
    assert recognizer.calls == 1
    assert orch.convert(source, options).ok and recognizer.calls == 1
    options.force = True
    assert orch.convert(source, options).ok and recognizer.calls == 2
    artifact = create_ocr_export(result.doc_dir)
    directory = result.doc_dir / "ocr" / artifact["id"]
    assert (directory / "editor.json").is_file()
    page = (directory / "index.html").read_text(encoding="utf-8")
    assert "data-latex" in page and "<table" in page and "data-page=\"5\"" in page
    assert "data-reader-protocol" in page
    with ZipFile(directory / "source.zip") as archive:
        ir = json.loads(archive.read("document.json"))
        assert any(b["content"].get("latex") for b in ir["blocks"])
        assert any(b["content"].get("html") for b in ir["blocks"])


def test_incomplete_result_does_not_replace_old_document(tmp_path):
    source=tmp_path / "paper.pdf"; source_pdf(source)
    orch=Orchestrator(tmp_path / "workspace")
    recognizer=FakeRecognizer(); orch._rapid_converter=RapidDocumentConverter(recognizer)
    first=orch.convert(source,ConvertOptions(ocr_mode="rapidocr"))
    before=(first.doc_dir/"document.json").read_bytes()
    recognizer.missing=True
    result=orch.convert(source,ConvertOptions(ocr_mode="rapidocr",force=True))
    assert not result.ok and any("缺页" in e for e in result.errors)
    assert (first.doc_dir/"document.json").read_bytes()==before


def test_rapid_mode_never_initializes_vl_or_uses_a_native_only_shortcut(tmp_path,monkeypatch):
    source=tmp_path / "paper.pdf"; source_pdf(source)
    orch=Orchestrator(tmp_path / "workspace")
    model=FakeRecognizer(); orch._rapid_converter=RapidDocumentConverter(model)
    monkeypatch.setattr(orch,"_resolve_recognizer",lambda *_: pytest.fail("VL must not initialize"))
    assert orch.convert(source,ConvertOptions(ocr_mode="rapidocr")).ok
    assert model.calls == 1


def test_native_action_flags_cannot_disable_formula_or_table_models():
    received=[]
    recognizer=RapidDocumentRecognizer()
    recognizer._engine=lambda data,**kwargs: received.append(kwargs)
    recognizer.recognize(b"pdf")
    assert received[0]["formula_enable"] is True and received[0]["table_enable"] is True


def test_generated_native_test_actor_is_valid_javascript(tmp_path):
    node=shutil.which("node")
    if not node:
        pytest.skip("Node required")
    runner=Path(__file__).resolve().parents[1]/"zotero/run_smoke.py"
    tree=ast.parse(runner.read_text(encoding="utf-8"))
    actors=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)
            and n.value.startswith("export class PaperLoomSmokeChild")]
    assert len(actors)==1
    path=tmp_path/"actor.mjs"; path.write_text(actors[0],encoding="utf-8")
    result=subprocess.run([node,"--check",str(path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
