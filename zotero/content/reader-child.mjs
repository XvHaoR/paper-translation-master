import { isSavedReader, actionPayload, READER_PROTOCOL } from "./reader-protocol.mjs";
import { enhance, exportPage } from "./paper-layout.mjs";

export class PaperLoomReaderChild extends JSWindowActorChild {
  handleEvent(event) {
    if (event.type === "DOMContentLoaded" || event.type === "pageshow") this.loadedDocument = this.document;
    if (event.type === "click") {
      this.explicitLocate = !!event.target.closest?.('button[data-action="locate"]');
      return;
    }
    if (event.type === "PaperLoomAction") {
      if (!isSavedReader(this.document)) return;
      if (this.document.body) this.document.body.dataset.actionReceived = "true";
      let data;
      const document = this.document;
      try { data = JSON.parse(event.detail); actionPayload(data); }
      catch (error) { this.result({ id: data?.id, error: error.message }, document); return; }
      if (data.type === "locate" && data.revealPDF === undefined) {
        data.revealPDF = this.explicitLocate === true;
        this.explicitLocate = false;
      }
      Promise.resolve().then(() => this.sendQuery("action", data)).then(value => this.result({ id: data.id, value }, document), error => this.result({ id: data.id, error: error.message }, document));
      return;
    }
    if (event.type === "PaperLoomReaderConnect") this.attempts = 0;
    if (isSavedReader(this.document)) this.connect();
  }
  async connect() {
    if (this.destroyed || !isSavedReader(this.document)) return { available: false };
    if (this.connecting) return this.connecting;
    const document = this.document;
    this.bridgeState("connecting");
    this.connecting = Promise.resolve().then(async () => {
      try {
        if (this.destroyed || this.document !== document) return { available: false };
        const loaded = await this.documentReady(document);
        if (this.destroyed || this.document !== document || !document.body) return { available: false };
        if (!loaded) throw new Error("阅读页面加载超时，请重新打开当前 OCR／译文页面");
        try { enhance(document); } catch (error) { this.report(error); }
        const config = await this.sendQuery("ready", { protocol: READER_PROTOCOL });
        if (this.destroyed || this.document !== document || !document.body) return { available: false };
        document.body.dataset.zoteroBridge = String(config?.available === true);
        if (config?.available !== true) {
          this.bridgeState("waiting", new Error("阅读窗口正在连接，请稍后再试"));
          this.scheduleConnect();
          return config;
        }
        this.attempts = 0;
        this.cancelRetry();
        this.bridgeState("ready");
        // Appearance is optional; an invalid style must not disable actions.
        try { this.applyView(config.view || {}); this.applyStyle(config.css || ""); }
        catch (error) { this.report(error); }
        return config;
      } catch (error) {
        if (!this.destroyed && this.document === document) {
          document.body.dataset.zoteroBridge = "false";
          this.bridgeState("waiting", error);
          this.scheduleConnect();
        }
        return { available: false, error: error.message };
      } finally { this.connecting = null; }
    });
    return this.connecting;
  }
  documentReady(document) {
    // A file URI and body marker can appear before deferred runtime scripts
    // have installed locate/copy/edit controls. DOMContentLoaded is the shared
    // readiness boundary for both proactive and event-triggered handshakes.
    const view = this.contentWindow;
    const mustWait = () => {
      if (document.readyState === "loading") return true;
      // "interactive" starts before defer scripts execute. Conversely, an
      // actor created after DOMContentLoaded must not await a past event: the
      // runtime's final exported object proves its controls are installed.
      if (document.readyState !== "interactive" || this.loadedDocument === document ||
          !document.querySelector('script[src="assets/offline_reader.js"]')) return false;
      try { return !(view.wrappedJSObject || view).PaperLoomOffline; }
      catch (_) { return true; }
    };
    if (!mustWait()) return Promise.resolve(true);
    return new Promise(resolve => {
      let timer, settled = false;
      const finish = loaded => {
        if (settled) return;
        settled = true;
        try {
          document.removeEventListener("DOMContentLoaded", loadedEvent);
          view.removeEventListener?.("pagehide", cancel);
          if (timer) view.clearTimeout(timer);
        } finally {
          if (this.cancelDocumentReady === cancel) this.cancelDocumentReady = null;
          resolve(loaded);
        }
      };
      const loadedEvent = () => { this.loadedDocument = document; finish(true); };
      const cancel = () => finish(false);
      this.cancelDocumentReady = cancel;
      document.addEventListener("DOMContentLoaded", loadedEvent, { once: true });
      view.addEventListener?.("pagehide", cancel, { once: true });
      timer = view.setTimeout(() => finish(false), 10000);
      if (!mustWait()) loadedEvent();
    });
  }
  scheduleConnect() {
    if (this.retryTimer || this.destroyed) return;
    const delays = [100, 250, 500, 1000, 2000];
    const attempt = this.attempts || 0;
    if (attempt >= delays.length) {
      this.bridgeState("error", new Error("阅读器连接未完成，请关闭当前阅读标签页后重新打开"));
      return;
    }
    this.attempts = attempt + 1;
    this.retryTimer = this.contentWindow.setTimeout(() => {
      this.retryTimer = null; this.connect();
    }, delays[attempt]);
  }
  cancelRetry() {
    if (this.retryTimer) this.contentWindow.clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }
  bridgeState(state, error) {
    if (!this.document.body) return;
    this.document.body.dataset.readerBridgeState = state;
    if (error) this.document.body.dataset.readerBridgeError = error.message || String(error);
    else delete this.document.body.dataset.readerBridgeError;
    if (state === "error") this.report(error);
  }
  report(error) { console.error("[PaperLoom saved-reader bridge]", error?.message || String(error)); }
  didDestroy() {
    this.destroyed = true;
    try { this.cancelDocumentReady?.(); this.cancelRetry(); } catch (_) { /* The content global was already disposed. */ }
  }
  result(value, document = this.document) {
    // Saving an edit replaces the browser before its action promise resolves.
    // A response to that departed page is no longer needed.
    try {
      if (this.destroyed || this.document !== document) return;
      const view = this.contentWindow;
      document.dispatchEvent(new view.CustomEvent("PaperLoomActionResult", { detail: JSON.stringify(value) }));
    } catch (_) {}
  }
  applyStyle(css) {
    let style = this.document.getElementById("paperloom-reader-layout");
    if (!style) {
      style = this.document.createElement("style");
      style.id = "paperloom-reader-layout";
      this.document.head.append(style);
    }
    if (style.textContent !== css) style.textContent = css;
  }
  applyView(data) {
    this.document.body.dataset.translationMode = data.mode === "translation" ? "translation" : "bilingual";
    this.document.body.classList.toggle("pl-focus", !!data.focus);
    const appearance = data.appearance || {};
    const style = this.document.documentElement.style;
    for (const kind of ["ocr", "translation"]) {
      const color = appearance[kind + "TextColor"] || "CanvasText";
      const family = appearance[kind + "FontFamily"];
      style.setProperty(`--pl-${kind}-color`, color);
      if (!family && this.document.body.classList.contains("pl-paper-reader")) style.removeProperty(`--pl-${kind}-font`);
      else style.setProperty(`--pl-${kind}-font`, family ? JSON.stringify(family) + ", sans-serif" : "system-ui, sans-serif");
    }
  }
  source(node) {
    if (!node) return null;
    const content=node.querySelector('.pl-source') || node.querySelector('.pl-target');
    const copy=content?.cloneNode(true);
    copy?.querySelectorAll('button,.pl-block-tools').forEach(tool=>tool.remove());
    return {block:node.dataset.blockId,page:Number(node.dataset.page),bbox:JSON.parse(node.dataset.bbox||'[]'),regions:JSON.parse(node.dataset.regions||'[]'),
      text:node.dataset.sourceText||copy?.textContent||'',type:node.dataset.blockType};
  }
  receiveMessage(message) {
    if (message.name === "reader-connect") { this.attempts = 0; return this.connect(); }
    if (!this.document.body) return null;
    if (message.name === "paper-export") return exportPage(this.document, message.data?.assets || "assets");
    if (message.name === "editor-command") {
      const editor = this.contentWindow.wrappedJSObject.PaperLoomEditor;
      if (!editor) throw new Error("编辑器尚未加载，请稍后再试");
      editor.command(message.data.command);
      return true;
    }
    if (message.name === "reader-style") {
      this.applyStyle(message.data.css || "");
      return true;
    }
    if (message.name === "reader-view") {
      this.applyView(message.data);
      return { schema: Number(this.document.body.dataset.exportSchema || 0) };
    }
    if (message.name === "block-source") return this.source(Array.from(this.document.querySelectorAll('.pl-block')).find(node=>node.dataset.blockId===message.data.id));
    if (message.name === "page-blocks") return Array.from(this.document.querySelectorAll('.pl-block'))
      .filter(node=>Number(node.dataset.page)<=message.data.page&&Number(node.dataset.page)>=message.data.page-1).map(node=>this.source(node));
    if (message.name === "select-block" || message.name === "locate-point") {
      const candidates = Array.from(this.document.querySelectorAll(".pl-block"));
      const node = message.name === "select-block" ? candidates.find(node => node.dataset.blockId === message.data.id) : candidates.filter(node => {
        if (Number(node.dataset.page) !== Number(message.data.page)) return false;
        const box = JSON.parse(node.dataset.bbox || "[]");
        return box.length === 4 && message.data.x >= box[0] && message.data.x <= box[2] && message.data.y >= box[1] && message.data.y <= box[3];
      }).sort((a,b)=>{
        const area = node => { const box=JSON.parse(node.dataset.bbox); return (box[2]-box[0])*(box[3]-box[1]); };
        return area(a)-area(b);
      })[0];
      if (!node) return false;
      this.document.querySelectorAll(".pl-block.selected").forEach(node => node.classList.remove("selected"));
      node.classList.add("selected"); this.document.body.classList.add("has-selection");
      node.scrollIntoView({ block: "center", behavior: "instant" });
      return this.source(node);
    }
    return null;
  }
}
