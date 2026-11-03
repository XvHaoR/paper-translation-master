"""Recover omitted prose around orphan inline formulas from native PDF evidence.

Never hide a formula just because it is inline. It is consumed only after its
source characters have been replaced at the matching coordinates in real prose.
"""
from __future__ import annotations
import re

from app.models.ir import Block, BlockContent, BlockType, SourceInfo, SourceMethod
from app.rendering.markdown_util import clean_latex, strip_math_delimiters


def _inside(box,x,y,pad=0):
    return len(box)==4 and box[0]-pad<=x<=box[2]+pad and box[1]-pad<=y<=box[3]+pad


def recover_inline_prose(blocks, document):
    if document is None:
        return blocks
    formulas=[b for b in blocks if b.type is BlockType.EQUATION_INLINE and len(b.bbox)==4]
    consumed=set();insertions={}
    for page_no in sorted({b.page for b in formulas}):
        if not 1<=page_no<=len(document):continue
        page=document[page_no-1];width,height=page.rect.width,page.rect.height
        regions=[]
        for b in blocks:
            if b.page==page_no and b.type not in (BlockType.EQUATION_INLINE,BlockType.UNKNOWN) and (b.content.text or b.content.html or b.content.image_path):
                regions.extend(r['bbox'] for r in b.source.regions if r.get('page')==page_no)
                if not b.source.regions and len(b.bbox)==4:regions.append(b.bbox)
        page_formulas=[b for b in formulas if b.page==page_no]
        native=page.get_text('rawdict')
        for formula in page_formulas:
            latex=clean_latex(strip_math_delimiters(formula.content.latex or ''))
            x=(formula.bbox[0]+formula.bbox[2])/2;y=(formula.bbox[1]+formula.bbox[3])/2
            for owner in blocks:
                if owner.page!=page_no or owner.type not in (BlockType.PARAGRAPH,BlockType.ABSTRACT,BlockType.CAPTION):continue
                boxes=[r['bbox'] for r in owner.source.regions if r.get('page')==page_no] or [owner.bbox]
                if not any(_inside(box,x,y) for box in boxes):continue
                # Exact existing TeX is proof of duplication. Keep its identity
                # in the owner's metadata, without changing surrounding prose.
                normalize=lambda value: re.sub(r'\s+','',clean_latex(strip_math_delimiters(value)))
                if any(normalize(m.group(1))==normalize(latex) for m in re.finditer(r'\$([^$]+)\$',owner.content.text or '')):
                    owner.content.inline_math.append({'id':formula.id,'latex':latex,'bbox':formula.bbox})
                    consumed.add(formula.id);break
        for index,source in enumerate(native['blocks']):
            if source.get('type')!=0:continue
            lines=source.get('lines',[])
            missing=[]
            for line in lines:
                box=line['bbox'];x=(box[0]+box[2])/2/width;y=(box[1]+box[3])/2/height
                if not any(_inside(region,x,y) for region in regions):missing.append(line)
            native_text='\n'.join(''.join(c['c'] for s in line['spans'] for c in s['chars']) for line in missing)
            if len(native_text.strip())<80:continue
            selected=[]
            for formula in page_formulas:
                x=(formula.bbox[0]+formula.bbox[2])/2;y=(formula.bbox[1]+formula.bbox[3])/2
                if formula.id not in consumed and any(_inside([l['bbox'][0]/width,l['bbox'][1]/height,l['bbox'][2]/width,l['bbox'][3]/height],x,y,.003) for l in missing):
                    selected.append(formula)
            if not selected:continue
            inserted=set();rendered=[];evidence=[]
            for line in missing:
                parts=[];superscript=False
                for span in line['spans']:
                    for char in span['chars']:
                        box=char['bbox'];x=(box[0]+box[2])/2/width;y=(box[1]+box[3])/2/height
                        formula=next((b for b in selected if _inside(b.bbox,x,y,.0005)),None)
                        if formula:
                            if superscript:parts.append('}$');superscript=False
                            if formula.id not in inserted:
                                latex=clean_latex(strip_math_delimiters(formula.content.latex or ''))
                                parts.append(' $'+latex+'$ ');inserted.add(formula.id)
                                evidence.append({'id':formula.id,'latex':latex,'bbox':formula.bbox})
                        else:
                            is_super=bool(span['flags']&1) and bool(char['c'].strip())
                            if is_super!=superscript:
                                parts.append('$^{' if is_super else '}$');superscript=is_super
                            parts.append(char['c'])
                if superscript:parts.append('}$')
                text=''.join(parts).strip()
                if rendered and rendered[-1].endswith('-'):rendered[-1]+=text
                else:rendered.append(text)
            if inserted!={b.id for b in selected}:
                continue  # No partial deletion when matching evidence is incomplete.
            box=[min(l['bbox'][0] for l in missing)/width,min(l['bbox'][1] for l in missing)/height,
                 max(l['bbox'][2] for l in missing)/width,max(l['bbox'][3] for l in missing)/height]
            paragraph=Block(id=f'p{page_no:03}-native-prose-{index}',type=BlockType.PARAGRAPH,
                page=page_no,bbox=box,layout_label='native_recovered_prose',
                content=BlockContent(text=' '.join(rendered),native_text=native_text,inline_math=evidence,
                                     note='由原 PDF 文字层恢复漏检正文，按坐标嵌入 OCR 行内公式'),
                source=SourceInfo(method=SourceMethod.MIXED,page=page_no,text=native_text,regions=[{'page':page_no,'bbox':box}]))
            anchor=min(selected,key=lambda b:b.order).id
            insertions.setdefault(anchor,[]).append(paragraph);consumed.update(inserted)
    output=[]
    for b in blocks:
        output.extend(insertions.get(b.id,[]))
        if b.id not in consumed:output.append(b)
    return output
