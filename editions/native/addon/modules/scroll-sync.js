"use strict";
(() => {
  const PL = PaperLoom, ANCHOR = .28, clamp = value => Math.max(0, Math.min(1, value));
  const regions = block => {
    const values = (block.regions || []).filter(region => PL.PDFRegions.box(region.bbox) && region.page > 0);
    return values.length ? values : PL.PDFRegions.box(block.bbox) ? [{page: block.page, bbox: block.bbox}] : [];
  };
  PL.ScrollSync = {
    active(state) { return PL.alive && state.saved && state.frame && state.mode === "split" && PL.pref("syncScroll", false); },
    bind(state) {
      const context = PL.PDFContext.current(state), container = context.viewer?.container || context.doc?.getElementById("viewerContainer");
      if (!container || state.syncContainer === container) return;
      this.unbind(state);
      state.syncContainer = container; state.syncContext = context;
      state.syncInput = () => { state.syncInputAt = Date.now(); state.syncMutedUntil = 0; state.syncSequence++; };
      state.syncScroll = () => {
        if (!this.active(state) || !state.syncInputAt || Date.now() < state.syncMutedUntil || state.syncTimer) return;
        state.syncTimer = setTimeout(() => { state.syncTimer = null; this.toReader(state).catch(error => Zotero.logError(error)); }, 70);
      };
      for (const name of ["wheel", "pointerdown", "touchstart", "keydown"]) container.addEventListener(name, state.syncInput, true);
      container.addEventListener("scroll", state.syncScroll, {passive: true});
    },
    mutePDF(state) { state.syncMutedUntil = Date.now() + 600; clearTimeout(state.syncTimer); state.syncTimer = null; state.syncSequence = (state.syncSequence || 0) + 1; },
    unbind(state) {
      clearTimeout(state.syncTimer); state.syncTimer = null;
      state.syncSequence = (state.syncSequence || 0) + 1;
      if (state.syncContainer) {
        state.syncContainer.removeEventListener("scroll", state.syncScroll);
        for (const name of ["wheel", "pointerdown", "touchstart", "keydown"]) state.syncContainer.removeEventListener(name, state.syncInput, true);
      }
      state.syncContainer = null; state.syncIndex = null; state.syncInputAt = 0;
    },
    async index(state, actor, frame) {
      if (state.syncIndex?.frame === frame) return state.syncIndex.blocks;
      const blocks = await actor.sendQuery("block-index");
      if (state.frame !== frame) return [];
      state.syncIndex = {frame, blocks: Array.isArray(blocks) ? blocks : []};
      return state.syncIndex.blocks;
    },
    async toReader(state) {
      if (!this.active(state) || Date.now() < state.syncMutedUntil) return;
      const frame = state.frame, global = frame.browsingContext?.currentWindowGlobal;
      if (!global) return;
      const actor = PL.ReaderUI.readerActor(global), sequence = ++state.syncSequence;
      const blocks = await this.index(state, actor, frame), context = PL.PDFContext.current(state), container = state.syncContainer;
      if (!this.active(state) || state.frame !== frame || state.syncSequence !== sequence || !container || !PL.PDFContext.same(state, state.syncContext)) return;
      const bounds = container.getBoundingClientRect(), y = bounds.top + container.clientHeight * ANCHOR;
      const pages = Array.from(context.doc.querySelectorAll(".page")).map(node => ({node, rect: node.getBoundingClientRect()}));
      pages.sort((a,b) => Math.max(a.rect.top-y,y-a.rect.bottom,0)-Math.max(b.rect.top-y,y-b.rect.bottom,0));
      const page = pages[0];
      if (!page || !page.rect.height) return;
      const number = Number(page.node.dataset.pageNumber), fraction = clamp((y-page.rect.top)/page.rect.height), candidates = [];
      for (const block of blocks) {
        const parts = regions(block), total = parts.reduce((sum, r) => sum + r.bbox[3]-r.bbox[1], 0);
        let before = 0;
        for (const part of parts) {
          const box = part.bbox, height = box[3]-box[1];
          if (part.page === number) candidates.push({block, box, distance: Math.max(box[1]-fraction,fraction-box[3],0),
            progress: clamp((before + clamp((fraction-box[1])/height)*height)/total)});
          before += height;
        }
      }
      candidates.sort((a,b) => a.distance-b.distance || (a.block.block===state.syncBlock ? -1 : b.block.block===state.syncBlock ? 1 : 0) || a.box[0]-b.box[0]);
      const hit = candidates[0];
      if (!hit || state.syncSequence !== sequence) return;
      state.syncBlock = hit.block.block;
      await actor.sendQuery("sync-scroll-to", {block: hit.block.block, progress: hit.progress, inputAt: state.syncInputAt});
    },
    async fromReader(state, data) {
      if (!this.active(state) || !Number.isFinite(data.progress) || !Number.isFinite(data.inputAt) || state.syncInputAt > data.inputAt) return false;
      this.bind(state);
      const context = PL.PDFContext.current(state), container = state.syncContainer;
      const parts = regions(data).filter(region => region.page <= (context.pdf?.numPages || 0));
      if (!container || !parts.length) return false;
      const total = parts.reduce((sum,r) => sum+r.bbox[3]-r.bbox[1],0);
      let distance = clamp(data.progress)*total, part = parts.at(-1);
      for (const candidate of parts) {
        part = candidate;
        if (distance <= candidate.bbox[3]-candidate.bbox[1]) break;
        distance -= candidate.bbox[3]-candidate.bbox[1];
      }
      const page = PL.PDFContext.page(context, part.page-1)?.div;
      if (!page) return false;
      this.mutePDF(state); state.syncBlock = data.block;
      const pageBox = page.getBoundingClientRect(), containerBox = container.getBoundingClientRect();
      container.scrollTop += pageBox.top - containerBox.top + pageBox.height * (part.bbox[1] + Math.min(distance, part.bbox[3]-part.bbox[1])) - container.clientHeight*ANCHOR;
      return true;
    },
  };
})();
