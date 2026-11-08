import json
from pathlib import Path

import pytest

from app.models.ir import Block, BlockContent, BlockType, DocumentIR, DocumentMeta
from app.services import translator
from app.services.ocr_edit import prepare_or_save
from app.services.translation_export import create_ocr_export, create_translation
from app.services.translation_layout import synchronize, LegacyTargets


@pytest.fixture
def saved_pair(tmp_path, monkeypatch):
    doc = tmp_path / "documents" / "paper"
    doc.mkdir(parents=True)
    ir = DocumentIR(document=DocumentMeta(id="paper", title="Paper"), blocks=[
        Block(id="p", type=BlockType.PARAGRAPH, page=1, bbox=[.1,.2,.3,.4], content=BlockContent(text="A sentence with $x_i$.")),
        Block(id="q", type=BlockType.PARAGRAPH, page=1, order=1, content=BlockContent(text="Another sentence.")),
        Block(id="eq", type=BlockType.EQUATION_BLOCK, page=1, order=2, content=BlockContent(latex=r"\frac{a}{b}")),
        Block(id="table", type=BlockType.TABLE, page=1, order=3, content=BlockContent(html="<table><tr><td>1</td></tr></table>")),
        Block(id="empty", type=BlockType.PARAGRAPH, page=1, order=4, content=BlockContent(text=" ")),
        Block(id="footer", type=BlockType.PAGE_FOOTER, page=1, order=5, content=BlockContent(text="Journal footer")),
    ])
    (doc / "document.json").write_text(ir.model_dump_json(), encoding="utf-8")
    ocr = create_ocr_export(doc)
    monkeypatch.setattr(translator, "translate_batch", lambda *a, **kw: {"results": {
        b.id: {"text": "中文：" + b.content.text} for b in ir.blocks if b.content.text and b.content.text.strip() and b.type is not BlockType.PAGE_FOOTER
    }})
    translation = create_translation(doc, {"llm_base_url":"http://unused/v1", "llm_model":"test", "lang_to":"zh"}, translator.TranslateCache(tmp_path/"cache"))
    monkeypatch.setattr(translator, "translate_batch", lambda *a, **kw: pytest.fail("layout sync must remain offline"))
    return doc / "ocr" / ocr["id"], doc / "translations" / translation["id"], ocr


@pytest.mark.parametrize("legacy", [False, True])
def test_reclassification_updates_translation_without_losing_tex_tables_or_provenance(saved_pair, tmp_path, legacy):
    ocr, translation, manifest = saved_pair
    if legacy:
        (translation / "translation-state.json").unlink()
    original_page = (translation / "index.html").read_bytes()
    edited = tmp_path / "edited"
    revision = prepare_or_save(ocr, edited, tmp_path, {"save":True,"base_revision":manifest["edit_revision"],
        "changes":{"p":{"role":"abstract"},"q":{"role":"abstract"}},"insertions":[]})
    result = synchronize(translation, ocr, edited, tmp_path / "synced")
    assert result["changed"] and result["artifact"]["ocr_revision"] == revision["source_revision"]
    page = (Path(result["output"]) / "index.html").read_text(encoding="utf-8")
    assert page.count('data-role="abstract"') == 2
    assert 'data-latex="x_i"' in page and r'\frac{a}{b}' in page
    assert '<td>1</td>' in page and 'data-bbox="[0.1, 0.2, 0.3, 0.4]"' in page
    assert "中文：" in page
    assert (translation / "index.html").read_bytes() == original_page
    parser = LegacyTargets(); parser.feed(page)
    assert set(parser.targets) == {"p","q","eq","table"}
    assert sum(v.count("<h2>Abstract</h2>") for v in parser.targets.values()) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_content_changes_keep_translation_stale_even_when_other_roles_sync(saved_pair, tmp_path, legacy):
    ocr, translation, manifest = saved_pair
    if legacy:
        (translation / "translation-state.json").unlink()
    edited = tmp_path / "edited"
    prepare_or_save(ocr, edited, tmp_path, {"save":True,"base_revision":manifest["edit_revision"],
        "changes":{"p":{"role":"abstract"},"q":{"markdown":"A changed sentence."}},"insertions":[]})
    result = synchronize(translation, ocr, edited, tmp_path / "synced")
    assert result["changed"] and not result["artifact"].get("ocr_revision")
    pairs = json.loads((tmp_path / "synced" / "translation-state.json").read_text(encoding="utf-8"))
    assert not pairs["source_verified"]
    assert "Another sentence." in next(b for b in pairs["originals"] if b["id"] == "q")["content"]["text"]
    page = (tmp_path / "synced" / "index.html").read_text(encoding="utf-8")
    assert "A changed sentence." not in page  # Old English must still match its old translation.


def test_legacy_type_only_edits_are_verified_against_saved_english_instead_of_revision_only(saved_pair, tmp_path):
    ocr, translation, manifest = saved_pair
    (translation / "translation-state.json").unlink()
    meta = json.loads((translation / "manifest.json").read_text(encoding="utf-8"))
    meta["source_sha256"] = "old-generation"
    (translation / "manifest.json").write_text(json.dumps(meta),encoding="utf-8")
    edited = tmp_path / "edited"
    prepare_or_save(ocr, edited, tmp_path, {"save":True,"base_revision":manifest["edit_revision"],"changes":{"p":{"role":"abstract"}}})
    result = synchronize(translation, ocr, edited, tmp_path / "synced")
    assert result["changed"] and result["artifact"]["ocr_revision"]


def test_legacy_already_retyped_ocr_refreshes_translation_without_another_role_change(saved_pair, tmp_path):
    ocr, translation, manifest = saved_pair
    (translation / "translation-state.json").unlink()
    edited = tmp_path / "edited"
    current = prepare_or_save(ocr, edited, tmp_path, {"save":True,"base_revision":manifest["edit_revision"],"changes":{"p":{"role":"abstract"}}})
    result = synchronize(translation, edited, edited, tmp_path / "synced")
    assert result["changed"] and result["artifact"]["ocr_revision"] == current["source_revision"]


def test_legacy_changed_english_is_not_relabelled_or_marked_current(saved_pair, tmp_path):
    ocr, translation, manifest = saved_pair
    (translation / "translation-state.json").unlink()
    edited = tmp_path / "edited"
    prepare_or_save(ocr, edited, tmp_path, {"save":True,"base_revision":manifest["edit_revision"],
        "changes":{"p":{"role":"abstract"},"q":{"markdown":"Changed after translation."}}})
    second = tmp_path / "second"
    current = json.loads((edited / "manifest.json").read_text(encoding="utf-8"))
    prepare_or_save(edited, second, tmp_path, {"save":False,"base_revision":current["edit_revision"]})
    result = synchronize(translation, edited, second, tmp_path / "synced")
    assert result["changed"] and not result["artifact"].get("ocr_revision")
    page = (tmp_path / "synced" / "index.html").read_text(encoding="utf-8")
    assert "Changed after translation." not in page and "Another sentence." in page
