"use strict";
(() => {
  const PL = PaperLoom;
  const ns = "http://www.w3.org/2000/svg";
  // Display only: changing this renderer cannot disable navigation or source
  // coordinate recovery. The overlay never changes the PDF or its text layer.
  PL.PDFHighlight = {
    clear(state) {
      for (const overlay of new Set([...(state.pdfHighlights || []), state.pdfHighlight])) {
        try { overlay?.remove(); } catch (_) { /* An old PDF document may be gone. */ }
      }
      state.pdfHighlights = []; state.pdfHighlight = null;
      for (const { node, opacity } of state.pdfDimmed || []) {
        try { node.style.opacity = opacity; node.classList.remove("paperloom-dimmed-page"); }
        catch (_) { /* A restored reader may have already discarded this node. */ }
      }
      state.pdfDimmed = [];
      // Clean up overlays created before this version was loaded.
      try {
        state.pdfDoc?.querySelectorAll(".paperloom-dimmed-page").forEach(node => {
          node.style.opacity = ""; node.classList.remove("paperloom-dimmed-page");
        });
      }
      catch (_) { /* An old content document may no longer be accessible. */ }
    },
    render(state, context, regions) {
      this.clear(state);
      if (!PL.pref("blockCompare", true) || !regions.length || state.mode !== "split") return;
      const { doc } = context, pageNumbers = new Set(regions.map(region => region.page));
      for (const number of pageNumbers) {
        const page = PL.PDFContext.page(context, number - 1);
        if (!page?.div) continue;
        const boxes = regions.filter(region => region.page === number).map(region => region.bbox);
        const svg = doc.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", "0 0 1 1"); svg.setAttribute("preserveAspectRatio", "none");
        svg.classList.add("paperloom-pdf-highlight");
        svg.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1000";
        const mask = doc.createElementNS(ns, "path");
        mask.setAttribute("d", "M0 0H1V1H0Z " + boxes.map(([x0,y0,x1,y1]) => `M${x0} ${y0}H${x1}V${y1}H${x0}Z`).join(" "));
        mask.setAttribute("fill-rule", "evenodd"); mask.setAttribute("fill", "rgba(255,255,255,.72)"); svg.append(mask);
        for (const [x0,y0,x1,y1] of boxes) {
          const rect = doc.createElementNS(ns, "rect");
          for (const [name,value] of Object.entries({x:x0,y:y0,width:x1-x0,height:y1-y0,fill:"none",stroke:"#6264ff","stroke-width":.003})) rect.setAttribute(name,String(value));
          svg.append(rect);
        }
        page.div.append(svg); state.pdfHighlights.push(svg);
      }
      state.pdfHighlight = state.pdfHighlights[0] || null;
      doc.querySelectorAll(".page").forEach(node => {
        if (!pageNumbers.has(Number(node.dataset.pageNumber))) {
          state.pdfDimmed.push({ node, opacity: node.style.opacity });
          node.classList.add("paperloom-dimmed-page"); node.style.opacity = ".3";
        }
      });
    },
  };
})();
