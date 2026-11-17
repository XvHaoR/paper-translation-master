"""Repair damaged citation scripts only with native PDF typography and context.

This module never guesses a lost reference number or deletes replacement chars.
Unverifiable damage remains visible and is flagged for review.
"""
from __future__ import annotations

import re
import unicodedata

from app.models.ir import BlockType, SourceMethod
from app.rendering.markdown_util import markdown_protected_ranges

TEXT_TYPES={BlockType.PARAGRAPH,BlockType.ABSTRACT,BlockType.CAPTION,
            BlockType.FOOTNOTE,BlockType.REFERENCE,BlockType.HEADING,BlockType.DOCUMENT_TITLE}


def _indexed(text):
    value=[];positions=[]
    for index,char in enumerate(text):
        for normalized in unicodedata.normalize('NFKC',char).lower():
            if normalized.isalnum():value.append(normalized);positions.append(index)
    return ''.join(value),positions


def _native_flow(block,document,cache):
    text=[];styles=[];boxes=[]
    regions=block.source.regions or [{'page':block.page,'bbox':block.bbox}]
    seen=set()
    for region in regions:
        number=region.get('page',block.page);box=region.get('bbox',[])
        if len(box)!=4 or not 1<=number<=len(document):continue
        page=document[number-1]
        if number not in cache:cache[number]=page.get_text('rawdict')
        for source in cache[number]['blocks']:
            for line in source.get('lines',[]):
                added=False
                for span in line['spans']:
                    for char in span['chars']:
                        rect=char['bbox'];x=(rect[0]+rect[2])/2/page.rect.width;y=(rect[1]+rect[3])/2/page.rect.height
                        key=(number,tuple(rect),char['c'])
                        if key in seen or not (box[0]-.002<=x<=box[2]+.002 and box[1]-.002<=y<=box[3]+.002):continue
                        seen.add(key);text.append(char['c']);styles.append(bool(span['flags']&1));boxes.append([v/[page.rect.width,page.rect.height][i%2] for i,v in enumerate(rect)]);added=True
                if added:text.append(' ');styles.append(False);boxes.append(None)
    value=''.join(text);runs=[];index=0
    while index<len(value):
        if not styles[index]:index+=1;continue
        end=index+1
        while end<len(value) and styles[end]:end+=1
        label=value[index:end].strip()
        if re.fullmatch(r'\d{1,3}(?:\s*[,–−-]\s*\d{1,3})*',label):
            rectangles=[r for r in boxes[index:end] if r]
            runs.append((index,end,label,[min(r[0] for r in rectangles),min(r[1] for r in rectangles),max(r[2] for r in rectangles),max(r[3] for r in rectangles)]))
        index=end
    return value,runs


def _gap(text,left,right):
    normalized,positions=_indexed(text)
    for size in (24,20,16,12,8):
        if len(left)<size or len(right)<size:continue
        before,after=left[-size:],right[:size]
        matches=[]
        for hit in re.finditer(re.escape(before),normalized):
            start=positions[hit.end()-1]+1
            for tail in re.finditer(re.escape(after),normalized[hit.end():]):
                end=positions[hit.end()+tail.start()]
                if end-start>40:break
                matches.append((start,end))
        if len(matches)==1:return matches[0]
        if matches:return None  # Ambiguous context is not permission to guess.
    return None


def repair_pdf_citations(blocks,document):
    cache={}
    for block in blocks:
        original=block.content.text or ''
        if block.type not in TEXT_TYPES or not ('\ufffd' in original or re.search(r'\b[A-Za-z]{4,}\^\d',original)):continue
        changes=[]
        if document is not None:
            native,runs=_native_flow(block,document,cache)
            protected=markdown_protected_ranges(original)
            for start,end,label,box in runs:
                left=_indexed(native[:start])[0];right=_indexed(native[end:])[0]
                gap=_gap(original,left,right)
                if gap is None:continue
                a,b=gap;damaged=original[a:b]
                if any(a<stop and b>begin for begin,stop in protected):continue
                if not ('\ufffd' in damaged or damaged.lstrip().startswith('^')):continue
                leading=re.match(r'\s*',damaged).group()
                trailing=re.search(r'[\s.;:!?)]*$',damaged).group()
                core=damaged[len(leading):len(damaged)-len(trailing) if trailing else len(damaged)]
                # The entire replaced slice must be a script, never body text.
                if not re.fullmatch(r'[\s\ufffd\d¹²³⁴⁵⁶⁷⁸⁹⁰,^{}$–−-]+',core):continue
                candidate=leading+'$^{'+label+'}$'+trailing
                changes.append((a,b,candidate,{'before':damaged,'after':candidate,'native':label,'bbox':box,'method':'pdf-citation-context'}))
        for start,end,replacement,evidence in sorted(changes,reverse=True):
            block.content.text=block.content.text[:start]+replacement+block.content.text[end:]
            block.content.text_repairs.insert(0,evidence)
        if changes:
            block.source.text=block.source.text or original
            block.source.method=SourceMethod.MIXED
        if '\ufffd' in (block.content.text or ''):
            warning='正文含无法从 PDF 验证的替换字符，请核对原文'
            if warning not in block.quality.warnings:block.quality.warnings.append(warning)
            block.quality.needs_review=True
    return blocks
