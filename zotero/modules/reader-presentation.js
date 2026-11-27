"use strict";
(() => {
  const PL = PaperLoom;
  PL.ReaderPresentation = {
    fontSize() { return Math.max(12, Math.min(40, Number(PL.pref("ocrFontSize", 17)) || 17)); },
    async setFont(size, states) {
      size = Math.max(12, Math.min(40, Math.round(Number(size) || 17)));
      PL.setPref("ocrFontSize", size);
      for (const state of states.values()) {
        if (state.fontSize) state.fontSize.value = String(size);
      }
      await Promise.all(Array.from(states.values(), state => this.applyFont(state)));
    },
    async applyFont(state) {
      const context = state.frame?.browsingContext;
      if (!context) return;
      // Saved readers have a 17px base. Native text zoom works across content
      // processes and page loads, even when the optional reader actor fails.
      // It changes text without scaling the source PDF or raster figures.
      context.textZoom = this.fontSize() / 17;
    },
    mode(state) {
      const pdf = state?.reader && Zotero.Items.get(state.reader.itemID);
      return (pdf && PL.Store.get(pdf)?.translationView) || PL.pref("translationView", "bilingual");
    },
    viewOptions(state) { return { mode: this.mode(state), kind: state?.kind, focus: PL.pref("blockCompare", true),
      split: state?.mode === "split", syncScroll: PL.pref("syncScroll",false), appearance: PL.Appearance.read() }; },
    async applyStyle(state) {
      const global = state.frame?.browsingContext?.currentWindowGlobal;
      if (global?.documentURI?.scheme === "file") await PL.ReaderUI.readerActor(global).sendQuery("reader-style", { css: PL.readerStyle });
    },
  };
})();
