"use strict";
(() => {
  const PL = PaperLoom;
  // Coordinate recovery, native PDF access and drawing have separate modules.
  // This controller only owns the lifetime of bidirectional navigation requests.
  PL.Comparison = {
    bind(state) {
      const doc = PL.PDFContext.current(state).doc;
      if (!doc || state.pdfDoc === doc) return;
      state.pdfDoc?.removeEventListener("click", state.pdfClick, true);
      state.pdfDoc = doc;
      state.pdfClick = async event => {
        if (!PL.alive || state.mode !== "split" || !state.saved || state.pdfDoc !== doc
          || (event.button !== undefined && event.button !== 0)) return;
        const page = event.target.closest?.(".page");
        if (!page || doc.defaultView.getSelection()?.isCollapsed === false) return;
        const bounds = page.getBoundingClientRect();
        if (!(bounds.width > 0 && bounds.height > 0)) return;
        const point = { page: Number(page.dataset.pageNumber),
          x: (event.clientX - bounds.left) / bounds.width, y: (event.clientY - bounds.top) / bounds.height };
        if (!Number.isInteger(point.page) || point.page < 1
          || ![point.x,point.y].every(value => Number.isFinite(value) && value >= 0 && value <= 1)) return;
        const frame = state.frame, global = frame?.browsingContext?.currentWindowGlobal;
        const context = PL.PDFContext.current(state);
        const request = state.pdfClickRequest = state.locationRequest = Symbol("pdf-click");
        const current = () => PL.alive && state.saved && state.mode === "split" && state.frame === frame
          && state.pdfDoc === doc && state.pdfClickRequest === request && state.locationRequest === request
          && context.doc === doc && PL.PDFContext.same(state, context)
          && frame?.browsingContext?.currentWindowGlobal === global;
        let block;
        try { block = await PL.ReaderUI.readerActor(global).sendQuery("locate-point", point); }
        catch (error) { if (current()) Zotero.logError(error); }
        if (!current()) return;
        if (!block) {
          try { block = await PL.SourceRegions.find(state, point); }
          catch (error) { if (current()) Zotero.logError(error); }
          if (block && current()) {
            try { await PL.ReaderUI.readerActor(global).sendQuery("select-block", { id: block.block }); }
            catch (error) { if (current()) Zotero.logError(error); }
          }
        }
        if (block && current()) {
          try { await this.locate(state, block, false); }
          catch (error) {
            if (state.frame === frame && state.pdfDoc === doc) Zotero.logError(error);
          }
        }
      };
      // Observe before the native PDF handlers stop bubbling after selection.
      doc.addEventListener("click", state.pdfClick, true);
    },
    clear(state) { PL.PDFHighlight.clear(state); },
    unbind(state) {
      state.locationRequest = Symbol("disposed"); state.pdfClickRequest = Symbol("disposed");
      state.pdfDoc?.removeEventListener("click", state.pdfClick, true);
      this.clear(state); state.pdfDoc = null; state.pdfClick = null;
    },
    async locate(state, data, navigate = true) {
      const frame = state.frame, global = frame?.browsingContext?.currentWindowGlobal;
      const request = state.locationRequest = Symbol("locate");
      const currentFrame = () => PL.alive && state.saved && state.frame === frame && !!frame
        && state.mode === "split" && state.locationRequest === request
        && frame.browsingContext?.currentWindowGlobal === global;
      const context = await PL.PDFContext.ready(state);
      const current = () => currentFrame() && PL.PDFContext.same(state, context);
      if (!current()) return false;
      const page = Number(data?.page), pageIndex = page - 1;
      if (!Number.isInteger(page) || page < 1 || page > context.pdf.numPages) throw new Error("该段落没有有效 PDF 页码");
      const regions = await PL.PDFRegions.resolve(state, { ...data, page }, context.pdf.numPages);
      if (!current()) return false;
      if (navigate) {
        const rects = await PL.PDFRegions.rects(context, page, regions);
        if (!current()) return false;
        // A native navigation already in flight cannot be cancelled. Queue the
        // newest navigation after it, while skipping all obsolete queued ones.
        const navigation = Promise.resolve(state.pdfNavigation).catch(() => {}).then(async () => {
          if (!current()) return;
          await state.reader.navigate(rects.length ? { position: { pageIndex, rects } } : { pageIndex });
        });
        state.pdfNavigation = navigation;
        await navigation;
      }
      if (!current()) return false;
      this.bind(state);
      state.lastLocated = { block: data.block, page, bbox: PL.PDFRegions.box(data.bbox) || [], regions };
      // Presentation failures must not discard a completed source navigation.
      try { PL.PDFHighlight.render(state, context, regions); }
      catch (error) { Zotero.logError(error); }
      return state.lastLocated;
    },
  };
})();
