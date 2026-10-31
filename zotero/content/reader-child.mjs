export class PaperLoomReaderChild extends JSWindowActorChild {
  handleEvent(event) {
    if (event.type === "click") {
      this.explicitLocate = !!event.target.closest?.('button[data-action="locate"]');
      return;
    }
    if (event.type === "PaperLoomAction") {
      if (this.document.body) this.document.body.dataset.actionReceived = "true";
      let data;
      try { data = JSON.parse(event.detail); }
      catch (_) { return; }
      if (data.type === "locate" && data.revealPDF === undefined) {
        data.revealPDF = this.explicitLocate === true;
        this.explicitLocate = false;
      }
      this.sendQuery("action", data).then(value => this.result({ id: data.id, value }), error => this.result({ id: data.id, error: error.message }));
      return;
    }
    if (!this.document.body?.hasAttribute("data-export-schema") && !this.document.querySelector('nav')?.textContent.includes("PaperLoom")) return;
    try {
      this.sendQuery("ready").then(config => {
        if (!this.document.body) return;
        this.document.body.dataset.zoteroBridge = String(config?.available === true);
        this.applyView(config?.view || {});
        // Presentation failures must not break the action bridge or mapping.
        try { this.applyStyle(config?.css || ""); } catch (_) {}
      }).catch(() => {});
    }
    catch (_) { /* The add-on may have been disabled while this page loaded. */ }
  }
  result(value) {
    // Saving an edit replaces the browser before its action promise resolves.
    // A response to that departed page is no longer needed.
    try {
      const view = this.contentWindow;
      this.document.dispatchEvent(new view.CustomEvent("PaperLoomActionResult", { detail: JSON.stringify(value) }));
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
      style.setProperty(`--pl-${kind}-font`, family ? JSON.stringify(family) + ", sans-serif" : "system-ui, sans-serif");
    }
  }
  source(node) {
    if (!node) return null;
    const content=node.querySelector('.pl-source') || node.querySelector('.pl-target');
    const copy=content?.cloneNode(true);
    copy?.querySelectorAll('button,.pl-block-tools').forEach(tool=>tool.remove());
    return {block:node.dataset.blockId,page:Number(node.dataset.page),bbox:JSON.parse(node.dataset.bbox||'[]'),
      text:node.dataset.sourceText||copy?.textContent||'',type:node.dataset.blockType};
  }
  receiveMessage(message) {
    if (!this.document.body) return null;
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
