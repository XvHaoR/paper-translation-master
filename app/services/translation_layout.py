"""Apply OCR classification to saved translations without an OCR/LLM service.

Only matching block identities and unchanged source content are eligible. Text
corrections still mark translations stale; classification alone does not.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil

from app.models.ir import Block
from app.services.ocr_edit import canonical, active_blocks


class LegacyTargets(HTMLParser):
    """Capture target fragments verbatim, without HTML-to-Markdown conversion."""
    def __init__(self, part="pl-target"):
        super().__init__(convert_charrefs=False)
        self.targets = {}
        self.block = None
        self.depth = 0
        self.parts = []
        self.part = part
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "section" and "pl-block" in values.get("class", "").split():
            self.block = values.get("data-block-id")
            self.metadata[self.block] = values
        if not self.depth and self.block and self.part in values.get("class", "").split():
            self.depth = 1
            self.parts = []
        elif self.depth:
            self.parts.append(self.get_starttag_text())
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if self.depth:
                self.parts.append(f"</{tag}>")
            else:
                if self.block in self.targets:
                    raise ValueError("译文块编号重复")
                self.targets[self.block] = "".join(self.parts)
        if tag == "section":
            self.block = None

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def same_content(left, right):
    return left.content.model_dump() == right.content.model_dump()


def signature(fragment):
    # Presentation wrappers change with the role; math and textual content must
    # still match. No HTML-to-text conversion that could discard TeX is used.
    fragment = re.sub(r'<h2>Abstract</h2>', '', fragment, flags=re.I)
    fragment = re.sub(r'</?(?:p|h[1-6])(?:\s[^>]*)?>', '', fragment, flags=re.I)
    return hashlib.sha256(re.sub(r'>\s+<', '><', fragment).strip().encode()).hexdigest()


def synchronize(saved: Path, old_ocr: Path, new_ocr: Path, output: Path, replacements=None) -> dict:
    from app.services.translation_export import reader_page, EXPORT_VERSION, _safe_html
    from app.services.offline_reader import copy_runtime
    from app.rendering.markdown_exporter import render_markdown, SKIP_TYPES
    from app.rendering.paper_blocks import block_html
    from app.models.ir import BlockType, BlockContent, SourceInfo
    from app.services.translator import TRANSLATABLE_TYPES

    manifest = json.loads((saved / "manifest.json").read_text(encoding="utf-8"))
    previous = json.loads((old_ocr / "manifest.json").read_text(encoding="utf-8"))
    current = json.loads((new_ocr / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("document_id") != current.get("document_id") or previous.get("document_id") != current.get("document_id"):
        raise ValueError("译文不属于当前 OCR 文献")
    new = json.loads((new_ocr / "source.json").read_text(encoding="utf-8"))
    new_blocks = {b.id: b for b in map(Block.model_validate, new["blocks"])}
    snapshot = saved / "translation-state.json"
    targets = None
    sources = None
    signatures = None
    if snapshot.is_file():
        pairs = json.loads(snapshot.read_text(encoding="utf-8"))
        originals = list(map(Block.model_validate, pairs["originals"]))
        translated = list(map(Block.model_validate, pairs["translated"]))
        targets = pairs.get("target_html")
        sources = pairs.get("source_html")
        signatures = pairs.get("source_signatures")
        was_current = pairs.get("baseline_verified", True)
    else:
        parser = LegacyTargets()
        source_parser = LegacyTargets("pl-source")
        page = (saved / "index.html").read_text(encoding="utf-8")
        parser.feed(page); source_parser.feed(page)
        targets = parser.targets
        sources = source_parser.targets
        signatures = {ident: signature(sources.get(ident, fragment)) for ident, fragment in targets.items()}
        originals = []
        for order, (ident, fragment) in enumerate(targets.items()):
            attrs = parser.metadata[ident]
            originals.append(Block(id=ident, type=BlockType(attrs["data-block-type"]),
                layout_label="table_title" if attrs.get("data-role") == "table_caption" else "figure_title" if attrs.get("data-role") == "figure_caption" else "",
                page=int(attrs["data-page"]), order=order, bbox=json.loads(attrs["data-bbox"]),
                content=BlockContent(text=sources.get(ident, fragment)),
                source=SourceInfo(page=int(attrs["data-page"]), text=attrs.get("data-source-text") or None,
                                  regions=json.loads(attrs.get("data-regions", "[]")))))
        translated = [b.model_copy(deep=True) for b in originals]
        was_current = True  # Each legacy paragraph is verified below by its saved English HTML.
        # Prefer the exact embedded English IR when its revision is verifiable.
        # This also avoids mistaking old/new math HTML formatting for a text edit.
        overlay = old_ocr / "editor.json"
        if overlay.is_file():
            payload = json.loads(overlay.read_text(encoding="utf-8"))
            baseline = None
            if manifest.get("ocr_revision") and manifest["ocr_revision"] == previous.get("source_revision"):
                baseline = active_blocks(payload, payload["changes"], payload["insertions"])
            elif not manifest.get("ocr_revision") and manifest.get("source_sha256") == previous.get("source_sha256") and manifest.get("schema") == previous.get("schema"):
                baseline = active_blocks(payload, {}, [])
            if baseline is not None and {b.id for b in baseline if b.type not in SKIP_TYPES and block_html(b, _safe_html).strip()} == set(targets):
                originals = baseline
                translated = [b.model_copy(deep=True) for b in originals]
                signatures = None
    def matches(original, newer):
        if signatures is not None:
            return signatures.get(original.id) == signature(block_html(newer, _safe_html))
        return same_content(original, newer)
    source_by_id = {b.id: b for b in originals}
    translated_by_id = {b.id: b for b in translated}
    originals, translated, pending = [], [], []
    missing_before = set(manifest.get("pending_new_blocks", []))
    missing_now = []
    replacements = replacements or {}
    for ident, newer in new_blocks.items():
        original, target = source_by_id.get(ident), translated_by_id.get(ident)
        requires_text = newer.type.value in TRANSLATABLE_TYPES and bool((newer.content.text or "".join(newer.content.items or [])).strip())
        equal = original and target and matches(original, newer) and ident not in missing_before
        pair = newer.model_copy(deep=True)
        output_block = newer.model_copy(deep=True)
        if ident in replacements or not requires_text:
            if ident in replacements:
                output_block.content.text = replacements[ident]
                if newer.type is BlockType.LIST:
                    output_block.content.items = replacements[ident].splitlines()
            for fragments in (targets, sources):
                if fragments is not None:
                    fragments.pop(ident, None)
            if signatures is not None:
                signatures[ident] = signature(block_html(newer, _safe_html))
        elif equal:
            output_block.content = target.content.model_copy(deep=True)
        else:
            pending.append(ident)
            if original and target and ident not in missing_before:
                # Keep the previous English with its actual translation until the user accepts a refresh.
                pair.content, output_block.content = original.content, target.content
            else:
                missing_now.append(ident)
                output_block.content = BlockContent(text="〔待翻译〕", items=["〔待翻译〕"] if newer.type is BlockType.LIST else None)
        originals.append(pair)
        translated.append(output_block)
    if not replacements and manifest.get("layout_revision") == current["source_revision"]:
        return {"changed": False, "pending": pending}
    if manifest.get("ocr_revision") == current["source_revision"] and not pending and not replacements:
        return {"changed": False}
    all_current = not pending
    identity = {"base": manifest["id"], "layout_revision": current["source_revision"]}
    if replacements:
        identity["replacements"] = replacements
    ident = hashlib.sha256(canonical(identity)).hexdigest()[:24]
    shutil.copytree(saved, output, ignore=shutil.ignore_patterns("bundle.zip"))
    for folder in ("media", "crops"):
        assets = new_ocr / "assets" / folder
        if assets.is_dir():
            shutil.copytree(assets, output / "assets" / folder, dirs_exist_ok=True)
    pairs = {"schema": 1, "source_verified": all_current, "baseline_verified": was_current,
             "originals": [b.model_dump(mode="json") for b in originals],
             "translated": [b.model_dump(mode="json") for b in translated]}
    if targets is not None:
        pairs["target_html"] = targets
        pairs["source_html"] = sources
        pairs["source_signatures"] = signatures
    (output / "translation-state.json").write_bytes(canonical(pairs))
    def markdown_for(blocks, fragments):
        if fragments is None:
            return render_markdown(blocks)
        from app.rendering.paper_blocks import retype_fragment
        # Raw inline HTML is valid Markdown and preserves legacy math, tables and links.
        return "\n\n".join(retype_fragment(fragments[b.id], b, first_abstract=False) if b.id in fragments
                           else render_markdown([b]) for b in blocks if b.type not in SKIP_TYPES)
    markdown = markdown_for(translated, targets)
    original_md = markdown_for(originals, sources)
    (output / "translation.md").write_text(markdown, encoding="utf-8")
    (output / "original.md").write_text(original_md, encoding="utf-8")
    (output / "index.html").write_text(reader_page(manifest["title"], manifest.get("lang_to", "zh"), markdown,
        original_md, blocks=translated, originals=originals, target_html=targets, source_html=sources), encoding="utf-8")
    copy_runtime(output)
    result = {**manifest, "id": ident, "schema": EXPORT_VERSION, "layout_revision": current["source_revision"],
              "pending_blocks": pending, "pending_new_blocks": missing_now, "complete": all_current}
    if all_current:
        result["ocr_revision"] = current["source_revision"]
    (output / "manifest.json").write_bytes(canonical(result))
    return {"changed": True, "pending": pending, "artifact": result, "output": str(output)}
