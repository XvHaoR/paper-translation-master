"use strict";
(() => {
  const PL = PaperLoom;
  const normalize = text => String(text || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const inside = (region, page, box) => region.page === page &&
    (box[0]+box[2])/2 >= region.bbox[0]-.025 && (box[0]+box[2])/2 <= region.bbox[2]+.025 &&
    (box[1]+box[3])/2 >= region.bbox[1]-.025 && (box[1]+box[3])/2 <= region.bbox[3]+.025;
  PL.PDFSelection = {
    async resolve(context, data, known) {
      const wanted = normalize(data.selectionText);
      if (wanted.length < 4 || wanted.length > 12000) return null;
      const numbers = [...new Set(known.map(region => region.page))].sort((a,b)=>a-b).slice(0,3);
      let text = "", items = [];
      for (const number of numbers) {
        const page = await PL.SourceRegions.textPage(context.pdf, number);
        items.push(...page.items.map(item => ({...item, start: item.start+text.length, end: item.end+text.length})));
        text += page.text;
      }
      for (let start = text.indexOf(wanted); start >= 0; start = text.indexOf(wanted,start+1)) {
        const matched = items.filter(item => item.end > start && item.start < start+wanted.length);
        if (!matched.length || !known.some(region => inside(region, matched[0].page, matched[0].bbox))) continue;
        const regions = matched.map(item => {
          const box = item.bbox.slice(), width = box[2]-box[0], length = item.end-item.start;
          // Text-layer DOM ranges below refine these PDF text-item estimates.
          box[2] = box[0]+width*Math.min(1,(start+wanted.length-item.start)/length);
          box[0] += width*Math.max(0,(start-item.start)/length);
          return {page: item.page, bbox: box};
        });
        return {regions, text: data.selectionText};
      }
      return null;
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
