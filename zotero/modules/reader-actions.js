"use strict";
(() => {
  const PL = PaperLoom;
  PL.ReaderActions = {
    async dispatch(state, data) {
      state.lastBridgeAction = data?.type;
      if (!PL.alive || !state.saved || !PL.ReaderSession.matches(state)) throw new Error("阅读窗口已更新，请重试");
      if (!data || typeof data !== "object") throw new Error("阅读操作无效");
      if (data.type === "annotation-save") return PL.ReaderAnnotations.save(state, data);
      if (data.type === "reader-scroll") return PL.ScrollSync.fromReader(state,data);
      if (data.type === "selection-clear") {
        state.locationRequest=Symbol("selection-cleared");
        if (state.lastLocated?.highlight === "selection") { PL.PDFHighlight.clear(state); state.lastLocated=null; }
        return true;
      }
      if (data.type === "selection-locate") {
        if (state.mode !== "split") return false;
        if (typeof data.selectionText !== "string" || data.selectionText.length>20000) throw new Error("选中文字过长");
        return PL.ReaderUI.locate(state,{...data,highlight:"selection"});
      }
      if (data.type === "copy-latex") {
        if (typeof data.text !== "string" || data.text.length > 100000) throw new Error("公式内容无效");
        Zotero.Utilities.Internal.copyTextToClipboard(data.text);
        return true;
      }
      if (data.type === "excel") return PL.Artifacts.exportTables(state.reader._window, data.items);
      if (data.type === "save-ocr-edits") return PL.Editing.save(state.reader, data).then(()=>true);
      if (data.type === "translation-mode") { await PL.ReaderUI.translationMode(state.reader, data.mode); return true; }
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
