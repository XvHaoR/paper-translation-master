"use strict";
(() => {
  const PL = PaperLoom;
  // A session owns only the browser/action connection. Appearance and source
  // navigation have separate lifetimes and cannot unregister these callbacks.
  PL.ReaderSession = {
    matches(state, frame = state.frame) {
      return !!state.uri && frame === state.frame &&
        frame?.currentURI?.spec?.split("#")[0] === state.uri.split("#")[0];
    },
    bind(state) {
      const frame = state.frame, uri = state.uri;
      if (!frame?.browsingContext?.id) return;
      state.bridgeID = frame.browsingContext.id;
      frame._paperloomAction = data => {
        if (!PL.alive || state.frame !== frame || state.uri !== uri) throw new Error("阅读窗口已更新，请重试");
        return PL.ReaderUI.action(state, data);
      };
      frame._paperloomOptions = () => ({ view: PL.ReaderUI.viewOptions(), css: PL.readerStyle });
    },
    unbind(state) {
      if (state.frame) { delete state.frame._paperloomAction; delete state.frame._paperloomOptions; }
      state.bridgeID = null; state.bridgeGlobal = null; state.bridgePending = null;
      state.bridgeStatus = "closed"; state.bridgeError = "";
    },
    async connect(state) {
      const frame = state.frame, global = frame?.browsingContext?.currentWindowGlobal;
      if (!global || global.documentURI?.spec?.split("#")[0] !== state.uri?.split("#")[0]) return false;
      this.bind(state);
      if (state.bridgeGlobal === global && state.bridgeStatus === "ready") return true;
      if (state.bridgePending?.global === global) return state.bridgePending.promise;
      state.bridgeStatus = "connecting";
      const current = () => PL.alive && state.frame === frame && frame.browsingContext?.currentWindowGlobal === global;
      const promise = Promise.resolve().then(async () => {
        try {
          const result = await PL.ReaderUI.readerActor(global).sendQuery("reader-connect");
          if (!current()) return false;
          state.bridgeGlobal = global;
          state.bridgeStatus = result?.available ? "ready" : "waiting";
          state.bridgeError = result?.available ? "" : result?.error || "段落定位连接尚未完成";
          return result?.available === true;
        } catch (error) {
          if (current()) { state.bridgeStatus = "failed"; state.bridgeError = error.message; Zotero.logError(error); }
          return false;
        } finally {
          if (state.bridgePending?.promise === promise) state.bridgePending = null;
        }
      });
      state.bridgePending = { global, promise };
      return promise;
    },
  };
})();
