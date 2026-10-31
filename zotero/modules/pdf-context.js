"use strict";
(() => {
  const unwrap = value => value && typeof Components !== "undefined"
    ? Components.utils.waiveXrays(value) : value;
  // Keep native reader/PDF.js realm access in one place. Never cache a failed
  // initialization: restored tabs can replace their primary view asynchronously.
  PaperLoom.PDFContext = {
    current(state) {
      const view = unwrap(unwrap(state.reader?._internalReader)?._primaryView);
      const window = unwrap(view?._iframeWindow);
      const app = unwrap(window?.PDFViewerApplication);
      return { view, window, doc: window?.document, app,
        pdf: unwrap(app?.pdfDocument), viewer: unwrap(app?.pdfViewer) };
    },
    async ready(state) {
      await PaperLoom.ReaderUI.ready(state.reader);
      const context = this.current(state);
      if (!context.doc || !context.viewer || !Number.isInteger(context.pdf?.numPages)
        || context.pdf.numPages < 1) throw new Error("PDF 阅读器尚未加载，请稍后重试；原文件未修改");
      return context;
    },
    same(state, context) {
      try {
        const current = this.current(state);
        return current.doc === context.doc && current.pdf === context.pdf && current.view === context.view;
      }
      catch (_) { return false; }
    },
    page(context, index) { return unwrap(context.viewer.getPageView(index)); },
  };
})();
