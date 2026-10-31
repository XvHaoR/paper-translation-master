"""OCR correction snapshots retain provenance and feed translation/export directly."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.models.ir import Block, BlockContent, BlockType, DocumentIR, DocumentMeta, SourceInfo
from app.parsing.page_strategy import map_label_to_block_type
from app.server import create_app
from app.services.ocr_edit import active_blocks, canonical, import_source, prepare_or_save
from app.services.translation_export import create_ocr_export, create_translation
from app.services import translator


@pytest.fixture
def paper(tmp_path):
    root = tmp_path / "documents" / "paper"
    root.mkdir(parents=True)
    (root / "assets").mkdir()
    from PIL import Image
    Image.new("RGB", (20, 10), "blue").save(root / "assets" / "image.png")
    ir = DocumentIR(document=DocumentMeta(id="paper", title="A paper"), blocks=[
        Block(id="p", type=BlockType.PARAGRAPH, page=1, bbox=[.1,.2,.4,.6], content=BlockContent(text="The original paragraph continues in another column.")),
        Block(id="fig", type=BlockType.FIGURE, page=1, order=1, bbox=[.5,.2,.9,.6], content=BlockContent(image_path="assets/image.png")),
        Block(id="caption", type=BlockType.CAPTION, layout_label="figure_title", page=1, order=2, content=BlockContent(text="Figure 1. A caption.")),
        Block(id="tabcap", type=BlockType.CAPTION, layout_label="table_title", page=2, order=3, content=BlockContent(text="Table 1. An overview.")),
    ])
    (root / "document.json").write_text(ir.model_dump_json(), encoding="utf-8")
    artifact = create_ocr_export(root)
    return root, root / "ocr" / artifact["id"], artifact


def test_official_types_survive_export_and_are_labeled(paper):
    root, saved, artifact = paper
    assert map_label_to_block_type("table_title") == "caption"
    payload = json.loads((saved / "editor.json").read_text(encoding="utf-8"))
    assert payload["blocks"][2]["layout_label"] == "figure_title"
    assert payload["blocks"][3]["layout_label"] == "table_title"
    page = (saved / "index.html").read_text(encoding="utf-8")
    assert 'data-role="figure_caption"' in page and 'data-role="table_caption"' in page
    assert '<span class="pl-role">正文</span>' in page
    assert "表注" in page and "图注" in page


def test_old_unknown_table_title_is_recovered_from_official_metadata(paper):
    root, _, _ = paper
    source = json.loads((root / "document.json").read_text(encoding="utf-8"))
    block = source["blocks"][-1]
    block.update(type="unknown", layout_label="", bbox=[.1,.2,.8,.3])
    original = canonical(source)
    (root / "document.json").write_bytes(original)
    (root / "raw").mkdir()
    (root / "raw" / "parser-output.json").write_bytes(canonical({"page_002":{"res":{
        "width":100,"height":100,"parsing_res_list":[{"block_label":"table_title","block_bbox":[10,20,80,30]}]}}}))
    artifact = create_ocr_export(root)
    page = (root / "ocr" / artifact["id"] / "index.html").read_text(encoding="utf-8")
    assert 'data-role="table_caption"' in page and "Table 1. An overview." in page
    assert (root / "document.json").read_bytes() == original


def test_edit_delete_insert_and_restore_keep_original_geometry(paper, tmp_path):
    root, saved, artifact = paper
    original = (root / "document.json").read_bytes()
    payload = json.loads((saved / "editor.json").read_text(encoding="utf-8"))
    fig = next(b["id"] for b in payload["blocks"] if b["type"] == "figure")
    request = {"save": True, "base_revision": artifact["edit_revision"],
        "changes": {"p": {"markdown": "Corrected **Markdown** with $x^2$.", "role": "paragraph"}, fig: {"deleted": True},
                    "caption": {"role": "table_caption", "markdown": "Table 2. Corrected caption."}},
        "insertions": [{"id": "manual-" + "a"*24, "before": "p", "markdown": "## Main"}]}
    output = tmp_path / "edited"
    result = prepare_or_save(saved, output, tmp_path, request)
    assert result["edited"] and result["edit_revision"] != artifact["edit_revision"]
    source = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert next(b for b in source["blocks"] if b["id"] == "p")["bbox"] == [.1,.2,.4,.6]
    paragraph = next(b for b in source["blocks"] if b["id"] == "p")
    assert paragraph["source"]["text"] == payload["blocks"][0]["content"]["text"]
    assert source["blocks"][0]["content"]["text"] == "Main" and source["blocks"][0]["bbox"] == []
    assert fig not in {b["id"] for b in source["blocks"]}
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "<h2>Main</h2>" in page and "<strong>Markdown</strong>" in page
    assert 'data-source-text="The original paragraph' in page and 'data-role="table_caption"' in page
    md = (output / "ocr.md").read_text(encoding="utf-8")
    assert "## Main" in md and "assets/media" not in md
    assert (root / "document.json").read_bytes() == original
    assert next((output / "assets" / "media").glob("*.png")).is_file()  # recoverable asset
    restored = tmp_path / "restored"
    reset = prepare_or_save(output, restored, tmp_path, {"save": True, "base_revision": result["edit_revision"], "changes": {}, "insertions": []})
    assert not reset["edited"] and "assets/media" in (restored / "ocr.md").read_text(encoding="utf-8")
    assert "## Main" not in (restored / "ocr.md").read_text(encoding="utf-8")


def test_stale_edits_and_identity_overrides_are_rejected(paper, tmp_path):
    _, saved, artifact = paper
    with pytest.raises(ValueError, match="版本已更新"):
        prepare_or_save(saved, tmp_path / "wrong", tmp_path, {"save": True, "base_revision": "old"})
    payload = json.loads((saved / "editor.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="字段无效"):
        active_blocks(payload, {"p": {"bbox": [0,0,1,1]}}, [])
    with pytest.raises(ValueError, match="不属于"):
        active_blocks(payload, {"unknown": {"deleted": True}}, [])
    payload["blocks"].append(Block(id="footer",type=BlockType.PAGE_FOOTER,content=BlockContent(text="footer")).model_dump(mode="json"))
    assert any(block.id=="footer" for block in active_blocks(payload,{},[]))


def test_legacy_bundle_migrates_without_ocr(paper, tmp_path):
    root, saved, artifact = paper
    (saved / "editor.json").unlink()
    manifest = json.loads((saved / "manifest.json").read_text())
    del manifest["edit_revision"]
    (saved / "manifest.json").write_text(json.dumps(manifest))
    result = prepare_or_save(saved, tmp_path / "migration", tmp_path, {})
    assert not result["edited"] and (tmp_path / "migration" / "editor.json").is_file()


def test_edited_source_translates_after_server_source_cache_was_removed(paper, tmp_path, monkeypatch):
    root, saved, artifact = paper
    edited = tmp_path / "corrected"
    result = prepare_or_save(saved, edited, tmp_path, {"save": True, "base_revision": artifact["edit_revision"],
        "changes": {"p": {"markdown": "This manually corrected sentence must be translated."}},
        "insertions": [{"id": "manual-"+"a"*24, "before": "p", "markdown": "## Main"}]})
    original = translator.translate_batch
    seen = []
    def fake_http(*args):
        prompt = args[3][0]["content"]
        seen.append(prompt)
        inputs = translator.parse_batch_output(prompt.split("\n请把每条")[0], 10000)
        return "\n".join(f"[ID:{i}] 中文：{text}" for i,text in inputs.items())
    monkeypatch.setattr(translator, "translate_batch", lambda *a, **kw: original(*a, **({"http_post":fake_http}|kw)))
    workspace = tmp_path / "empty-server"
    settings = Settings(workspace=workspace)
    (workspace / "settings.json").write_text(json.dumps({"llm_base_url":"http://localhost:1/v1","llm_model":"test"}))
    client = TestClient(create_app(settings))
    upload = client.post("/api/documents/paper/edited-source", data={"revision":result["source_revision"]},
        files={"upload": ("source.zip", (edited / "source.zip").read_bytes(), "application/zip")})
    assert upload.status_code == 200, upload.text
    translated = client.post("/api/documents/paper/translations", json={"source_revision":result["source_revision"],"lang_to":"zh"})
    assert translated.status_code == 200, translated.text
    manifest = translated.json()
    assert manifest["ocr_revision"] == result["source_revision"]
    assert any("manually corrected sentence" in prompt for prompt in seen)
    assert any("Main" in prompt for prompt in seen)
    assert client.get(manifest["download_url"]).status_code == 200
    count = len(seen)
    repeated = client.post("/api/documents/paper/translations", json={"source_revision":result["source_revision"],"lang_to":"zh"})
    assert repeated.json()["cached"] and len(seen) == count


@pytest.mark.parametrize("name", ["../escape.png", "assets/media/code.js", "C:/Windows/escape.png"])
def test_snapshot_import_rejects_unsafe_paths(paper, tmp_path, name):
    _, saved, artifact = paper
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("document.json", (saved / "source.json").read_bytes())
        archive.writestr(name, b"bad")
    with pytest.raises(ValueError, match="不安全"):
        import_source(bad, tmp_path / "bad-import", artifact["source_revision"])
