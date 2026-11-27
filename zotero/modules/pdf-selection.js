"use strict";
(() => {
  const PL = PaperLoom;
  const normalize = text => String(text || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const inside = (region, page, box) => region.page === page &&
    (box[0]+box[2])/2 >= region.bbox[0]-.025 && (box[0]+box[2])/2 <= region.bbox[2]+.025 &&
    (box[1]+box[3])/2 >= region.bbox[1]-.025 && (box[1]+box[3])/2 <= region.bbox[3]+.025;
  const nearby = (text, start, end, prefix, suffix) =>
    (prefix && text.slice(Math.max(0,start-prefix.length),start) === prefix ? prefix.length : 0)
    + (suffix && text.slice(end,end+suffix.length) === suffix ? suffix.length : 0);
  function uniqueMatch(candidates) {
    candidates.sort((a,b) => b.score - a.score);
    return candidates.length && candidates[0].score !== candidates[1]?.score ? candidates[0].match : null;
  }
  PL.PDFSelection = {
    async nativeMatch(context, source, known) {
      const wanted = normalize(source.quote);
      if (!wanted || wanted.length > 12000) return null;
      const prefix = normalize(source.before).slice(-64), suffix = normalize(source.after).slice(0,64);
      const pages = [...new Set(known.map(region => region.page))].sort((a,b) => a-b).slice(0,3);
      const chars = [], refs = [], candidates = [];
      let text = "";
      for (const page of pages) {
        const options = Components.utils.cloneInto({pageIndex: page - 1}, context.pdf);
        const data = Components.utils.waiveXrays(await context.pdf.getPageData(options));
        const viewport = PL.SourceRegions.viewport(await context.pdf.getPage(page));
        for (const [offset, char] of data.chars.entries()) {
          const word = char.ignorable ? "" : normalize(char.c);
          for (let i = 0; i < word.length; i++) refs.push(chars.length);
          text += word;
          chars.push({page, char, offset, viewport});
        }
      }
      const isNear = ({page, char, viewport}) => {
        if (!char.rect?.every(Number.isFinite)) return false;
        const [x0,y0,x1,y1] = char.rect;
        const [x,y] = viewport.convertToViewportPoint((x0+x1)/2, (y0+y1)/2);
        const box = [x/viewport.width,y/viewport.height,x/viewport.width,y/viewport.height];
        return known.some(region => inside(region, page, box));
      };
      for (let start = text.indexOf(wanted); start >= 0; start = text.indexOf(wanted, start+1)) {
        const end = start + wanted.length, first = refs[start], last = refs[end-1];
        if (!isNear(chars[first]) || !isNear(chars[last])) continue;
        const score = nearby(text, start, end, prefix, suffix);
        if (wanted.length < 4 && !score) continue;
        candidates.push({match: [first, last], score});
      }
      const bounds = uniqueMatch(candidates);
      if (!bounds) return null;
      const [first, last] = bounds, rects = new Map();
      let line = null, lastPage = null;
      for (const {page, char} of chars.slice(first,last+1)) {
        const values = char.inlineRect || char.rect;
        if (values?.length === 4 && values.every(Number.isFinite)) {
          const [x0,y0,x1,y1] = values;
          const rect = [Math.min(x0,x1),Math.min(y0,y1),Math.max(x0,x1),Math.max(y0,y1)];
          if (!rects.has(page)) rects.set(page, []);
          if (!line || lastPage !== page) { line = rect; rects.get(page).push(line); }
          else {
            line[0] = Math.min(line[0],rect[0]); line[1] = Math.min(line[1],rect[1]);
            line[2] = Math.max(line[2],rect[2]); line[3] = Math.max(line[3],rect[3]);
          }
          lastPage = page;
        }
        if (char.lineBreakAfter || char.paragraphBreakAfter) line = null;
      }
      return {rects, offset: chars[first].offset};
    },
    async resolve(context, data, known, { strict = false } = {}) {
      const wanted = normalize(data.selectionText);
      if (wanted.length < (strict ? 1 : 4) || wanted.length > 12000) return null;
      const numbers = [...new Set(known.map(region => region.page))].sort((a,b)=>a-b).slice(0,3);
      let text = "";
      const items = [], pageStarts = new Map(), candidates = [];
      for (const number of numbers) {
        const page = await PL.SourceRegions.textPage(context.pdf, number);
        pageStarts.set(number, text.length);
        items.push(...page.items.map(item => ({...item, start: item.start+text.length, end: item.end+text.length})));
        text += page.text;
      }
      const prefix = normalize(data.before).slice(-64), suffix = normalize(data.after).slice(0,64);
      for (let start = text.indexOf(wanted); start >= 0; start = text.indexOf(wanted,start+1)) {
        const end = start + wanted.length;
        const matched = items.filter(item => item.end > start && item.start < end);
        if (!matched.length || !known.some(region => inside(region, matched[0].page, matched[0].bbox))) continue;
        if (strict && !known.some(region => inside(region, matched.at(-1).page, matched.at(-1).bbox))) continue;
        const regions = matched.map(item => {
          const box = item.bbox.slice(), width = box[2]-box[0], length = item.end-item.start;
          // Text-layer DOM ranges below refine these PDF text-item estimates.
          box[2] = box[0]+width*Math.min(1,(end-item.start)/length);
          box[0] += width*Math.max(0,(start-item.start)/length);
          return {page: item.page, bbox: box};
        });
        const match = {regions, text: data.selectionText, offset: start - pageStarts.get(matched[0].page)};
        if (!strict) return match;
        const score = nearby(text, start, end, prefix, suffix);
        if (wanted.length < 4 && !score) continue;
        candidates.push({match, score});
      }
      return uniqueMatch(candidates);
    },
    refine(context, text, known) {
      const wanted = normalize(text), results = [];
      if (!wanted) return null;
      for (const number of new Set(known.map(region=>region.page))) {
        const page = PL.PDFContext.page(context,number-1)?.div, layer = page?.querySelector(".textLayer");
        if (!layer) continue;
        const doc = context.doc, walker = doc.createTreeWalker(layer,4), entries = [];
        let node, joined = "";
        while ((node=walker.nextNode())) {
          if (node.parentElement?.closest(".endOfContent,button")) continue;
          const offsets = []; let normalized = "", offset = 0;
          for (const char of node.data) {
            const part = normalize(char);
            for (let i=0; i<part.length; i++) offsets.push({start:offset,end:offset+char.length});
            normalized += part; offset += char.length;
          }
          if (!normalized) continue;
          entries.push({node,offsets,start:joined.length,end:joined.length+normalized.length}); joined+=normalized;
        }
        for (let start=joined.indexOf(wanted); start>=0; start=joined.indexOf(wanted,start+1)) {
          const bounds=page.getBoundingClientRect(), boxes=[];
          if (!bounds.width || !bounds.height) continue;
          for (const entry of entries.filter(entry=>entry.end>start&&entry.start<start+wanted.length)) {
            const a=Math.max(0,start-entry.start), b=Math.min(entry.offsets.length,start+wanted.length-entry.start)-1;
            const range=doc.createRange();
            range.setStart(entry.node,entry.offsets[a].start); range.setEnd(entry.node,entry.offsets[b].end);
            for (const rect of range.getClientRects()) {
              if (rect.width<=0 || rect.height<=0) continue;
              const box=[(rect.left-bounds.left)/bounds.width,(rect.top-bounds.top)/bounds.height,
                (rect.right-bounds.left)/bounds.width,(rect.bottom-bounds.top)/bounds.height].map(value=>Math.max(0,Math.min(1,value)));
              if (PL.PDFRegions.box(box)) boxes.push({page:number,bbox:box});
            }
          }
          if (boxes.length && known.some(region=>inside(region,number,boxes[0].bbox))) { results.push(...boxes); break; }
        }
      }
      return results.length ? results : null;
    },
  };
})();
