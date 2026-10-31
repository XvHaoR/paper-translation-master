"use strict";
(() => {
  const PL = PaperLoom;
  PL.ReaderUI = {
    states: new Map(),
    register() {
      // Window actors are lazy-loaded. A versioned resource alias avoids a
      // cached failed chrome:// module import surviving an add-on upgrade.
      this.actorResource = `paperloom-reader-${Date.now().toString(36)}`;
      this.resources = Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler);
      this.resources.setSubstitution(this.actorResource, Services.io.newURI(PL.rootURI));
      ChromeUtils.registerWindowActor("PaperLoomReader", {
        allFrames: true, matches: ["file:///*"],
        parent: { esModuleURI: `resource://${this.actorResource}/content/reader-parent.mjs` },
        child: { esModuleURI: `resource://${this.actorResource}/content/reader-child.mjs`, events: { DOMContentLoaded: {}, pageshow: {}, click: {capture:true}, PaperLoomAction: { wantUntrusted: true, capture: true }, PaperLoomReaderConnect: { wantUntrusted: true, capture: true } } },
      });
      this.readyObserver = { observe: (_subject, _topic, id) => {
        for (const state of this.states.values()) {
          if (String(state.frame?.browsingContext?.id) === id) {
            this.bindFrame(state);
            this.initializeFrame(state, { connect: false });
          }
        }
      } };
      Services.obs.addObserver(this.readyObserver, "paperloom-reader-ready");
      this.listener = event => this.toolbar(event);
      Zotero.Reader.registerEventListener("renderToolbar", this.listener, PL.id);
      for (const reader of Zotero.Reader._readers) {
        reader._initPromise.then(() => {
          if (!PL.alive || reader._isTabClosed) return;
          const doc = reader._iframeWindow?.document;
          const target = doc?.querySelector(".toolbar .custom-sections");
          if (target) this.toolbar({ reader, doc, append: node => target.append(node) });
        }).catch(error => Zotero.logError(error));
      }
    },
    async open(source) {
      let reader = await Zotero.Reader.open(source.id);
      const deadline = Date.now() + 30000;
      // Zotero intentionally returns undefined when selecting a restored or
      // unloaded tab. Its native load hook creates the reader asynchronously.
      while (!reader && PL.alive && Date.now() < deadline) {
        reader = Zotero.Reader._readers.find(value => value.itemID === source.id && !value._isTabClosed);
        if (!reader) await Zotero.Promise.delay(50);
      }
      if (!reader) throw new Error("PDF 阅读标签页未能恢复，请关闭该标签页后重新打开");
      await this.ready(reader);
      return reader;
    },
    async ready(reader) {
      let timer;
      try {
        await Promise.race([(async () => {
          await reader._initPromise;
          await reader._internalReader?._primaryView?.initializedPromise;
        })(), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("PDF 阅读器加载超时，请关闭该标签页后重新打开；原文件未修改")), 30000);
        })]);
      }
      finally { clearTimeout(timer); }
    },
    state(reader, doc = reader._iframeWindow?.document) {
      if (this.states.has(reader)) return this.states.get(reader);
      const style = doc.createElement("style");
      style.dataset.paperloom = "reader-style";
      style.textContent = PL.ReaderLayout.css;
      doc.head.append(style);
      const state = { reader, doc, style, mode: "original", swapped: false, ratio: Number(PL.pref("splitRatio", "0.5")) || .5, kind: "ocr" };
      state.unload = () => this.remove(reader);
      doc.defaultView.addEventListener("unload", state.unload, { once: true });
      this.states.set(reader, state);
      return state;
    },
    toolbar({ reader, doc, append }) {
      if (!PL.alive || Zotero.Items.get(reader.itemID)?.attachmentContentType !== "application/pdf") return;
      const state = this.state(reader, doc);
      for (const old of doc.querySelectorAll(".paperloom-reader-controls")) old.remove();
      const controls = doc.createElement("div");
      controls.className = "paperloom-reader-controls";
      for (const [mode, label] of [["original", "原文"], ["split", "对照"], ["ocr", "OCR"]]) {
        const button = doc.createElement("button");
        button.className = "paperloom-mode-button";
        button.dataset.mode = mode;
        button.textContent = label;
        button.title = this.modeLabel(mode);
        button.setAttribute("aria-pressed", String(state.mode === mode));
        // Direct buttons avoid native popup/focus handling in the embedded reader.
        const activate = PL.run(reader._window, () => this.setMode(reader, mode));
        button.addEventListener("click", event => {
          event.preventDefault(); event.stopPropagation(); activate();
        });
        controls.append(button);
      }
      const swap = doc.createElement("button");
      swap.textContent = "⇄";
      swap.className = "paperloom-swap-button";
      swap.title = "交换原文与 OCR 窗口位置";
      swap.setAttribute("aria-label", "交换左右窗口");
      swap.disabled = state.mode !== "split";
      swap.addEventListener("click", () => this.swap(reader));
      controls.append(swap);
      append(controls);
    },
    modeLabel(mode) { return ({ original: "原文 PDF", split: "原文 / OCR 对照", ocr: "仅 OCR 版本" })[mode]; },
    createPane(state) { PL.ReaderPanel.create(state); },
    async setMode(reader, mode) {
      await this.ready(reader);
      const state = this.state(reader);
      if (!["original", "split", "ocr"].includes(mode)) return;
      state.mode = mode;
      if (mode !== "original") this.createPane(state);
      this.layout(state);
      if (mode !== "original" && !state.saved) await this.load(reader, "ocr");
      if (mode === "split") this.bindPDF(state);
      else this.clearHighlight(state);
    },
    swap(reader) {
      const state = this.state(reader);
      state.swapped = !state.swapped;
      this.layout(state);
    },
    layout(state) { PL.ReaderLayout.apply(state); },
    position(state) { PL.ReaderLayout.position(state); },
    async load(reader, kind) {
      const state = this.state(reader);
      const request = state.loadRequest = Symbol("load");
      this.createPane(state);
      const source = Zotero.Items.get(reader.itemID);
      const saved = await PL.Store.saved(source, kind);
      if (state.loadRequest !== request) return;
      if (saved) return this.showSaved(reader, saved, kind);
      state.kind = kind;
      state.saved = null;
      state.frame?.remove();
      state.frame = null;
      state.message.hidden = false;
      state.message.textContent = kind === "ocr"
        ? "这篇文献还没有 OCR 版本。\n点击上方「识别 / 重试」开始，完成后会自动保存。\n也可在文献列表中批量选择后执行 OCR。"
        : "尚无译文。\n点击「翻译」会使用已保存的 OCR 结果，并保存完整译文。";
      this.refresh();
    },
    async showSaved(reader, saved, kind) {
      const state = this.state(reader);
      const request = state.loadRequest = Symbol("show");
      this.createPane(state);
      const path = await saved.attachment.getFilePathAsync();
      if (state.loadRequest !== request) return;
      if (!path) throw new Error("保存的文档尚未下载，请先在 Zotero 中下载附件");
      const uri = Services.io.newFileURI(Zotero.File.pathToFile(path)).spec;
      const replaceFrame = state.uri !== uri || !state.frame;
      let schema = state.exportSchema;
      if (replaceFrame) {
        schema = null;
        try { schema = (await IOUtils.readJSON(PathUtils.join(PathUtils.parent(path), "manifest.json"))).schema; }
        catch (_) { /* The saved page can supply its own metadata. */ }
        if (state.loadRequest !== request) return;
      }
      // Commit the new document only after asynchronous preparation succeeds.
      // The visible page keeps its own actions while its replacement loads.
      state.saved = saved;
      state.kind = kind;
      state.savedLanguage = kind === "translate" ? PL.language() : null;
      if (replaceFrame) {
        this.unbindFrame(state);
        state.exportSchema = schema;
        this.clearHighlight(state);
        state.lastLocated = null;
        state.frame?.remove();
        const frame = reader._window.document.createXULElement("browser");
        frame.setAttribute("class", "paperloom-pane-frame");
        frame.setAttribute("type", "content");
        frame.setAttribute("flex", "1");
        frame.setAttribute("remote", "false");
        frame.setAttribute("maychangeremoteness", "true");
        frame.setAttribute("disableglobalhistory", "true");
        frame.setAttribute("messagemanagergroup", "basicViewer");
        frame.setAttribute("title", kind === "ocr" ? "OCR 文献" : "已保存译文");
        state.content.append(frame);
        frame.browsingContext.sandboxFlags &= ~0x80;
        state.frame = frame;
        state.uri = uri;
        this.bindFrame(state);
        frame.loadURI(Services.io.newURI(uri), { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
      }
      state.message.hidden = true;
      // Manifest metadata is not a signal that the content browser has loaded.
      // Keep about:blank out of actor requests, independently of styling.
      const frame = state.frame;
      const deadline = Date.now() + 5000;
      while (state.frame === frame && frame?.browsingContext?.currentWindowGlobal?.documentURI?.spec !== uri && Date.now() < deadline) {
        await Zotero.Promise.delay(25);
      }
      if (state.loadRequest !== request) return;
      await this.initializeFrame(state);
      this.bindPDF(state);
      this.refresh();
    },
    fontSize() { return PL.ReaderPresentation.fontSize(); },
    setFont(size) { return PL.ReaderPresentation.setFont(size, this.states); },
    applyFont(state) { return PL.ReaderPresentation.applyFont(state); },
    readerActor(global) { return global.getActor("PaperLoomReader"); },
    viewOptions() { return PL.ReaderPresentation.viewOptions(); },
    async initializeFrame(state, { connect = true } = {}) {
      const effects = [this.applyFont(state), this.applyStyle(state), this.applyOptions(state)];
      if (connect) effects.push(PL.ReaderSession.connect(state));
      const results = await Promise.allSettled(effects);
      for (const result of results) if (result.status === "rejected") Zotero.logError(result.reason);
    },
    applyStyle(state) { return PL.ReaderPresentation.applyStyle(state); },
    bindFrame(state) { PL.ReaderSession.bind(state); },
    unbindFrame(state) { PL.ReaderSession.unbind(state); },
    async applyOptions(state) {
      const frame = state.frame;
      const global = frame?.browsingContext?.currentWindowGlobal;
      if (global?.documentURI?.scheme !== "file") return;
      try {
        const result = await this.readerActor(global).sendQuery("reader-view", this.viewOptions());
        if (state.frame !== frame) return;
        state.exportSchema = result?.schema;
        this.refresh();
      }
      catch (error) { Zotero.logError(error); }
    },
    async translationMode(mode) {
      if (PL.pref("translationView", "bilingual") === mode) return;
      PL.setPref("translationView", mode);
      for (const state of this.states.values()) await this.applyOptions(state);
      this.refresh();
    },
    async toggleCompare(reader) {
      PL.setPref("blockCompare", !PL.pref("blockCompare", true));
      for (const state of this.states.values()) {
        if (!PL.pref("blockCompare", true)) this.clearHighlight(state);
        else if (state.mode === "split" && state.lastLocated) await this.locate(state, state.lastLocated, false);
        await this.applyOptions(state);
      }
      this.refresh();
    },
    action(state, data) { return PL.ReaderActions.dispatch(state, data); },
    bindPDF(state) { PL.Comparison.bind(state); },
    clearHighlight(state) { PL.Comparison.clear(state); },
    locate(state, data, navigate = true) { return PL.Comparison.locate(state, data, navigate); },
    async process(reader, kind) {
      const source = Zotero.Items.get(reader.itemID);
      const force = kind === "force-ocr";
      if (force) kind = "ocr";
      const saved = await PL.Tasks.enqueue(source, kind, reader._window, { force });
      if (!PL.alive || !this.states.has(reader)) return;
      await this.showSaved(reader, saved, kind);
    },
    releaseOCR(source) {
      for (const [reader, state] of this.states) {
        if (reader.itemID !== source.id || state.kind !== "ocr") continue;
        this.unbindFrame(state); this.clearHighlight(state);
        state.frame?.remove();
        state.frame = null; state.saved = null; state.uri = null;
        state.message.hidden = false;
        state.message.textContent = "旧 OCR 已清除。点击「识别 / 重试」将重新识别。";
      }
    },
    releaseTranslation(source, language) {
      for (const [reader, state] of this.states) {
        if (reader.itemID !== source.id || state.kind !== "translate" || (state.savedLanguage && state.savedLanguage !== language)) continue;
        this.unbindFrame(state); this.clearHighlight(state);
        state.frame?.remove(); state.frame = null; state.saved = null; state.uri = null;
        state.message.hidden = false;
        state.message.textContent = "译文已删除。OCR 和 PDF 已保留，点击「翻译」将基于当前 OCR 重新翻译。";
      }
    },
    refresh() { for (const state of this.states.values()) PL.ReaderPanel.refresh(state); },
    remove(reader) {
      const state = this.states.get(reader);
      if (!state) return;
      this.unbindFrame(state); this.clearHighlight(state);
      PL.Comparison.unbind(state);
      state.doc.defaultView.removeEventListener("unload", state.unload);
      state.pane?.remove();
      state.modePopup?.remove();
      state.divider?.remove();
      state.placeholder?.remove();
      state.chromeStyle?.remove();
      state.resizeObserver?.disconnect();
      if (state.positionListener) reader._window.removeEventListener("resize", state.positionListener);
      if (state.container) state.container.style.position = state.oldPosition;
      state.style.remove();
      for (const node of state.doc.querySelectorAll(".paperloom-reader-controls")) node.remove();
      state.doc.body.classList.remove("paperloom-reading", "paperloom-swapped", "paperloom-ocr-only");
      state.doc.documentElement.style.removeProperty("--paperloom-pdf-width");
      this.states.delete(reader);
    },
    shutdown({ applicationShutdown = false } = {}) {
      Zotero.Reader.unregisterEventListener("renderToolbar", this.listener);
      Services.obs.removeObserver(this.readyObserver, "paperloom-reader-ready");
      for (const reader of [...this.states.keys()]) this.remove(reader);
      // During application shutdown content frames can still dispatch their
      // final pageshow events. Keep their modules available until process exit.
      if (!applicationShutdown) {
        ChromeUtils.unregisterWindowActor("PaperLoomReader");
        this.resources.setSubstitution(this.actorResource, null);
      }
    },
  };
})();
