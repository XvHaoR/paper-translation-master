"use strict";
(() => {
  const PL = PaperLoom;
  // PDF coordinates and selection are independent of reading styles and fonts.
  PL.Comparison = {
    bind(state) {
      const doc = state.reader._internalReader?._primaryView?._iframeWindow?.document;
      if (!doc || state.pdfDoc === doc) return;
      if (state.pdfDoc) state.pdfDoc.removeEventListener("click", state.pdfClick, true);
      state.pdfDoc = doc;
      state.pdfClick = async event => {
        if (state.mode !== "split" || !state.saved) return;
        const page = event.target.closest?.(".page");
        if (!page || !doc.defaultView.getSelection().isCollapsed) return;
        const bounds = page.getBoundingClientRect();
        const frame = state.frame;
        try {
          const point = {
            page: Number(page.dataset.pageNumber), x: (event.clientX - bounds.left) / bounds.width, y: (event.clientY - bounds.top) / bounds.height,
          };
          let block = await PL.ReaderUI.readerActor(frame.browsingContext.currentWindowGlobal).sendQuery("locate-point", point);
          if (!block) {
            block = await PL.SourceRegions.find(state, point);
            if (block && state.frame === frame) await PL.ReaderUI.readerActor(frame.browsingContext.currentWindowGlobal).sendQuery("select-block", {id:block.block});
          }
          if (block && state.frame === frame && state.mode === "split") await this.locate(state, block, false);
        }
        catch (error) { Zotero.logError(error); }
      };
      // Native PDF handlers may stop bubbling after selecting text/annotations.
      // Observe the click without depending on their bubble-phase behavior.
      doc.addEventListener("click", state.pdfClick, true);
    },
    clear(state) {
      for (const overlay of state.pdfHighlights || []) overlay.remove();
      state.pdfHighlights = [];
      state.pdfHighlight?.remove(); state.pdfHighlight = null;
      state.pdfDoc?.querySelectorAll(".paperloom-dimmed-page").forEach(node => { node.style.opacity = ""; node.classList.remove("paperloom-dimmed-page"); });
    },
    async locate(state, data, navigate = true) {
      const frame = state.frame;
      const request = state.locationRequest = Symbol("locate");
      await PL.ReaderUI.ready(state.reader);
      if (!PL.alive || state.frame !== frame || !state.saved) return false;
      const view = state.reader._internalReader._primaryView;
      const app = view._iframeWindow.PDFViewerApplication;
      const pageIndex = Number(data.page) - 1;
      if (!Number.isInteger(pageIndex) || pageIndex < 0 || pageIndex >= app.pdfDocument.numPages) throw new Error("该段落没有有效 PDF 页码");
      const bbox = Array.isArray(data.bbox) ? data.bbox.map(Number) : [];
      const valid = bbox.length === 4 && bbox.every(value => Number.isFinite(value) && value >= 0 && value <= 1) && bbox[2] > bbox[0] && bbox[3] > bbox[1];
      const regions=valid ? await PL.SourceRegions.resolve(state,{...data,page:pageIndex+1,bbox}) : [];
      if (state.frame !== frame || state.locationRequest !== request) return false;
      if (navigate) {
        const boxes=regions.filter(region=>region.page===pageIndex+1);
        if (boxes.length) {
          const viewport=app.pdfViewer.getPageView(pageIndex)?.viewport || PL.SourceRegions.viewport(await app.pdfDocument.getPage(pageIndex+1));
          const rects=boxes.map(region=>{
            const first=viewport.convertToPdfPoint(region.bbox[0]*viewport.width,region.bbox[1]*viewport.height);
            const second=viewport.convertToPdfPoint(region.bbox[2]*viewport.width,region.bbox[3]*viewport.height);
            return [Math.min(first[0],second[0]),Math.min(first[1],second[1]),Math.max(first[0],second[0]),Math.max(first[1],second[1])];
          });
          await state.reader.navigate({position:{pageIndex,rects}});
        }
        else await state.reader.navigate({pageIndex});
      }
      if (state.frame !== frame || state.locationRequest !== request) return false;
      this.bind(state); this.clear(state);
      if (regions.length && PL.pref("blockCompare",true)) {
        const doc=view._iframeWindow.document;
        const pageNumbers=new Set(regions.map(region=>region.page));
        for (const number of pageNumbers) {
          const page=app.pdfViewer.getPageView(number-1);
          if (!page?.div) continue;
          const boxes=regions.filter(region=>region.page===number).map(region=>region.bbox);
          const svg=doc.createElementNS("http://www.w3.org/2000/svg","svg");
          svg.setAttribute("viewBox","0 0 1 1");svg.setAttribute("preserveAspectRatio","none");
          svg.classList.add("paperloom-pdf-highlight");
          svg.style.cssText="position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1000";
          const mask=doc.createElementNS(svg.namespaceURI,"path");
          mask.setAttribute("d","M0 0H1V1H0Z "+boxes.map(([x0,y0,x1,y1])=>`M${x0} ${y0}H${x1}V${y1}H${x0}Z`).join(" "));
          mask.setAttribute("fill-rule","evenodd");mask.setAttribute("fill","rgba(255,255,255,.72)");svg.append(mask);
          for (const [x0,y0,x1,y1] of boxes) {
            const rect=doc.createElementNS(svg.namespaceURI,"rect");
            for(const [name,value] of Object.entries({x:x0,y:y0,width:x1-x0,height:y1-y0,fill:"none",stroke:"#6264ff","stroke-width":.003})) rect.setAttribute(name,String(value));
            svg.append(rect);
          }
          page.div.append(svg);state.pdfHighlights.push(svg);
        }
        state.pdfHighlight=state.pdfHighlights[0]||null;
        doc.querySelectorAll(".page").forEach(node=>{if(!pageNumbers.has(Number(node.dataset.pageNumber))){node.classList.add("paperloom-dimmed-page");node.style.opacity=".3";}});
      }
      state.lastLocated={block:data.block,page:pageIndex+1,bbox,regions};
      return state.lastLocated;
    },
  };
})();
