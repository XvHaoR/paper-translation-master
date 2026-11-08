"""Durable, self-contained translations for Zotero (independent of the web reader)."""
from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import bleach

from app.models.ir import Block, BlockType
from app.rendering.html_renderer import render_official_markdown
from app.rendering.markdown_exporter import render_markdown
from app.rendering.markdown_util import markdown_table_to_html, normalize_bare_math
from app.services import translator
from app.services.figure_export import export_figures
from app.services.offline_reader import render_sections, copy_runtime

EXPORT_VERSION = 13
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class IncompleteTranslation(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("部分段落翻译失败，已完成的翻译单元已缓存；重试即可继续")
        self.errors = errors


def _export_blocks(data: dict, doc_dir: Path, output: Path) -> list[Block]:
    """Normalize official-mode table/image payloads and copy only referenced images."""
    labels = {}
    raw_path = doc_dir / "raw" / "parser-output.json"
    if raw_path.is_file() and not data.get("editor_prepared"):
        # Older IRs retained the official JSON but discarded its exact category.
        # Recover that metadata by page/rectangle without re-running OCR or
        # changing the original document.json.
        from app.parsing.page_strategy import normalize_bbox
        try:
            for key, value in json.loads(raw_path.read_text(encoding="utf-8")).items():
                number = re.search(r"(\d+)$", key)
                page = value.get("res", value)
                if not number or not page.get("width") or not page.get("height"):
                    continue
                for entry in page.get("parsing_res_list", []):
                    box = entry.get("block_bbox", entry.get("bbox", []))
                    if len(box) == 4:
                        normalized = normalize_bbox(box, page["width"], page["height"])
                        labels[(int(number[1]), tuple(round(n, 6) for n in normalized))] = entry.get("block_label", entry.get("label", ""))
        except (ValueError, TypeError, AttributeError):
            labels = {}  # Optional metadata must not prevent reading a saved OCR.
    blocks = []
    for raw in data.get("blocks", []):
        block = Block.model_validate(raw)
        if not block.layout_label:
            block.layout_label = labels.get((block.page, tuple(round(n, 6) for n in block.bbox)), "")
            if block.type is BlockType.UNKNOWN and block.layout_label:
                from app.parsing.page_strategy import map_label_to_block_type
                block.type = BlockType(map_label_to_block_type(block.layout_label))
        if block.type is BlockType.TABLE and not block.content.html:
            block.content.html = markdown_table_to_html(block.content.text or "") or ""
        if block.type is BlockType.LIST and not block.content.text:
            block.content.text = "\n".join(block.content.items or [])
        if block.type not in (BlockType.CODE, BlockType.EQUATION_BLOCK, BlockType.EQUATION_INLINE):
            if block.content.text:
                block.content.text = normalize_bare_math(block.content.text)
            if block.content.items:
                block.content.items = [normalize_bare_math(item) for item in block.content.items]
        blocks.append(block)
    if data.get("editor_prepared"):
        # These images already contain the user's corrected composite figures.
        # Re-running grouping here could recreate a deliberately deleted panel.
        for block in blocks:
            if block.type is BlockType.FIGURE:
                relative = block.content.image_path or ""
                path = (doc_dir / relative).resolve()
                if not path.is_relative_to(doc_dir.resolve()) or not path.is_file():
                    raise ValueError("编辑文档引用的图片不存在")
                target = output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        return blocks
    from app.services.paper_semantics import prepare_blocks
    return export_figures(prepare_blocks(blocks, data, doc_dir), data, doc_dir, output)


def _safe_html(markdown: str) -> str:
    return bleach.clean(
        render_official_markdown(markdown),
        tags={"p", "h1", "h2", "h3", "h4", "h5", "h6", "em", "strong", "del",
              "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead", "tbody",
              "tr", "th", "td", "a", "img", "span", "div", "br", "hr", "sub", "sup"},
        attributes={"*": ["class", "translate", "data-latex"], "a": ["href", "title"],
                    "img": ["src", "alt"], "td": ["colspan", "rowspan"], "th": ["colspan", "rowspan"]},
        protocols=["http", "https", "mailto"], strip=True,
    )


def reader_page(title: str, language: str, markdown: str, original: str | None = None,
                *, blocks: list[Block] | None = None, originals: list[Block] | None = None, editor: dict | None = None, target_html: dict | None = None, source_html: dict | None = None) -> str:
    """A saved page has no runtime/service dependency and can be embedded by Zotero."""
    translated = original is not None
    filename = "translation.md" if translated else "ocr.md"
    label = "译文" if translated else "OCR"
    content = render_sections(blocks, originals, _safe_html, target_html=target_html, source_html=source_html) if blocks is not None else _safe_html(markdown)
    controls = ('<button type="button" data-translation-mode="bilingual">逐段双语</button> '
                '<button type="button" data-translation-mode="translation">仅译文</button>') if translated and blocks is not None else ""
    return f'''<!DOCTYPE html><html lang="{html.escape(language, quote=True)}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'none'">
<title>{html.escape(title)} — 论文翻译大师 {label}</title>
<style>html{{color-scheme:light dark}}body{{max-width:960px;margin:20px auto;padding:0 24px;font:17px/1.8 system-ui,sans-serif}}img{{max-width:100%}}table{{border-collapse:collapse;display:block;overflow:auto}}th,td{{border:1px solid #aaa;padding:6px 12px}}pre{{overflow:auto;background:#8881;padding:16px}}nav{{font-size:13px;padding:8px 0;border-bottom:1px solid #8884}}.math-display{{overflow:auto}}summary{{cursor:pointer}}a{{color:#3875d7}}</style>
<style>.pl-block{{position:relative;padding:8px;margin:10px 0;border:1px solid transparent;scroll-margin-top:35px}}.pl-block.selected{{border-color:#6264ff;background:#7777ff08}}.pl-source{{color:inherit;margin-bottom:12px}}.pl-source+.pl-target{{border-top:1px dashed #8884;padding-top:10px;color:var(--pl-translation-color,#235f91)}}body[data-translation-mode="translation"] .pl-source{{display:none}}body[data-translation-mode="translation"] .pl-target{{border:0;padding-top:0}}.pl-block-tools{{position:absolute;right:4px;top:-18px;z-index:3;background:Canvas;font:12px system-ui;display:flex;gap:6px;justify-content:flex-end;opacity:0}}.pl-block:hover>.pl-block-tools,.pl-block.selected>.pl-block-tools,.pl-block:focus-within>.pl-block-tools{{opacity:1}}button{{cursor:pointer;border:1px solid #8885;border-radius:4px;padding:3px 6px;background:transparent;color:inherit}}[data-latex]{{position:relative}}.pl-math-copy{{font:11px system-ui;opacity:0;position:absolute;right:0;top:100%;z-index:5;background:Canvas;color:CanvasText;white-space:nowrap}}[data-latex]:hover>.pl-math-copy,.pl-math-copy:focus{{opacity:1}}body.pl-focus.has-selection .pl-block:not(.selected){{opacity:.3}}#pl-status{{font:12px system-ui;color:#a33}}@media(prefers-color-scheme:dark){{.pl-source+.pl-target{{color:#abd6ff}}}}</style>
<script>window.MathJax={{tex:{{inlineMath:[['\\\\(','\\\\)']],displayMath:[['\\\\[','\\\\]']] }},options:{{enableMenu:false}}}};</script>
<link rel="stylesheet" href="assets/offline_reader.css"><script defer src="assets/mathjax/tex-chtml-full.js"></script><script defer src="assets/table_excel.js"></script><script defer src="assets/offline_reader.js"></script><script defer src="assets/ocr_editor.js"></script></head><body data-reader-protocol="paperloom.saved-reader/1" data-export-schema="{EXPORT_VERSION}" data-translation-mode="bilingual">
<nav><span hidden aria-hidden="true" data-reader-legacy-marker="PaperLoom">PaperLoom</span>论文翻译大师 {label} · 已保存 · <a href="{filename}">{label} Markdown</a> {controls}</nav><div id="pl-status" role="status"></div>
<main>{content}</main>
{('<script type="application/json" id="pl-editor-data">' + json.dumps(editor, ensure_ascii=False).replace('<', chr(92) + 'u003c') + '</script>') if editor else ''}
{('<hr><details><summary>查看原文</summary>' + _safe_html(original) + '</details>') if translated and blocks is None else ''}
</body></html>'''


def _zip_bundle(directory: Path) -> None:
    with zipfile.ZipFile(directory / "bundle.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name != "bundle.zip":
                bundle.write(path, path.relative_to(directory).as_posix())


def create_ocr_export(doc_dir: Path) -> dict:
    """Persist the OCR version without checking LLM settings or translating."""
    doc_dir = doc_dir.resolve()
    source = (doc_dir / "document.json").read_bytes()
    identity = {"schema": EXPORT_VERSION, "kind": "ocr", "source_sha256": hashlib.sha256(source).hexdigest()}
    export_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    target = doc_dir / "ocr" / export_id
    with _locks_guard:
        lock = _locks.setdefault(str(target), threading.Lock())
    with lock:
        if (target / "bundle.zip").is_file() and (target / "manifest.json").is_file():
            return {**json.loads((target / "manifest.json").read_text(encoding="utf-8")), "cached": True}
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".ocr-", dir=target.parent))
        try:
            data = json.loads(source)
            blocks = _export_blocks(data, doc_dir, staging)
            from app.services.ocr_edit import editor_payload, write_editor_files
            editor = editor_payload(data, blocks)
            editor_meta = write_editor_files(editor, blocks, staging)
            markdown = render_markdown(blocks)
            title = data.get("document", {}).get("title") or "OCR 文献"
            (staging / "ocr.md").write_text(markdown, encoding="utf-8")
            (staging / "index.html").write_text(reader_page(title, "en", markdown, blocks=blocks, editor=editor), encoding="utf-8")
            copy_runtime(staging)
            shutil.copytree(Path(__file__).resolve().parents[1] / "static" / "mathjax", staging / "assets" / "mathjax")
            manifest = {**identity, "id": export_id, "document_id": data.get("document", {}).get("id", doc_dir.name),
                        "title": title, "complete": True, "created_at": datetime.now(timezone.utc).isoformat(),
                        **editor_meta, "edit_revision": editor_meta["source_revision"], "edited": False,
                        "files": {"reader": "index.html", "markdown": "ocr.md"}}
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            _zip_bundle(staging)
            if (doc_dir / "document.json").read_bytes() != source:
                raise ValueError("文档在保存 OCR 版本时已修改，请重试")
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
            return {**manifest, "cached": False}
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def create_translation(doc_dir: Path, config: dict, cache: translator.TranslateCache, *, force: bool = False, cache_only: bool = False, revision: str | None = None, source_dir: Path | None = None) -> dict:
    """Return an existing artifact or translate and atomically publish a complete bundle.

    Source IR, provider, model and language identify a version. API keys never enter
    manifests. Errors leave the previous version intact and block-cache work reusable.
    """
    if force and cache_only:
        raise ValueError("只升级显示时不能要求重新翻译")
    doc_dir = doc_dir.resolve()
    input_dir = source_dir.resolve() if source_dir else doc_dir
    source = (input_dir / "document.json").read_bytes()
    identity = {
        "schema": EXPORT_VERSION,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        # Custom endpoint URLs sometimes carry credentials in their query or
        # userinfo. Identify them without putting those credentials in bundles.
        "provider_sha256": hashlib.sha256(config.get("llm_base_url", "").rstrip("/").encode()).hexdigest(),
        "model": config.get("llm_model", ""),
        "lang_to": config.get("lang_to") or "zh",
    }
    if source_dir:
        identity["ocr_revision"] = hashlib.sha256(source).hexdigest()
    if force:
        import secrets
        if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{24,64}", revision)):
            raise ValueError("翻译版本标识无效")
        identity["revision"] = revision or secrets.token_hex(12)
    export_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    target = doc_dir / "translations" / export_id
    with _locks_guard:
        lock = _locks.setdefault(str(target), threading.Lock())
    with lock:
        if (target / "bundle.zip").is_file() and (target / "manifest.json").is_file():
            return {**json.loads((target / "manifest.json").read_text(encoding="utf-8")), "cached": True}
        if not translator.translate_enabled(config):
            raise ValueError("翻译未配置：请在 论文翻译大师 翻译设置填写服务地址与模型")
        data = json.loads(source)
        # Translator consumes IR on disk. Normalized list text must also be visible
        # there, so translate_batch handles items-only lists itself.
        wanted = [b["id"] for b in data.get("blocks", [])
                  if b.get("type") in translator.TRANSLATABLE_TYPES and translator._block_text(b)]
        if force:
            class FreshCache:
                # A cleared translation starts a new namespace, but retries of
                # that same generation must keep completed translation units.
                def get(self, key):
                    return cache.get("revision:" + identity["revision"] + ":" + key)
                def put(self, key, value):
                    cache.put("revision:" + identity["revision"] + ":" + key, value)
            result = translator.translate_batch(input_dir, wanted, config, FreshCache())["results"]
        elif cache_only:
            def offline_only(*_args):
                raise ValueError("旧译文缓存不完整，无法只升级显示；原译文已保留")
            result = translator.translate_batch(input_dir, wanted, config, cache, http_post=offline_only)["results"]
        else:
            result = translator.translate_batch(input_dir, wanted, config, cache)["results"]
        errors = {bid: result.get(bid, {}).get("error", "缺少译文") for bid in wanted
                  if not result.get(bid, {}).get("text", "").strip()}
        if errors:
            raise IncompleteTranslation(errors)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".export-", dir=target.parent))
        try:
            blocks = _export_blocks(data, input_dir, staging)
            original = render_markdown(blocks)
            translated = []
            for block in blocks:
                copy = block.model_copy(deep=True)
                if block.id in result:
                    copy.content.text = result[block.id]["text"]
                    if copy.type is BlockType.LIST:
                        copy.content.items = copy.content.text.splitlines()
                translated.append(copy)
            markdown = render_markdown(translated)
            # Persist pairs so later classification changes can be applied
            # offline, without translating again or parsing rendered formulas.
            (staging / "translation-state.json").write_text(json.dumps({
                "schema": 1, "originals": [b.model_dump(mode="json") for b in blocks],
                "translated": [b.model_dump(mode="json") for b in translated],
            }, ensure_ascii=False), encoding="utf-8")
            (staging / "translation.md").write_text(markdown, encoding="utf-8")
            (staging / "original.md").write_text(original, encoding="utf-8")
            title = data.get("document", {}).get("title") or "论文翻译大师 译文"
            lang = identity["lang_to"]
            page = reader_page(title, lang, markdown, original, blocks=translated, originals=blocks)
            (staging / "index.html").write_text(page, encoding="utf-8")
            copy_runtime(staging)
            static = Path(__file__).resolve().parents[1] / "static" / "mathjax"
            shutil.copytree(static, staging / "assets" / "mathjax")
            manifest = {
                **identity, "id": export_id, "document_id": data.get("document", {}).get("id", doc_dir.name),
                "title": title, "created_at": datetime.now(timezone.utc).isoformat(),
                "translated_blocks": len(wanted), "complete": True,
                "files": {"markdown": "translation.md", "reader": "index.html", "original": "original.md"},
            }
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            _zip_bundle(staging)
            # Recheck before publishing so edits during a slow LLM call cannot
            # produce a translation mislabeled as the earlier IR version.
            if (input_dir / "document.json").read_bytes() != source:
                raise ValueError("文档在翻译期间已修改，请重试；已完成译文缓存保留")
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
            return {**manifest, "cached": False}
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def translation_path(doc_dir: Path, export_id: str, filename: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{24}", export_id):
        return None
    if filename not in {"bundle.zip", "manifest.json", "translation.md", "original.md", "index.html"}:
        return None
    path = doc_dir / "translations" / export_id / filename
    return path if path.is_file() else None
