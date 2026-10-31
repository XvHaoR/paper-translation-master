"""Persistent exports survive restart and never publish partial translations."""
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.models.ir import Block, BlockContent, BlockType, DocumentIR, DocumentMeta
from app.server import create_app
from app.services import translator
from app.services.translation_export import create_translation, create_ocr_export, IncompleteTranslation, translation_path


CONFIG = {"llm_base_url": "http://localhost:9999/v1", "llm_model": "test", "lang_to": "zh"}


@pytest.fixture
def document(tmp_path):
    doc_dir = tmp_path / "documents" / "paper"
    doc_dir.mkdir(parents=True)
    ir = DocumentIR(document=DocumentMeta(id="paper", title="Test <paper>"), blocks=[
        Block(id="title", type=BlockType.DOCUMENT_TITLE, content=BlockContent(text="Test paper")),
        Block(id="p1", type=BlockType.PARAGRAPH, order=1, content=BlockContent(text="A $x^2$ sentence.")),
        Block(id="p2", type=BlockType.PARAGRAPH, order=2, content=BlockContent(text="Another paragraph.")),
        Block(id="eq", type=BlockType.EQUATION_BLOCK, order=3, content=BlockContent(latex="E=mc^2")),
        Block(id="table", type=BlockType.TABLE, order=4, content=BlockContent(text="| A | B |\n|---|---|\n| 1 | 2 |")),
        Block(id="list", type=BlockType.LIST, order=5, content=BlockContent(items=["one", "two"])),
        Block(id="figure", type=BlockType.FIGURE, order=6, content=BlockContent(image_path="assets/official/imgs/")),
    ])
    (doc_dir / "document.json").write_text(ir.model_dump_json(), encoding="utf-8")
    images = doc_dir / "assets" / "official" / "imgs"
    images.mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (4, 4)).save(images / "sample.png")
    return doc_dir


def fake_http(*args):
    prompt = args[3][0]["content"]
    inputs = translator.parse_batch_output(prompt.split("\n请把每条")[0], 10000)
    return "\n".join(f"[ID:{index}] 中文：{text}" for index, text in inputs.items())


def patch_translator(monkeypatch):
    original = translator.translate_batch
    calls = []

    def translate(*args, **kwargs):
        calls.append(1)
        return original(*args, **({"http_post": fake_http} | kwargs))

    monkeypatch.setattr(translator, "translate_batch", translate)
    return calls


def test_complete_offline_bundle_and_restart(document, tmp_path, monkeypatch):
    calls = patch_translator(monkeypatch)
    cache_file = tmp_path / "translate-cache.json"
    first = create_translation(document, CONFIG, translator.TranslateCache(cache_file))
    assert first["complete"] and not first["cached"]
    assert first["translated_blocks"] == 4
    bundle = document / "translations" / first["id"] / "bundle.zip"
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert {"translation.md", "original.md", "index.html", "manifest.json"} <= set(names)
        assert "assets/mathjax/tex-chtml-full.js" in names
        markdown = archive.read("translation.md").decode()
        assert "中文：" in markdown and "$x^2$" in markdown and "E=mc^2" in markdown
        assert "| 1 | 2 |" in markdown
        assert "one" in markdown and "- two" in markdown
        assert "中文：" not in archive.read("original.md").decode()
        assert any(n.startswith("assets/media/") for n in names)
        page = archive.read("index.html").decode()
        assert "fetch(" not in page and "runtime.js" not in page and "127.0.0.1" not in page
        assert "connect-src 'none'" in page
        assert "llm_api_key" not in archive.read("manifest.json").decode()
    # Full artifact hit works after process/cache restart, without any LLM call.
    monkeypatch.setattr(translator, "translate_batch", lambda *a: pytest.fail("must use saved artifact"))
    second = create_translation(document, CONFIG, translator.TranslateCache(cache_file))
    assert second["cached"] and second["id"] == first["id"] and len(calls) == 1


def test_structured_pairs_and_offline_actions_are_saved(document, tmp_path, monkeypatch):
    patch_translator(monkeypatch)
    source = json.loads((document / "document.json").read_text())
    source["blocks"][1].update(page=2, bbox=[.1,.2,.8,.4])
    (document / "document.json").write_text(json.dumps(source))
    artifact = create_translation(document, CONFIG, translator.TranslateCache(tmp_path / "cache.json"))
    page = (document / "translations" / artifact["id"] / "index.html").read_text(encoding="utf-8")
    assert 'data-block-id="p1" data-page="2"' in page
    assert 'data-bbox="[0.1, 0.2, 0.8, 0.4]"' in page
    assert 'class="pl-source"' in page and 'class="pl-target"' in page
    assert page.index('class="pl-source"') < page.index('class="pl-target"')
    assert 'data-translation-mode="bilingual"' in page and 'data-translation-mode="translation"' in page
    with zipfile.ZipFile(document / "translations" / artifact["id"] / "bundle.zip") as archive:
        assert {"assets/offline_reader.js", "assets/table_excel.js", "assets/offline_reader.css"} <= set(archive.namelist())
        assert 'href="assets/offline_reader.css"' in page
    assert json.loads((document / "document.json").read_text()) == source


def test_force_translation_bypasses_artifact_and_unit_caches(document, tmp_path, monkeypatch):
    original = translator.translate_batch
    calls = []
    def http(*args):
        calls.append(1)
        return fake_http(*args)
    monkeypatch.setattr(translator, "translate_batch", lambda *a, **kw: original(*a, **({"http_post": http} | kw)))
    cache = translator.TranslateCache(tmp_path / "cache.json")
    first = create_translation(document, CONFIG, cache)
    before = len(calls)
    second = create_translation(document, CONFIG, cache, force=True)
    assert len(calls) > before and second["id"] != first["id"]
    assert (document / "translations" / first["id"] / "index.html").is_file()


def test_display_upgrade_is_cache_only_and_never_calls_provider(document, tmp_path, monkeypatch):
    from app.services import translation_export
    original = translator.translate_batch
    monkeypatch.setattr(translator, "translate_batch", lambda *a, **kw: original(*a, **({"http_post": fake_http} | kw)))
    cache = translator.TranslateCache(tmp_path / "cache.json")
    first = create_translation(document, CONFIG, cache)
    monkeypatch.setattr(translation_export, "EXPORT_VERSION", 999)
    second = create_translation(document, CONFIG, cache, cache_only=True)
    assert second["complete"] and second["id"] != first["id"]
    monkeypatch.setattr(translation_export, "EXPORT_VERSION", 1000)
    with pytest.raises(IncompleteTranslation):
        create_translation(document, CONFIG, translator.TranslateCache(tmp_path / "empty-cache.json"), cache_only=True)


def test_forced_generation_retries_keep_the_new_result_without_old_cache(document, tmp_path, monkeypatch):
    calls = patch_translator(monkeypatch)
    cache = translator.TranslateCache(tmp_path / "cache.json")
    revision = "a" * 24
    first = create_translation(document, CONFIG, cache, force=True, revision=revision)
    before = len(calls)
    second = create_translation(document, CONFIG, cache, force=True, revision=revision)
    assert second["id"] == first["id"] and second["cached"] and len(calls) == before


def test_forced_generation_can_rebuild_from_its_new_unit_cache(document, tmp_path, monkeypatch):
    original = translator.translate_batch
    provider_calls = []
    def http(*args):
        provider_calls.append(1)
        return fake_http(*args)
    monkeypatch.setattr(translator, "translate_batch", lambda *a, **kw: original(*a, **({"http_post": http} | kw)))
    cache = translator.TranslateCache(tmp_path / "cache.json")
    revision = "b" * 24
    first = create_translation(document, CONFIG, cache, force=True, revision=revision)
    (document / "translations" / first["id"] / "bundle.zip").unlink()
    before = len(provider_calls)
    second = create_translation(document, CONFIG, cache, force=True, revision=revision)
    assert second["id"] == first["id"] and not second["cached"] and len(provider_calls) == before


def test_failed_translation_does_not_publish(document, tmp_path, monkeypatch):
    monkeypatch.setattr(translator, "translate_batch", lambda *a: {"results": {"p1": {"error": "rate limit"}}})
    with pytest.raises(IncompleteTranslation) as error:
        create_translation(document, CONFIG, translator.TranslateCache(tmp_path / "cache.json"))
    assert error.value.errors["p1"] == "rate limit"
    assert not list(document.glob("translations/*/bundle.zip"))


def test_bundle_metadata_excludes_endpoint_credentials(document, tmp_path, monkeypatch):
    patch_translator(monkeypatch)
    config = {**CONFIG, "llm_base_url": "http://localhost:9999/v1?key=url-secret", "llm_api_key": "api-secret"}
    result = create_translation(document, config, translator.TranslateCache(tmp_path / "cache.json"))
    manifest = (document / "translations" / result["id"] / "manifest.json").read_text(encoding="utf-8")
    assert "url-secret" not in manifest and "api-secret" not in manifest


def test_distinct_languages_and_source_versions(document, tmp_path, monkeypatch):
    patch_translator(monkeypatch)
    cache = translator.TranslateCache(tmp_path / "cache.json")
    first = create_translation(document, CONFIG, cache)
    english = create_translation(document, {**CONFIG, "lang_to": "en"}, cache)
    data = json.loads((document / "document.json").read_text())
    data["blocks"][1]["content"]["text"] = "Changed source"
    (document / "document.json").write_text(json.dumps(data))
    edited = create_translation(document, CONFIG, cache)
    assert len({first["id"], english["id"], edited["id"]}) == 3
    assert translation_path(document, first["id"], "translation.md").is_file()


def test_provider_change_does_not_reuse_other_providers_blocks(document, tmp_path, monkeypatch):
    original = translator.translate_batch
    calls = []

    def http(*args):
        calls.append(args[0])
        return fake_http(*args)

    monkeypatch.setattr(translator, "translate_batch", lambda *a: original(*a, http_post=http))
    cache = translator.TranslateCache(tmp_path / "cache.json")
    create_translation(document, CONFIG, cache)
    other = {**CONFIG, "llm_base_url": "http://localhost:9998/v1"}
    create_translation(document, other, cache)
    assert CONFIG["llm_base_url"] in calls and other["llm_base_url"] in calls


def test_concurrent_requests_publish_once(document, tmp_path, monkeypatch):
    calls = patch_translator(monkeypatch)
    cache = translator.TranslateCache(tmp_path / "cache.json")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: create_translation(document, CONFIG, cache), range(2)))
    assert len(calls) == 1
    assert {r["cached"] for r in results} == {True, False}


def test_unsafe_assets_and_html_are_excluded(document, tmp_path, monkeypatch):
    patch_translator(monkeypatch)
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not an image")
    data = json.loads((document / "document.json").read_text())
    data["blocks"][-1]["content"]["image_path"] = "../../secret.png"
    data["blocks"][1]["content"]["text"] = '<script>alert(1)</script><img src="x" onerror="alert(1)">'
    (document / "document.json").write_text(json.dumps(data))
    result = create_translation(document, CONFIG, translator.TranslateCache(tmp_path / "cache.json"))
    with zipfile.ZipFile(document / "translations" / result["id"] / "bundle.zip") as archive:
        page = archive.read("index.html").decode()
        assert "<script>alert" not in page and "onerror=" not in page
        assert not any(n.startswith("assets/media/") for n in archive.namelist())
    assert translation_path(document, "../paper", "manifest.json") is None
    assert translation_path(document, result["id"], "../document.json") is None


def test_api_export_download_and_read_without_settings(document, tmp_path, monkeypatch):
    patch_translator(monkeypatch)
    (tmp_path / "settings.json").write_text(json.dumps(CONFIG))
    app = create_app(Settings(workspace=tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/documents/paper/translations", json={"lang_to": "zh"})
        assert response.status_code == 200, response.text
        artifact = response.json()
        assert client.get(artifact["download_url"]).content.startswith(b"PK")
        (tmp_path / "settings.json").unlink()
        assert client.get(artifact["download_url"].replace("bundle.zip", "translation.md")).status_code == 200
        assert client.post("/api/documents/paper/translations", json={"lang_to": "../zh"}).status_code == 400
        assert client.post("/api/documents/missing/translations", json={}).status_code == 404


def test_api_partial_failure_is_actionable(document, tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps(CONFIG))
    monkeypatch.setattr(translator, "translate_batch", lambda *a: {"results": {}})
    with TestClient(create_app(Settings(workspace=tmp_path))) as client:
        response = client.post("/api/documents/paper/translations", json={})
        assert response.status_code == 422
        assert "p1" in response.json()["detail"]["errors"]


def test_edit_during_translation_cannot_publish_old_version(document, tmp_path, monkeypatch):
    original = translator.translate_batch

    def translate(*args):
        result = original(*args, http_post=fake_http)
        with (document / "document.json").open("a") as f:
            f.write("\n")
        return result

    monkeypatch.setattr(translator, "translate_batch", translate)
    with pytest.raises(ValueError, match="修改"):
        create_translation(document, CONFIG, translator.TranslateCache(tmp_path / "cache.json"))
    assert not list(document.glob("translations/*/bundle.zip"))


def test_ocr_export_is_independent_of_llm_and_reuses_saved_bundle(document, monkeypatch):
    monkeypatch.setattr(translator, "translate_batch", lambda *a: pytest.fail("OCR must never translate"))
    first = create_ocr_export(document)
    second = create_ocr_export(document)
    assert first["id"] == second["id"] and second["cached"]
    with zipfile.ZipFile(document / "ocr" / first["id"] / "bundle.zip") as archive:
        assert {"ocr.md", "index.html", "manifest.json"} <= set(archive.namelist())
        assert "translation.md" not in archive.namelist()
        assert "E=mc^2" in archive.read("ocr.md").decode()
        assert "| 1 | 2 |" in archive.read("ocr.md").decode()
        assert any(name.startswith("assets/media/") for name in archive.namelist())
        page = archive.read("index.html").decode()
        assert 'src="assets/mathjax/tex-chtml-full.js"' in page
        assert "fetch(" not in page and "127.0.0.1" not in page


def test_ocr_api_needs_no_translation_configuration(document, tmp_path, monkeypatch):
    monkeypatch.setattr(translator, "translate_batch", lambda *a: pytest.fail("OCR must never translate"))
    with TestClient(create_app(Settings(workspace=tmp_path))) as client:
        assert client.get("/api/zotero/status").json()["protocol"] == 2
        response = client.post("/api/documents/paper/ocr-export")
        assert response.status_code == 200, response.text
        assert client.get(response.json()["download_url"]).content.startswith(b"PK")
        assert client.post("/api/documents/missing/ocr-export").status_code == 404
        assert client.get("/api/documents/paper/ocr/invalid/bundle.zip").status_code == 404


def test_ocr_new_source_creates_version_without_deleting_saved_result(document):
    first = create_ocr_export(document)
    data = json.loads((document / "document.json").read_text())
    data["blocks"][1]["content"]["text"] = "A revised paragraph"
    (document / "document.json").write_text(json.dumps(data))
    second = create_ocr_export(document)
    assert first["id"] != second["id"]
    assert (document / "ocr" / first["id"] / "bundle.zip").is_file()
