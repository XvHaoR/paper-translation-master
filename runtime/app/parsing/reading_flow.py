"""Recover prose flow without losing the physical PDF regions it came from."""
from __future__ import annotations

import re
from collections import Counter

from app.models.ir import BlockType

PARSER_VERSION = "native-layout-v6-line-numbers"

BODY = {BlockType.PARAGRAPH, BlockType.ABSTRACT}
MEDIA = {BlockType.TABLE, BlockType.FIGURE}
FLOAT = MEDIA | {BlockType.CAPTION}
CAPTION = re.compile(r"^\s*(?P<kind>table|fig(?:ure)?\.?|[图表])\s*(?:S?\d+[a-z]?|[IVX]+)[.:：、]\s*", re.I)
COMPOUND_PREFIX = {"open", "fine", "spatial", "region", "vision", "language", "ground", "state",
                   "real", "large", "small", "high", "low", "multi", "cross", "self", "semi", "non"}


def vocabulary(text):
    return Counter(word.lower() for word in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", text))


def join_text(left, right, lexicon=None):
    left, right = left.rstrip(), right.lstrip()
    if not left or not right:
        return left + right
    match = re.search(r"([A-Za-z]+(?:-[A-Za-z]+)*)-\Z", left)
    word = re.match(r"([a-z][A-Za-z]*)", right)
    if match and word:
        prefix, suffix = match[1].lower(), word[1].lower()
        joined, hyphenated = prefix + suffix, prefix + "-" + suffix
        # Prefer an unbroken spelling elsewhere in this PDF. Preserve an
        # existing compound (ground-and-refer) and semantic compound prefixes.
        keep = bool(lexicon and lexicon.get(hyphenated))
        if not keep and not (lexicon and lexicon.get(joined)):
            keep = "-" in prefix or prefix in COMPOUND_PREFIX
        return (left if keep else left[:-1]) + right
    if re.search(r"[\u3400-\u9fff]\Z", left) and re.match(r"[\u3400-\u9fff]", right):
        return left + right
    return left + " " + right


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) / max(.001, min(a[2]-a[0], b[2]-b[0]))


def associate_captions(blocks):
    """Recover misclassified captions only with a nearby matching media box."""
    for block in blocks:
        if block.type not in BODY | {BlockType.CAPTION} or len(block.bbox) != 4:
            continue
        match = CAPTION.match(block.content.text or "")
        if not match:
            continue
        kind = BlockType.TABLE if match['kind'].lower() in {'table', '表'} else BlockType.FIGURE
        candidates = []
        for media in blocks:
            if media.type is not kind or media.page != block.page or len(media.bbox) != 4:
                continue
            a, b = block.bbox, media.bbox
            gap = max(0, a[1]-b[3], b[1]-a[3])
            if _overlap(a, b) < .6 or gap > .085:
                continue
            # A body paragraph between them is evidence against attachment.
            top, bottom = min(a[3], b[3]), max(a[1], b[1])
            if any(x is not block and x.type in BODY and x.page == block.page and len(x.bbox) == 4
                   and top < (x.bbox[1]+x.bbox[3])/2 < bottom and _overlap(x.bbox, a) > .5 for x in blocks):
                continue
            candidates.append((gap + abs((a[0]+a[2]-b[0]-b[2])/2)*.15, media))
        if not candidates:
            continue
        media = min(candidates, key=lambda value: value[0])[1]
        block.type = BlockType.CAPTION
        block.layout_label = 'table_caption' if kind is BlockType.TABLE else 'figure_caption'
        block.anchor_id, block.anchor_kind, block.placement = media.id, kind.value, 'after'


def _regions(block):
    return block.source.regions or [{"page": block.page, "bbox": block.bbox}]


def _continues(previous, following, floats):
    if previous.type not in BODY or following.type not in BODY:
        return False
    left, right = (previous.content.text or '').rstrip(), (following.content.text or '').lstrip()
    if not left or not right or re.search(r'[.!?。！？:：;；][\)\]”\"\u2019]*$', left):
        return False
    # A lowercase continuation is strong textual evidence. Uppercase starts,
    # numbered lists and headings remain separate rather than being guessed.
    if not (right[0].islower() or '\u3400' <= right[0] <= '\u9fff'):
        return False
    last = _regions(previous)[-1]
    page, a, b = last['page'], last['bbox'], following.bbox
    if len(a) != 4 or len(b) != 4:
        return False
    if following.page == page + 1:
        return a[3] > .55 and b[1] < .65
    if following.page != page:
        return False
    same_column = _overlap(a, b) > .7 and -.015 <= b[1]-a[3] <= (.65 if floats else .08)
    next_column = b[0] >= a[2]-.025 and a[3] > b[1]+.12
    return same_column or next_column


def reconstruct(blocks, lexicon=None):
    """Defer floats until the paragraph ends; footnotes form a separate stream.

    Block.page/bbox keep their real physical location. reading_order is solely
    the logical display order, and merged blocks retain every source region.
    """
    associate_captions(blocks)
    notes, flow, pending = [], [], []
    current = None
    for block in blocks:
        if block.type is BlockType.FOOTNOTE:
            block.placement = 'document_end'
            block.anchor_kind = 'document'
            notes.append(block)
            continue
        if block.type in FLOAT and current is not None:
            pending.append(block)
            continue
        if current is not None and _continues(current, block, pending):
            if not current.source.regions:
                current.source.regions = _regions(current)
            # Keep component text and identity for traceability and future
            # sentence-to-region matching, including across different pages.
            if len(current.source.regions) == 1:
                current.source.regions[0].setdefault('text', current.source.text or current.content.text)
                current.source.regions[0].setdefault('block_id', current.id)
            for region in _regions(block):
                current.source.regions.append({**region, 'text': region.get('text', block.source.text or block.content.text),
                                               'block_id': region.get('block_id', block.id)})
            current.content.text = join_text(current.content.text, block.content.text, lexicon)
            current.source.text = join_text(current.source.text or '', block.source.text or block.content.text, lexicon)
            current.quality.needs_review |= block.quality.needs_review
            current.quality.warnings = list(dict.fromkeys(current.quality.warnings + block.quality.warnings))
            continue
        flow.extend(pending)
        pending = []
        flow.append(block)
        current = block if block.type in BODY else None
    flow.extend(pending)
    # Move caption blocks together with their identified media. No text is
    # discarded; standalone captions retain their original place.
    captions = {}
    for block in flow:
        if block.type is BlockType.CAPTION and block.anchor_id:
            captions.setdefault(block.anchor_id, []).append(block)
    attached = {b.id for values in captions.values() for b in values}
    ordered = []
    for block in flow:
        if block.id in attached:
            continue
        ordered.append(block)
        ordered.extend(captions.get(block.id, []))
    ordered.extend(notes)
    for index, block in enumerate(ordered):
        block.reading_order = index
    return ordered
