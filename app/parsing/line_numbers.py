"""Exclude review-copy line counters from the in-memory layout document.

Numbers must form a vertical sequence beside prose and have marginal typography.
Never remove numeric substrings from sentences, or save changes to the source PDF.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median
import re

import pymupdf


@dataclass
class Counter:
    rect: pymupdf.Rect
    text: str
    size: float
    color: int
    beside_prose: bool = False


def _candidates(page):
    words = page.get_text("words")
    lines = defaultdict(list)
    for word in words:
        lines[word[5:7]].append(word)
    spans = [s for b in page.get_text("dict")["blocks"]
             for line in b.get("lines", []) for s in line["spans"]]
    prose = []
    for row in lines.values():
        letters = [w for w in row if any(c.isalpha() for c in w[4])]
        if sum(c.isalpha() for w in letters for c in w[4]) < 24:
            continue
        rect = pymupdf.Rect(letters[0][:4])
        for word in letters[1:]:
            rect |= pymupdf.Rect(word[:4])
        if rect.width < page.rect.width * .14:
            continue
        styles = [s for s in spans if pymupdf.Point(*s["origin"]) in (rect + (-1, -2, 1, 2))]
        size = median([s["size"] for s in styles]) if styles else rect.height
        color = max(styles, key=lambda s: len(s["text"]))["color"] if styles else 0
        prose.append((rect, size, color))
    result = []
    for word in words:
        if not re.fullmatch(r"[0-9]{1,5}", word[4]):
            continue
        rect = pymupdf.Rect(word[:4])
        center = (rect.tl + rect.br) / 2
        span = next((s for s in spans if center in pymupdf.Rect(s["bbox"])), None)
        if not span:
            continue
        counter = Counter(rect, word[4], span["size"], span["color"])
        for body, size, color in prose:
            if abs(center.y - (body.y0 + body.y1) / 2) > body.height * .5:
                continue
            gap = max(body.x0 - rect.x1, rect.x0 - body.x1)
            if not max(3, size * .6) <= gap <= page.rect.width * .12:
                continue
            # Plain numbered lists and table index columns usually use the same
            # typography as their contents; ambiguous cases stay in the source.
            smaller = counter.size <= size * .93
            different_color = counter.color != color and counter.color != 0
            padded = len(counter.text) >= 3 and counter.text.startswith("0")
            if smaller or different_color or padded:
                counter.beside_prose = True
                break
        result.append(counter)
    return result


def _sequence(group):
    if len(group) < 2:
        return False
    ordered = sorted(group, key=lambda n: n.rect.y0)
    deltas = [int(b.text) - int(a.text) for a, b in zip(ordered, ordered[1:])]
    step = median(deltas)
    if step not in (1, 5, 10):
        return False
    return (sum(d > 0 and d % step == 0 and d <= step * 10 for d in deltas)
            >= .85 * len(deltas)
            and ordered[-1].rect.y0 - ordered[0].rect.y0 > median(n.size for n in group) * (len(group) - 1))


def _lanes(candidates, width):
    # Both alignments matter when counters change from 99 to 100.
    for edge in ("x0", "x1"):
        groups = []
        for counter in sorted(candidates, key=lambda n: getattr(n.rect, edge)):
            lane = next((g for g in groups
                         if abs(getattr(g[0].rect, edge) - getattr(counter.rect, edge)) <= max(1.5, width * .003)
                         and abs(g[0].size - counter.size) < max(.5, counter.size * .12)
                         and g[0].color == counter.color), None)
            if lane is None:
                groups.append([counter])
            else:
                lane.append(counter)
        yield from groups


def remove_line_numbers(document, pages):
    """Filter only the analysis copy; return per-page counts for parser metadata.

    Strong lanes teach the same document's short pages. Text coordinates are
    unchanged, so existing reader/highlight anchors keep referring to the PDF.
    """
    page_lanes, templates, selected = {}, [], defaultdict(dict)
    for number in pages:
        page = document[number - 1]
        rotation = page.rotation
        page.set_rotation(0)
        try:
            lanes = list(_lanes(_candidates(page), page.rect.width))
            page_lanes[number] = (lanes, page.rect.width)
            for lane in lanes:
                if len(lane) < 6 or not _sequence(lane):
                    continue
                if sum(n.beside_prose for n in lane) < max(4, len(lane) * .6):
                    continue
                templates.append((lane[0].rect.x0 / page.rect.width, lane[0].rect.x1 / page.rect.width,
                                  lane[0].size / page.rect.width, lane[0].color))
                for n in lane:
                    selected[number][tuple(n.rect)] = n
        finally:
            page.set_rotation(rotation)
    for number, (lanes, width) in page_lanes.items():
        for lane in lanes:
            if len(lane) < 2 or not _sequence(lane) or sum(n.beside_prose for n in lane) < 2:
                continue
            first = lane[0]
            if any(min(abs(first.rect.x0 / width - x0), abs(first.rect.x1 / width - x1)) < .003
                   and abs(first.size / width - size) < .001 and first.color == color
                   for x0, x1, size, color in templates):
                for n in lane:
                    selected[number][tuple(n.rect)] = n
    counts = {}
    for number, counters in selected.items():
        page = document[number - 1]
        rotation = page.rotation
        page.set_rotation(0)
        try:
            # Applying our annotations must not apply an unrelated pending user
            # redaction. Delete those annotations from this disposable copy only.
            for annot in list(page.annots() or []):
                if annot.type[0] == pymupdf.PDF_ANNOT_REDACT:
                    page.delete_annot(annot)
            for counter in counters.values():
                page.add_redact_annot(counter.rect + (.1, .1, -.1, -.1), fill=False, cross_out=False)
            page.apply_redactions(images=0, graphics=0, text=0)
            counts[str(number)] = len(counters)
        finally:
            page.set_rotation(rotation)
    return counts
