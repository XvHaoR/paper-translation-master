"use strict";
(() => {
  const PL = PaperLoom;
  const CSS = `
    .paperloom-ocr-pane[hidden],.paperloom-ocr-placeholder[hidden],.paperloom-divider[hidden],.paperloom-pane-message[hidden]{display:none!important}
    .paperloom-reader-controls{display:flex;align-items:center;gap:4px;padding-inline:5px;font:12px system-ui}
    .paperloom-reader-controls .paperloom-mode-button{max-width:145px;overflow:hidden;text-overflow:ellipsis}
    .paperloom-reader-controls button,.paperloom-pane-toolbar button{font:inherit;white-space:nowrap;border:1px solid #8885;border-radius:4px;padding:4px 7px;background:var(--material-background,#fff);color:inherit;cursor:pointer}
    .paperloom-reader-controls button[aria-pressed="true"],.paperloom-pane-toolbar button[aria-pressed="true"]{background:#3875d7;color:white}
    .paperloom-reader-controls button:disabled,.paperloom-pane-toolbar button:disabled{opacity:.45;cursor:default}
    body.paperloom-reading #split-view{flex-direction:row!important}
    body.paperloom-reading #split-view>.primary-view{flex:0 0 var(--paperloom-pdf-width,50%)!important;width:var(--paperloom-pdf-width,50%)!important;min-width:0;order:0}
    body.paperloom-reading #split-view>.secondary-view,body.paperloom-reading #reader-ui .split-view>.secondary-view,body.paperloom-reading #reader-ui .split-view-resizer{display:none!important}
    body.paperloom-reading #reader-ui .split-view>.primary-view{flex:0 0 var(--paperloom-pdf-width,50%)!important;width:var(--paperloom-pdf-width,50%)!important;min-width:0}
    body.paperloom-swapped #split-view>.primary-view{order:2!important}
    body.paperloom-swapped #reader-ui .split-view>.primary-view{margin-inline-start:calc(100% - var(--paperloom-pdf-width,50%))}
    body.paperloom-ocr-only #split-view>.primary-view,body.paperloom-ocr-only #reader-ui .split-view>.primary-view{display:none!important}
    .paperloom-ocr-pane{display:flex;flex:1;min-width:0;flex-direction:column;position:relative;z-index:3;background:var(--material-background,#fff);color:var(--fill-primary,#222);order:2}
    body.paperloom-swapped .paperloom-ocr-pane{order:0}
    .paperloom-ocr-placeholder{flex:1;min-width:0;order:2}
    body.paperloom-swapped .paperloom-ocr-placeholder{order:0}
    .paperloom-pane-toolbar{display:flex;align-items:center;gap:5px;padding:6px 8px;flex-wrap:wrap;border-bottom:1px solid #8884;font:12px system-ui;flex-shrink:0}
    .paperloom-pane-toolbar strong{margin-inline-end:auto}
    .paperloom-pane-content{position:relative;display:flex;flex:1;min-height:0}
    .paperloom-pane-frame{position:relative!important;visibility:visible!important;width:100%;height:100%;flex:1;min-height:0;border:0;background:var(--material-background,#fff)}
    .paperloom-pane-message{padding:28px;line-height:1.7;max-width:540px;font:14px system-ui;white-space:pre-wrap}
    .paperloom-divider{flex:0 0 5px;background:#8882;cursor:col-resize;order:1;z-index:4}
    .paperloom-divider:focus,.paperloom-divider:hover{background:#3875d7}
    body.paperloom-ocr-only .paperloom-divider{display:none}
  `;
  PL.ReaderUI = {
    states: new Map(),
    register() {
      // Window actors are lazy-loaded. A versioned resource alias avoids a
      // cached failed chrome:// module import surviving an add-on upgrade.
      this.actorResource = `paperloom-reader-0214-${Date.now().toString(36)}`;
      this.resources = Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler);
      this.resources.setSubstitution(this.actorResource, Services.io.newURI(PL.rootURI));
      ChromeUtils.registerWindowActor("PaperLoomReader", {
        allFrames: true, matches: ["file:///*"],
        parent: { esModuleURI: `resource://${this.actorResource}/content/reader-parent.mjs` },
        child: { esModuleURI: `resource://${this.actorResource}/content/reader-child.mjs`, events: { DOMContentLoaded: {}, pageshow: {}, click: {capture:true}, PaperLoomAction: { wantUntrusted: true, capture: true } } },
      });
      this.readyObserver = { observe: (_subject, _topic, id) => {
        for (const state of this.states.values()) {
          if (String(state.frame?.browsingContext?.id) === id) {
            this.bindFrame(state);
            this.initializeFrame(state);
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
      style.textContent = CSS;
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
    createPane(state) {
      if (state.pane) return;
      const { reader } = state;
      const nativeDoc = state.doc;
      const doc = reader._window.document;
      const target = nativeDoc.getElementById("split-view");
      if (!target) throw new Error("当前 Zotero 阅读器结构不支持 OCR 面板");
      const divider = nativeDoc.createElement("div");
      divider.className = "paperloom-divider";
      divider.tabIndex = 0;
      divider.setAttribute("role", "separator");
      divider.setAttribute("aria-orientation", "vertical");
      divider.setAttribute("aria-label", "调整 PDF 与 OCR 宽度");
      const setRatio = ratio => {
        state.ratio = Math.max(.2, Math.min(.8, ratio));
        PL.setPref("splitRatio", String(state.ratio));
        this.layout(state);
      };
      divider.addEventListener("keydown", event => {
        if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
          event.preventDefault();
          setRatio(state.ratio + (event.key === "ArrowLeft" ? -.05 : .05) * (state.swapped ? -1 : 1));
        }
      });
      divider.addEventListener("pointerdown", event => {
        event.preventDefault();
        divider.setPointerCapture(event.pointerId);
        const move = event => {
          const bounds = target.getBoundingClientRect();
          const ratio = (event.clientX - bounds.left) / bounds.width;
          setRatio(state.swapped ? 1 - ratio : ratio);
        };
        const stop = () => {
          divider.removeEventListener("pointermove", move);
          divider.removeEventListener("pointerup", stop);
          divider.removeEventListener("pointercancel", stop);
        };
        divider.addEventListener("pointermove", move);
        divider.addEventListener("pointerup", stop);
        divider.addEventListener("pointercancel", stop);
      });
      const html = tag => doc.createElementNS("http://www.w3.org/1999/xhtml", tag);
      const chromeStyle = html("style");
      chromeStyle.textContent = CSS;
      doc.documentElement.append(chromeStyle);
      const pane = html("section");
      pane.className = "paperloom-ocr-pane";
      pane.style.cssText = "position:absolute;z-index:5;box-sizing:border-box;overflow:hidden";
      pane.setAttribute("aria-label", "论文翻译大师 OCR 阅读窗口");
      const toolbar = html("div");
      toolbar.className = "paperloom-pane-toolbar";
      const heading = html("strong");
      heading.textContent = "OCR 版本";
      toolbar.append(heading);
      const button = (label, action, name) => {
        const node = html("button");
        node.textContent = label;
        node.dataset.action = name;
        node.addEventListener("click", PL.run(reader._window, action));
        toolbar.append(node);
        return node;
      };
      button("识别 / 重试", () => this.process(reader, "ocr"), "ocr");
      button("重新 OCR", () => this.process(reader, "force-ocr"), "force-ocr");
      button("清除 OCR", () => PL.Tasks.clearOCR(Zotero.Items.get(reader.itemID)), "clear-ocr");
      button("删除译文", () => PL.Tasks.clearTranslation(Zotero.Items.get(reader.itemID)), "clear-translation");
      button("翻译", () => this.process(reader, "translate"), "translate");
      button("看 OCR", () => this.load(reader, "ocr"), "show-ocr");
      button("看译文", () => this.load(reader, "translate"), "show-translation");
      button("编辑 OCR", () => PL.Editing.open(reader), "edit-ocr");
      button("逐段双语", () => this.translationMode("bilingual"), "bilingual");
      button("仅译文", () => this.translationMode("translation"), "translation-only");
      button("段落对照", () => this.toggleCompare(reader), "block-compare");
      button("导出 Markdown", async () => {
        const source = Zotero.Items.get(reader.itemID);
        const saved = await PL.Store.saved(source, state.kind);
        if (!saved) throw new Error("请先保存当前版本");
        await PL.Artifacts.exportMarkdown(reader._window, saved, state.kind);
      }, "export");
      button("设置", () => PL.UI.settings(reader._window), "settings");
      const fontGroup = html("span");
      fontGroup.style.cssText = "display:flex;align-items:center;gap:4px;font-size:12px";
      fontGroup.textContent = "字号";
      const smaller = html("button");
      smaller.textContent = "A−";
      smaller.title = "缩小 OCR / 译文字号";
      smaller.dataset.action = "font-smaller";
      smaller.addEventListener("click", PL.run(reader._window, () => this.setFont(this.fontSize() - 1)));
      const fontSize = html("input");
      fontSize.type = "number";
      fontSize.min = "12";
      fontSize.max = "40";
      fontSize.value = String(this.fontSize());
      fontSize.setAttribute("aria-label", "OCR 与译文字号");
      fontSize.style.cssText = "width:49px;padding:3px;border:1px solid #8885;border-radius:4px;background:transparent;color:inherit;font:inherit";
      fontSize.addEventListener("input", PL.run(reader._window, () => {
        const size = Number(fontSize.value);
        // Let incomplete input remain editable; valid values and spinner steps
        // apply immediately without requiring a blur/change event.
        if (Number.isFinite(size) && size >= 12 && size <= 40) return this.setFont(size);
      }));
      fontSize.addEventListener("change", PL.run(reader._window, () => this.setFont(Number(fontSize.value))));
      const larger = html("button");
      larger.textContent = "A+";
      larger.title = "放大 OCR / 译文字号";
      larger.dataset.action = "font-larger";
      larger.addEventListener("click", PL.run(reader._window, () => this.setFont(this.fontSize() + 1)));
      const reset = html("button");
      reset.textContent = "重置";
      reset.dataset.action = "font-reset";
      reset.addEventListener("click", PL.run(reader._window, () => this.setFont(17)));
      fontGroup.append(smaller, fontSize, larger, reset);
      toolbar.append(fontGroup);
      const content = html("div");
      content.className = "paperloom-pane-content";
      const message = html("div");
      message.className = "paperloom-pane-message";
      content.append(message);
      pane.append(toolbar, content);
      // The PDF reader's resource:// document cannot embed file:// pages. Keep
      // its layout placeholder inside the reader, and host a sandboxed content
      // browser in the surrounding Zotero chrome at the exact same rectangle.
      const placeholder = nativeDoc.createElement("div");
      placeholder.className = "paperloom-ocr-placeholder";
      target.append(divider, placeholder);
      const container = reader._tabContainer || reader._iframe.parentElement;
      const oldPosition = container.style.position;
      container.style.position = "relative";
      container.append(pane);
      Object.assign(state, { pane, placeholder, container, oldPosition, chromeStyle, divider, heading, content, message, fontSize });
      const observer = new nativeDoc.defaultView.ResizeObserver(() => this.position(state));
      observer.observe(placeholder);
      state.resizeObserver = observer;
      state.positionListener = () => this.position(state);
      reader._window.addEventListener("resize", state.positionListener);
      this.position(state);
    },
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
    layout(state) {
      const { doc, mode, reader } = state;
      doc.body.classList.toggle("paperloom-reading", mode !== "original");
      doc.body.classList.toggle("paperloom-ocr-only", mode === "ocr");
      doc.body.classList.toggle("paperloom-swapped", mode !== "original" && state.swapped);
      doc.documentElement.style.setProperty("--paperloom-pdf-width", `${state.ratio * 100}%`);
      if (state.pane) state.pane.hidden = mode === "original";
      if (state.placeholder) state.placeholder.hidden = mode === "original";
      if (state.divider) state.divider.hidden = mode === "original";
      for (const controls of doc.querySelectorAll(".paperloom-reader-controls")) {
        for (const button of controls.querySelectorAll(".paperloom-mode-button")) {
          button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
        }
        controls.querySelector(".paperloom-swap-button").disabled = mode !== "split";
      }
      doc.defaultView.dispatchEvent(new doc.defaultView.Event("resize"));
      this.position(state);
    },
    position(state) {
      if (!state.pane) return;
      const placeholder = state.placeholder.getBoundingClientRect();
      const outer = state.reader._iframe.getBoundingClientRect();
      const container = state.container.getBoundingClientRect();
      state.pane.hidden = state.mode === "original" || !placeholder.width || !outer.width;
      Object.assign(state.pane.style, {
        left: `${outer.left + placeholder.left - container.left}px`,
        top: `${outer.top + placeholder.top - container.top}px`,
        width: `${placeholder.width}px`, height: `${placeholder.height}px`,
      });
    },
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
      state.saved = saved;
      state.kind = kind;
      state.savedLanguage = kind === "translate" ? PL.language() : null;
      if (state.uri !== uri || !state.frame) {
        this.unbindFrame(state);
        let schema = null;
        try { schema = (await IOUtils.readJSON(PathUtils.join(PathUtils.parent(path), "manifest.json"))).schema; }
        catch (_) { /* Metadata can be recovered from the saved page. */ }
        if (state.loadRequest !== request) return;
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
    fontSize() { return Math.max(12, Math.min(40, Number(PL.pref("ocrFontSize", 17)) || 17)); },
    async setFont(size) {
      size = Math.max(12, Math.min(40, Math.round(Number(size) || 17)));
      PL.setPref("ocrFontSize", size);
      for (const state of this.states.values()) {
        if (state.fontSize) state.fontSize.value = String(size);
      }
      await Promise.all(Array.from(this.states.values(), state => this.applyFont(state)));
    },
    async applyFont(state) {
      const context = state.frame?.browsingContext;
      if (!context) return;
      // Saved readers have a 17px base. Native text zoom works across content
      // processes and page loads, even when the optional reader actor fails.
      // It changes text without scaling the source PDF or raster figures.
      context.textZoom = this.fontSize() / 17;
    },
    readerActor(global) { return global.getActor("PaperLoomReader"); },
    viewOptions() { return { mode: PL.pref("translationView", "bilingual"), focus: PL.pref("blockCompare", true), appearance: PL.Appearance.read() }; },
    async initializeFrame(state) {
      const results = await Promise.allSettled([this.applyFont(state), this.applyStyle(state), this.applyOptions(state)]);
      for (const result of results) if (result.status === "rejected") Zotero.logError(result.reason);
    },
    async applyStyle(state) {
      const global = state.frame?.browsingContext?.currentWindowGlobal;
      if (global?.documentURI?.scheme === "file") await this.readerActor(global).sendQuery("reader-style", { css: PL.readerStyle });
    },
    bindFrame(state) {
      const id = state.frame?.browsingContext?.id;
      if (id) {
        state.bridgeID = id;
        state.frame._paperloomAction = data => this.action(state, data);
        state.frame._paperloomOptions = () => ({ view: this.viewOptions(), css: PL.readerStyle });
      }
    },
    unbindFrame(state) {
      if (state.frame) { delete state.frame._paperloomAction; delete state.frame._paperloomOptions; }
      state.bridgeID = null;
    },
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
    async action(state, data) {
      state.lastBridgeAction = data?.type;
      if (!PL.alive || !state.saved || state.frame?.currentURI?.spec !== state.uri) throw new Error("阅读窗口已更新，请重试");
      if (data.type === "copy-latex") {
        if (typeof data.text !== "string" || data.text.length > 100000) throw new Error("公式内容无效");
        Zotero.Utilities.Internal.copyTextToClipboard(data.text);
        return true;
      }
      if (data.type === "excel") return PL.Artifacts.exportTables(state.reader._window, data.items);
      if (data.type === "save-ocr-edits") return PL.Editing.save(state.reader, data).then(()=>true);
      if (data.type === "translation-mode") { await this.translationMode(data.mode === "translation" ? "translation" : "bilingual"); return true; }
      if (data.type === "locate") {
        if (state.mode !== "split") {
          if (data.revealPDF === true) await this.setMode(state.reader, "split");
          else return true; // Selecting/copying text keeps the user's reading mode.
        }
        return this.locate(state, data);
      }
      throw new Error("不支持的阅读操作");
    },
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
    refresh() {
      for (const [reader, state] of this.states) {
        if (!state.pane) continue;
        const source = Zotero.Items.get(reader.itemID);
        if (!source) continue;
        const record = PL.Store.get(source);
        const active = PL.Tasks.active?.source.id === source.id;
        const legacy = !!state.saved && state.exportSchema != null && state.exportSchema < 5;
        state.heading.textContent = active ? PL.Tasks.active.message || (PL.Tasks.active.kind === "ocr" ? "正在 OCR…" : "正在翻译…")
          : state.kind === "ocr" ? "OCR 版本" : `译文 · ${PL.language()}`;
        if (!active && legacy) state.heading.textContent += state.kind === "ocr" ? " · 旧版 OCR：重新 OCR 后可使用段落对照" : " · 旧版译文，可删除后重新翻译";
        if (!active && state.kind === "translate" && PL.Editing.stale(record)) state.heading.textContent += " · 原文已修改，点击翻译可更新";
        for (const button of state.pane.querySelectorAll("button")) {
          const action = button.dataset.action;
          if (["bilingual", "translation-only", "clear-translation"].includes(action)) {
            button.hidden = state.kind !== "translate" && action !== "clear-translation";
            if (action === "clear-translation") button.hidden = !record?.translations?.[PL.language()]?.attachmentID;
            if (action === "bilingual" || action === "translation-only") button.setAttribute("aria-pressed", String(PL.pref("translationView", "bilingual") === (action === "bilingual" ? "bilingual" : "translation")));
          }
          if (["ocr", "force-ocr", "clear-ocr"].includes(action)) button.hidden = state.kind === "translate";
          if (action === "edit-ocr") { button.hidden = state.kind !== "ocr"; button.title = "编辑 Markdown、调整类型、插入标题或删除图片；保存为新版本"; }
          if (action === "show-ocr" || action === "show-translation") button.setAttribute("aria-pressed", String(state.kind === (action === "show-ocr" ? "ocr" : "translate")));
          if (action === "block-compare") {
            button.textContent = PL.pref("blockCompare", true) ? "关闭段落高亮" : "开启段落高亮";
            button.setAttribute("aria-pressed", String(!legacy && PL.pref("blockCompare", true)));
          }
          button.disabled = (["ocr", "force-ocr", "translate"].includes(action) && active)
            || (legacy && ["bilingual", "translation-only", "block-compare"].includes(action))
            || (action === "show-translation" && !record?.translations?.[PL.language()]?.attachmentID)
            || (action === "show-ocr" && !record?.ocr?.attachmentID)
            || (action === "export" && !state.saved);
          if (action === "edit-ocr") button.disabled = active || !state.saved;
          if (["bilingual", "translation-only", "block-compare"].includes(action)) button.title = legacy
            ? (state.kind === "ocr" ? "旧版 OCR 不含段落坐标，请点击「重新 OCR」生成新结果" : "旧版译文不支持此功能，请删除译文后基于当前 OCR 重新翻译")
            : "切换显示与 PDF 段落对照，不重新识别或翻译";
          if (action === "block-compare" && !legacy) button.title = "仅切换区域高亮与背景淡化；双向段落定位始终可用";
        }
        if (!state.saved && !active) {
          const status = state.kind === "ocr" ? record?.ocr : record?.translations?.[PL.language()];
          if (status?.state === "failed") state.message.textContent = `处理失败：${status.message}\n可点击上方按钮重试，已完成的缓存会保留。`;
          if (state.mode !== "original" && status?.state === "ready" && !state.loadingSaved) {
            state.loadingSaved = true;
            PL.Store.saved(source, state.kind).then(saved => saved && this.showSaved(reader, saved, state.kind))
              .catch(error => Zotero.logError(error)).finally(() => { state.loadingSaved = false; });
          }
        }
      }
    },
    remove(reader) {
      const state = this.states.get(reader);
      if (!state) return;
      this.unbindFrame(state); this.clearHighlight(state);
      state.pdfDoc?.removeEventListener("click", state.pdfClick, true);
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
