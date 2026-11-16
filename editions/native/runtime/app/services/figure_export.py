"""Keep each figure asset unique and retain composite figures from the source PDF.

PaddleOCR's `large` policy keeps detected outer boxes; it cannot invent a
missing outer box. When a single numbered caption follows consecutive panel
blocks, retain their original PDF rectangle, including subplot labels/legends.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil

from app.models.ir import Block, BlockType

_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?\.?\s*\d+|图\s*\d+)", re.I)
_IMAGE_BOX = re.compile(r"_box_([\d.]+)_([\d.]+)_([\d.]+)_([\d.]+)\.")
_RASTER = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _asset(root: Path, relative: str) -> Path | None:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.suffix.lower() not in _RASTER:
        return None
    return path


def _figure_asset(block: Block, sizes: dict, root: Path) -> list[Path]:
    relative = block.content.image_path or ""
    directory = (root / relative).resolve()
    if not directory.is_relative_to(root.resolve()):
        return []
    if not directory.is_dir():
        path = _asset(root, relative)
        return [path] if path else []
    candidates = [p for p in sorted(directory.iterdir()) if _asset(root, p.relative_to(root.resolve()).as_posix())]
    # Legacy official IR pointed every panel at the same page imgs directory.
    # Resolve its own bbox instead of repeating every image for every panel.
    size = sizes.get(block.page)
    if len(candidates) > 1 and block.bbox and size:
        expected = [block.bbox[i] * size[i % 2] for i in range(4)]
        matches = []
        for candidate in candidates:
            match = _IMAGE_BOX.search(candidate.name)
            if match:
                distance = sum(abs(float(value) - target) for value, target in zip(match.groups(), expected))
                matches.append((distance, candidate))
        if matches:
            distance, path = min(matches, key=lambda value: value[0])
            if distance <= 12:
                return [path]
        # Never silently replace an unknown panel with all unrelated page images.
        return []
    return candidates


def export_figures(blocks: list[Block], data: dict, root: Path, output: Path) -> list[Block]:
    blocks = sorted(blocks, key=lambda b: (b.page, b.order))
    sizes = {p["number"]: (p["width"], p["height"]) for p in data.get("pages", [])}
    groups, removed = {}, set()
    document = None
    pdf = root / "original.pdf"
    try:
        for index, caption in enumerate(blocks):
            if caption.type is not BlockType.CAPTION or not _CAPTION.match(caption.content.text or "") or not caption.bbox:
                continue
            members = []
            for previous in reversed(blocks[:index]):
                if previous.page != caption.page:
                    break
                if previous.type not in (BlockType.FIGURE, BlockType.CAPTION, BlockType.EQUATION_INLINE):
                    break
                if previous.type is BlockType.CAPTION and _CAPTION.match(previous.content.text or ""):
                    break
                if previous.bbox:
                    members.append(previous)
            panels = [b for b in members if b.type is BlockType.FIGURE and b.bbox]
            if len(panels) < 2 or not pdf.is_file():
                continue
            # A caption above the pictures or intervening body text is not
            # evidence that these are panels of one figure.
            if any(b.bbox[3] > caption.bbox[1] + .02 for b in panels):
                continue
            if document is None:
                import fitz
                document = fitz.open(pdf)
            if not 1 <= caption.page <= len(document):
                continue
            import fitz
            page = document[caption.page - 1]
            left = min([caption.bbox[0]] + [b.bbox[0] for b in members])
            right = max([caption.bbox[2]] + [b.bbox[2] for b in members])
            top = min(b.bbox[1] for b in members)
            bottom = max(b.bbox[3] for b in members)
            # Include plot labels and legends omitted by region detection,
            # stopping before the numbered caption (kept as searchable text).
            bottom = max(bottom, caption.bbox[1] - .008)
            box = fitz.Rect(max(0, left - .005) * page.rect.width, max(0, top - .005) * page.rect.height,
                            min(1, right + .005) * page.rect.width, min(caption.bbox[1] - .003, bottom + .003) * page.rect.height)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=box, alpha=False)
            content = pixmap.tobytes("png")
            name = hashlib.sha256(content).hexdigest()[:24] + ".png"
            target = output / "assets" / "media" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            first = min(panels, key=lambda b: b.order)
            combined = first.model_copy(deep=True)
            combined.content.image_path = f"assets/media/{name}"
            combined.bbox = [left, top, right, bottom]
            groups[first.id] = combined
            removed.update(b.id for b in members if b.id != first.id)
        result = []
        seen = set()
        for block in blocks:
            if block.id in removed:
                continue
            if block.id in groups:
                result.append(groups[block.id])
            elif block.type is BlockType.FIGURE:
                for index, path in enumerate(_figure_asset(block, sizes, root)):
                    # A legacy directory can appear several times in the IR.
                    identity = (block.page, path)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    name = hashlib.sha256(path.read_bytes()).hexdigest()[:24] + path.suffix.lower()
                    destination = output / "assets" / "media" / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                    image = block.model_copy(deep=True)
                    image.id += f"-image-{index}"
                    image.content.image_path = f"assets/media/{name}"
                    result.append(image)
            else:
                result.append(block)
        return result
    finally:
        if document is not None:
            document.close()
