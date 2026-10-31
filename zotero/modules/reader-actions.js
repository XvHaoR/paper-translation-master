"use strict";
(() => {
  const PL = PaperLoom;
  PL.ReaderActions = {
    async dispatch(state, data) {
      state.lastBridgeAction = data?.type;
      if (!PL.alive || !state.saved || !PL.ReaderSession.matches(state)) throw new Error("阅读窗口已更新，请重试");
      if (!data || typeof data !== "object") throw new Error("阅读操作无效");
      if (data.type === "copy-latex") {
        if (typeof data.text !== "string" || data.text.length > 100000) throw new Error("公式内容无效");
        Zotero.Utilities.Internal.copyTextToClipboard(data.text);
        return true;
      }
      if (data.type === "excel") return PL.Artifacts.exportTables(state.reader._window, data.items);
      if (data.type === "save-ocr-edits") return PL.Editing.save(state.reader, data).then(()=>true);
      if (data.type === "translation-mode") { await PL.ReaderUI.translationMode(data.mode === "translation" ? "translation" : "bilingual"); return true; }
      if (data.type === "locate") {
        if (state.mode !== "split") {
          if (data.revealPDF === true) await PL.ReaderUI.setMode(state.reader, "split");
          else return true; // Selecting/copying text keeps the user's reading mode.
        }
        return PL.ReaderUI.locate(state, data);
      }
      throw new Error("不支持的阅读操作");
    },
  };
})();
