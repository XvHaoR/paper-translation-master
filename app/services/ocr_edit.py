"""Block-preserving OCR corrections. No OCR model, network or LLM is used here.

Saved bundles carry their own base blocks and correction overlay. Each save creates
a new snapshot; source IDs/coordinates and original text are never reconstructed
from rendered Markdown. Images remain available for undo and offline translation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

from app.models.ir import Block, BlockContent, BlockType, DocumentIR, SourceInfo
from app.rendering.markdown_exporter import _render_block, render_markdown

EDIT_TYPES = {"document_title", "heading", "paragraph", "abstract", "caption", "list",
              "equation_block", "equation_inline", "table", "code", "reference", "footnote", "authors", "figure"}
ROLES = {"figure_caption": "caption", "table_caption": "caption"}


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def role(block: Block) -> str:
    if block.type is BlockType.CAPTION:
        label = block.layout_label.lower()
        if "table" in label or re.match(r"^\s*(?:table\b|表\s*\d)", block.content.text or "", re.I):
            return "table_caption"
        return "figure_caption"
    return block.type.value


def editor_payload(data: dict, blocks: list[Block]) -> dict:
    return {"schema": 1, "document": data.get("document", {}), "pages": data.get("pages", []),
            "blocks": [b.model_dump(mode="json") for b in blocks], "changes": {}, "insertions": [],
            "markdown": {b.id: _render_block(b) or "" for b in blocks}}


def active_blocks(payload: dict, changes: dict, insertions: list) -> list[Block]:
    """Validate a correction overlay, keeping identity/geometry outside its authority."""
    base = [Block.model_validate(b) for b in payload["blocks"]]
    ids = {b.id for b in base}
    base_ids = set(ids)
    if len(ids) != len(base) or len(base) > 20000:
        raise ValueError("OCR 内容块编号重复或数量过多")
    if not isinstance(changes, dict) or not set(changes) <= ids or not isinstance(insertions, list) or len(insertions) > 2000:
        raise ValueError("编辑内容不属于当前 OCR 版本")
    added: dict[str, list[Block]] = {}
    for entry in insertions:
        ident, before = entry.get("id", ""), entry.get("before", "")
        if not re.fullmatch(r"manual-[a-f0-9]{16,64}", ident) or ident in ids or before not in base_ids:
            raise ValueError("新增标题的位置或编号无效")
        ids.add(ident)
        text = entry.get("markdown", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("新增标题内容无效")
        match = re.match(r"^(#{1,6})\s+([^\n]+)\s*$", text.strip())
        if not match:
            raise ValueError("新增标题请使用 Markdown 标题，例如 ## Main")
        anchor = next(b for b in base if b.id == before)
        added.setdefault(before, []).append(Block(id=ident, type=BlockType.HEADING, page=anchor.page,
            heading_level=len(match[1]), layout_label="manual_heading", bbox=[],
            content=BlockContent(text=match[2]), source=SourceInfo(page=anchor.page)))
    result = []
    for original in base:
        result.extend(added.get(original.id, []))
        patch = changes.get(original.id, {})
        if not isinstance(patch, dict) or set(patch) - {"markdown", "role", "deleted"}:
            raise ValueError("编辑字段无效")
        if patch.get("deleted") is True:
            continue
        block = original.model_copy(deep=True)
        new_role = patch.get("role", role(original))
        kind = ROLES.get(new_role, new_role)
        if kind not in EDIT_TYPES and not patch:
            # Header/footer/unknown blocks remain in the snapshot for fidelity,
            # even though the reader does not expose editing controls for them.
            result.append(block)
            continue
        if kind not in EDIT_TYPES or (original.type is BlockType.FIGURE) != (kind == "figure"):
            raise ValueError("图片类型不能转换为文本")
        block.type = BlockType(kind)
        if new_role in ROLES:
            block.layout_label = "table_title" if new_role == "table_caption" else "figure_title"
        if "markdown" in patch:
            text = patch["markdown"]
            if not isinstance(text, str) or len(text) > 100000 or not text.strip():
                raise ValueError("内容不能为空；删除内容请使用删除按钮")
            if block.source.text is None:
                block.source.text = original.content.text or "\n".join(original.content.items or [])
            if kind == "figure":
                raise ValueError("图片通过删除按钮编辑")
            content = BlockContent(text=text)
            if kind in {"heading", "document_title"}:
                match = re.match(r"^(#{1,6})\s+", text)
                if match:
                    block.heading_level = len(match[1]); content.text = text[match.end():].strip()
            elif kind == "abstract":
                content.text = re.sub(r"^#{1,6}\s+Abstract\s*\n*", "", text, flags=re.I)
            elif kind in {"equation_block", "equation_inline"}:
                content = BlockContent(latex=text.strip().strip("$ \n"), display=kind == "equation_block")
            elif kind == "table":
                from app.rendering.markdown_util import markdown_table_to_html
                content.html = markdown_table_to_html(text) or (text if text.lstrip().lower().startswith("<table") else None)
                if not content.html:
                    raise ValueError("表格请使用 Markdown 表格或 HTML table")
            elif kind == "list":
                content.items = [re.sub(r"^\s*[-*+]\s+", "", line) for line in text.splitlines() if line.strip()]
                content.text = "\n".join(content.items)
            elif kind == "code":
                content.text = re.sub(r"^```[^\n]*\n|\n```\s*$", "", text)
            block.content = content
        result.append(block)
    for order, block in enumerate(result):
        block.order = order
    return result


def write_editor_files(payload: dict, blocks: list[Block], output: Path) -> dict:
    snapshot = {"schema_version": "1.0", "document": payload["document"], "pages": payload.get("pages", []),
                "blocks": [b.model_dump(mode="json") for b in blocks], "editor_prepared": True}
    source = canonical(snapshot)
    revision = hashlib.sha256(source).hexdigest()
    (output / "editor.json").write_bytes(canonical(payload))
    (output / "source.json").write_bytes(source)
    with zipfile.ZipFile(output / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("document.json", source)
        for image in sorted((output / "assets" / "media").glob("*")):
            if image.is_file():
                archive.write(image, image.relative_to(output).as_posix())
    return {"source_revision": revision}


def prepare_or_save(saved: Path, output: Path, workspace: Path, request: dict) -> dict:
    """Run in a short-lived local Python process, independent of the model service."""
    from app.services.translation_export import _export_blocks, reader_page, EXPORT_VERSION
    from app.services.offline_reader import copy_runtime
    manifest = json.loads((saved / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "ocr":
        raise ValueError("请在 OCR 页面编辑原文")
    output.mkdir(parents=True, exist_ok=False)
    if (saved / "editor.json").is_file():
        payload = json.loads((saved / "editor.json").read_text(encoding="utf-8"))
        media = saved / "assets" / "media"
        if media.is_dir():
            shutil.copytree(media, output / "assets" / "media")
    else:
        # Migrate old saved pages only when the cached source is exactly their
        # original IR version. Never silently substitute a later OCR generation.
        doc_id = manifest.get("document_id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", doc_id):
            raise ValueError("OCR 文献编号无效")
        doc_dir = workspace / "documents" / doc_id
        source = (doc_dir / "document.json").read_bytes()
        if hashlib.sha256(source).hexdigest() != manifest.get("source_sha256"):
            raise ValueError("旧附件对应的 OCR 源版本已不在本机；请使用包含编辑数据的新 OCR 版本")
        data = json.loads(source)
        blocks = _export_blocks(data, doc_dir, output)
        payload = editor_payload(data, blocks)
    if request.get("base_revision") != manifest.get("edit_revision"):
        raise ValueError("OCR 编辑版本已更新，请重新打开后再编辑")
    if request.get("save"):
        payload["changes"] = request.get("changes", {})
        payload["insertions"] = request.get("insertions", [])
    blocks = active_blocks(payload, payload["changes"], payload["insertions"])
    meta = write_editor_files(payload, blocks, output)
    revision = meta["source_revision"]
    edited = bool(payload["changes"] or payload["insertions"])
    markdown = render_markdown(blocks)
    (output / "ocr.md").write_text(markdown, encoding="utf-8")
    (output / "index.html").write_text(reader_page(manifest["title"], "en", markdown, blocks=blocks, editor=payload), encoding="utf-8")
    copy_runtime(output)
    shutil.copytree(saved / "assets" / "mathjax", output / "assets" / "mathjax")
    result = {**manifest, "schema": EXPORT_VERSION, "id": revision[:24], "edit_revision": revision,
              "edited": edited, **meta, "files": {"reader": "index.html", "markdown": "ocr.md"}}
    (output / "manifest.json").write_bytes(canonical(result))
    return result


def import_source(archive_path: Path, destination: Path, expected: str) -> None:
    """Import the restricted snapshot used for translation, not executable bundles."""
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("编辑版本标识无效")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(set(names)) != len(names) or len(entries) > 5000 or sum(e.file_size for e in entries) > 200 * 1024 * 1024:
            raise ValueError("编辑文档包过大或存在重复路径")
        for entry in entries:
            if entry.filename != "document.json" and not re.fullmatch(r"assets/media/[a-f0-9]{24}\.(png|jpg|jpeg|webp|gif)", entry.filename):
                raise ValueError("编辑包包含不安全的路径")
        if archive.getinfo("document.json").file_size > 20 * 1024 * 1024:
            raise ValueError("编辑文档过大")
        source = archive.read("document.json")
        if hashlib.sha256(source).hexdigest() != expected:
            raise ValueError("编辑文档校验失败")
        data = json.loads(source)
        ir = DocumentIR.model_validate(data)
        if not data.get("editor_prepared") or len(ir.blocks) > 20000 or len({b.id for b in ir.blocks}) != len(ir.blocks):
            raise ValueError("编辑文档结构无效")
        for block in ir.blocks:
            if block.type is BlockType.FIGURE and block.content.image_path not in names:
                raise ValueError("编辑包缺少引用的图片")
        destination.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            target = destination / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
