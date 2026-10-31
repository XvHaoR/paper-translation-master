"use strict";
(() => {
  const PL = PaperLoom;
  const pages = new WeakMap();
  const views = new WeakMap();
  const unwrap = value => typeof Components === "undefined" ? value : Components.utils.waiveXrays(value);
  function viewportFor(value) {
    const page=unwrap(value);
    // PDF.js runs in the reader's content realm. Its options must be cloned
    // there; privileged-realm objects otherwise expose an undefined scale.
    const options=typeof Components === "undefined" ? {scale:1} : Components.utils.cloneInto({scale:1},page);
    return unwrap(page.getViewport(options));
  }
  const normalize = text => String(text || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const valid = box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)
    && box.every(value => value >= 0 && value <= 1) && box[2] > box[0] && box[3] > box[1];
  const inside = (box, x, y, pad = 0) => x >= box[0] - pad && x <= box[2] + pad && y >= box[1] - pad && y <= box[3] + pad;
  const union = (a, b) => [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[2],b[2]),Math.max(a[3],b[3])];
  const area = box => (box[2]-box[0])*(box[3]-box[1]);
  const grams = text => new Set(Array.from({length:Math.max(0,text.length-2)},(_,i)=>text.slice(i,i+3)));
  const similarity = (a,b) => { const left=grams(a),right=grams(b);return left.size ? [...left].filter(value=>right.has(value)).length/left.size : 0; };

  async function textPage(pdf, number) {
    let cache = pages.get(pdf);
    if (!cache) { cache = new Map(); pages.set(pdf, cache); }
    if (!cache.has(number)) cache.set(number, (async () => {
      const page = unwrap(await pdf.getPage(number));
      const viewport = viewportFor(page);
      const content = await page.getTextContent();
      const items=[]; let text="";
      for (const item of content.items) {
        const word=normalize(item.str);
        if (!word || !item.transform || !Number.isFinite(item.width)) continue;
        const [a,b,c,d,e,f]=item.transform;
        const length=Math.hypot(a,b)||1, height=Math.hypot(c,d)||item.height||length;
        const ux=a/length,uy=b/length;
        const points=[];
        for (const along of [0,item.width]) for (const up of [-.2*height,.85*height]) {
          const point=viewport.convertToViewportPoint(e+ux*along-uy*up,f+uy*along+ux*up);
          points.push([point[0]/viewport.width,point[1]/viewport.height]);
        }
        const box=[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))]
          .map(value=>Math.max(0,Math.min(1,value)));
        if (!valid(box)) continue;
        items.push({start:text.length,end:text.length+word.length,bbox:box,page:number,word});
        text+=word;
      }
      return {text,items};
    })().catch(error => { cache.delete(number); throw error; }));
    return cache.get(number);
  }

  function cacheFor(state) {
    let cache=views.get(state.frame);
    if (!cache) { cache=new Map(); views.set(state.frame,cache); }
    return cache;
  }

  PL.SourceRegions = {
    viewport: viewportFor,
    async resolve(state, data) {
      const base={page:Number(data.page),bbox:data.bbox};
      if (!valid(base.bbox) || !state.frame) return [base];
      if (data.regions?.length && data.regions.every(region=>Number.isInteger(region.page)&&valid(region.bbox))) return data.regions;
      const cache=cacheFor(state);
      if (cache.has(data.block)) return cache.get(data.block).regions;
      const frame=state.frame;
      let source=data;
      if (!source.text) source={...data,...await PL.ReaderUI.readerActor(frame.browsingContext.currentWindowGlobal).sendQuery("block-source",{id:data.block})};
      const wanted=normalize(source.text);
      let regions=[base];
      // Endpoint matching is anchored inside the known OCR rectangle. Require
      // substantial agreement before recovering a missing column/page region.
      if (wanted.length>=100 && source.type!=="figure" && source.type!=="table") {
        try {
          const pdf=unwrap(state.reader._internalReader._primaryView._iframeWindow.PDFViewerApplication.pdfDocument);
          const first=await textPage(pdf,base.page);
          const prefix=wanted.slice(0,32),suffix=wanted.slice(-48);
          let start=first.text.indexOf(prefix),anchor;
          while (start>=0) {
            anchor=first.items.find(item=>item.start<=start&&item.end>start);
            if (anchor && inside(base.bbox,(anchor.bbox[0]+anchor.bbox[2])/2,(anchor.bbox[1]+anchor.bbox[3])/2,.015)) break;
            start=first.text.indexOf(prefix,start+1);
          }
          if (start>=0) {
            let text=first.text,items=first.items;
            let end=text.indexOf(suffix,start+32);
            if (end<0 && base.page<pdf.numPages) {
              const next=await textPage(pdf,base.page+1);
              items=[...items,...next.items.map(item=>({...item,start:item.start+text.length,end:item.end+text.length}))];
              text+=next.text;end=text.indexOf(suffix,start+32);
            }
            if (end>=0) {
              end+=suffix.length;
              const matched=text.slice(start,end);
              if (matched.length>=wanted.length*.65 && matched.length<=wanted.length*1.4 && similarity(wanted,matched)>=.72) {
                const extra=[];
                for (const item of items.filter(item=>item.end>start&&item.start<end)) {
                  const box=item.bbox,x=(box[0]+box[2])/2,y=(box[1]+box[3])/2;
                  if (item.page===base.page&&inside(base.bbox,x,y,.012)) continue;
                  if (!wanted.includes(item.word) && similarity(item.word,wanted)<.65) continue;
                  let group=extra.find(region=>region.page===item.page
                    &&Math.min(region.bbox[2],box[2])-Math.max(region.bbox[0],box[0])>Math.min(region.bbox[2]-region.bbox[0],box[2]-box[0])*.2
                    &&Math.max(0,box[1]-region.bbox[3],region.bbox[1]-box[3])<.04);
                  if (group) group.bbox=union(group.bbox,box);
                  else extra.push({page:item.page,bbox:box.slice()});
                }
                regions=[base,...extra.filter(region=>area(region.bbox)>.0001)];
              }
            }
          }
        }
        catch (error) { Zotero.logError(error); /* Scans/no native text keep their known OCR rectangle. */ }
      }
      if (state.frame===frame) cache.set(data.block,{...source,block:data.block,regions});
      return regions;
    },
    async find(state, point) {
      const frame=state.frame,cache=cacheFor(state);
      const hit=()=>[...cache.values()].flatMap(block=>block.regions.filter(region=>region.page===point.page&&inside(region.bbox,point.x,point.y)).map(region=>({block,area:area(region.bbox)})))
        .sort((a,b)=>a.area-b.area)[0]?.block;
      if (hit()) return hit();
      const candidates=await PL.ReaderUI.readerActor(frame.browsingContext.currentWindowGlobal).sendQuery("page-blocks",{page:point.page});
      for (const block of candidates || []) {
        if (state.frame!==frame) return null;
        await this.resolve(state,block);
      }
      return hit() || null;
    },
  };
})();
