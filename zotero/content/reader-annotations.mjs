import {modelRange, normalizedText, textModel} from "./text-alignment.mjs";

export function textAnchor(model, start, end) {
  return {quote: model.text.slice(start, end).trim(),
    before: model.text.slice(Math.max(0, start - 120), start), after: model.text.slice(end, end + 120)};
}

export function anchorOffsets(text, anchor) {
  const {value, offsets} = normalizedText(text), quote = normalizedText(anchor.quote).value;
  if (!quote) return null;
  const before = normalizedText(anchor.before).value, after = normalizedText(anchor.after).value;
  let best = -1, match = null;
  for (let start = value.indexOf(quote); start >= 0; start = value.indexOf(quote, start + 1)) {
    const end = start + quote.length;
    const score = (before && value.slice(Math.max(0, start - before.length), start) === before ? before.length : 0)
      + (after && value.slice(end, end + after.length) === after ? after.length : 0);
    if (score > best) { best = score; match = [offsets[start].start, offsets[end - 1].end]; }
    else if (score === best) match = null;
  }
  return match;
}

export class ReaderAnnotations {
  constructor(actor) {
    this.actor = actor;
    this.doc = actor.document;
    this.win = actor.contentWindow;
    this.names = [];
    this.localRanges = new Map();
  }
  openAt(event) {
    if (!event.isTrusted || event.button !== 0 || this.doc.body.classList.contains("pl-editing") ||
        event.target.closest?.("button,a,input,textarea")) return;
    const block = event.target.closest?.(".pl-block");
    const selection = this.win.getSelection();
    if (!block || !selection?.isCollapsed) return;
    for (const {range, mark} of this.localRanges.get(block.dataset.blockId) || []) {
      const inside = Array.from(range.getClientRects()).some(rect => event.clientX >= rect.left &&
        event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom);
      if (!inside) continue;
      selection.removeAllRanges(); selection.addRange(range.cloneRange());
      this.show(range, {block:mark.block, localSource:mark.scope === "ocr", source:mark.source || null, target:mark.target || null});
      event.stopPropagation();
      return;
    }
  }
  show(range, data) {
    const changed = JSON.stringify(data) !== JSON.stringify(this.selection);
    if (changed) {
      this.selection = data;
      this.saved = false;
    }
    if (!this.tools) {
      const tools = this.tools = this.doc.createElement("div");
      tools.className = "pl-annotation-tools";
      tools.setAttribute("role", "group");
      tools.setAttribute("aria-label", "划词批注");
      tools.addEventListener("mousedown", event => event.preventDefault());
      for (const [name, note] of [["高亮", false], ["批注", true]]) {
        const button = this.doc.createElement("button");
        button.type = "button";
        button.textContent = name;
        button.addEventListener("click", event => {
          if (event.isTrusted) this.save(note);
        });
        tools.append(button);
      }
      this.remove = this.doc.createElement("button");
      this.remove.type = "button"; this.remove.textContent = "删除高亮与批注"; this.remove.hidden = true;
      this.remove.addEventListener("click", event => { if (event.isTrusted) this.save(false, true); });
      tools.append(this.remove);
      this.status = this.doc.createElement("span");
      this.status.setAttribute("role", "status");
      tools.append(this.status);
      this.doc.body.append(tools);
      this.installStyle();
    }
    this.tools.hidden = false;
    if (changed) {
      const scope = data.target ? "translation" : data.localSource ? "ocr" : null;
      const previous = scope && this.marks?.find(mark => mark.scope === scope && mark.block === data.block
        && JSON.stringify(data.target ? mark.target : mark.source) === JSON.stringify(data.target || data.source));
      this.remove.hidden = !previous;
      if (!this.pending) this.status.textContent = previous?.comment || (data.target ? "仅保存到译文" : data.localSource ? "仅保存到识别文本" : "");
      for (const button of this.tools.querySelectorAll("button")) button.disabled = this.pending || !(data.source || data.target);
    }
    const bounds = range.getBoundingClientRect(), {offsetWidth, offsetHeight} = this.tools;
    this.tools.style.left = Math.max(4, Math.min(this.win.innerWidth - offsetWidth - 4, bounds.left)) + "px";
    this.tools.style.top = Math.max(4, bounds.top > offsetHeight + 6 ? bounds.top - offsetHeight - 6 : bounds.bottom + 6) + "px";
  }
  hide() {
    if (!this.tools) return;
    this.selection = null;
    this.tools.hidden = true;
    this.status.textContent = "";
  }
  async save(note, remove = false) {
    if (!(this.selection?.source || this.selection?.target) || this.pending || this.saved) return;
    const selection = this.selection;
    this.pending = true;
    for (const button of this.tools.querySelectorAll("button")) button.disabled = true;
    this.status.textContent = "正在保存…";
    try {
      const result = await this.actor.sendQuery("action", {...selection, type: "annotation-save", note, remove});
      if (this.destroyed) return;
      if (result?.marks) this.paint(result.marks);
      if (this.selection === selection && result?.removed) {
        this.win.getSelection()?.removeAllRanges(); this.hide(); return;
      }
      if (this.selection === selection) {
        this.status.textContent = result?.canceled ? "" : selection.target ? "已保存到译文" : selection.localSource ? "已保存到识别文本" : "已保存到 PDF 批注";
        this.saved = !result?.canceled;
      }
    } catch (error) {
      if (!this.destroyed && this.selection === selection) this.status.textContent = error.message || "批注保存失败";
    } finally {
      this.pending = false;
      if (!this.destroyed && this.selection && this.selection !== selection) {
        this.status.textContent = this.selection.target ? "仅保存到译文" : this.selection.localSource ? "仅保存到识别文本" : "";
      }
      if (this.tools) for (const button of this.tools.querySelectorAll("button")) button.disabled = !(this.selection?.source || this.selection?.target) || this.saved;
    }
  }
  installStyle() {
    if (this.style) return;
    this.style = this.doc.createElement("style");
    this.style.textContent = `
      .pl-annotation-tools {position:fixed;z-index:100;display:flex;align-items:center;gap:5px;
        max-width:calc(100vw - 8px);padding:4px;background:Canvas;color:CanvasText;
        border:1px solid #8888;border-radius:5px;box-shadow:0 2px 5px #0003;font:12px system-ui}
      .pl-annotation-tools[hidden],.pl-editing .pl-annotation-tools {display:none}
      .pl-annotation-tools button {white-space:nowrap}
      .pl-annotation-tools [role="status"]{max-height:8em;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
      .pl-annotation-tools [role="status"]:empty,.pl-annotation-tools button[hidden]{display:none}
    `;
    this.doc.head.append(this.style);
  }
  paint(marks) {
    this.marks = marks;
    const registry = this.win.CSS?.highlights;
    if (!registry || !this.win.Highlight) return;
    this.installStyle();
    this.clearMarks();
    const ranges = new Map(), blocks = new Map([...this.doc.querySelectorAll(".pl-block")].map(block => [block.dataset.blockId, block]));
    for (const mark of marks) {
      const block = blocks.get(mark.block);
      if (!block || Number(block.dataset.page) !== mark.page) continue;
      const target = block.querySelector(".pl-target");
      const local = mark.scope === "translation";
      const source = local ? target : block.querySelector(".pl-source") || (this.actor.interactions?.options.kind !== "translate" && target);
      if (!source) continue;
      const model = textModel(source), offsets = anchorOffsets(model.text, local ? mark.target : mark.source);
      if (!offsets) continue;
      const color = /^#[0-9a-f]{6}$/i.test(mark.color) ? mark.color.toLowerCase() : "#ffd400";
      const name = "paperloom-annotation-" + color.slice(1);
      if (!ranges.has(name)) ranges.set(name, []);
      const range = modelRange(model, ...offsets);
      if (!range) continue;
      ranges.get(name).push(range);
      if (mark.scope === "translation" || mark.scope === "ocr") {
        if (!this.localRanges.has(mark.block)) this.localRanges.set(mark.block, []);
        this.localRanges.get(mark.block).push({range, mark});
      }
    }
    for (const [name, matches] of ranges) {
      if (!this.rules?.has(name)) {
        this.rules ||= new Set();
        this.rules.add(name);
        this.style.append(`::highlight(${name}){background:#${name.slice(-6)}70}`);
      }
      registry.set(name, new this.win.Highlight(...matches.filter(Boolean)));
      this.names.push(name);
    }
  }
  clearMarks() {
    this.localRanges.clear();
    for (const name of this.names) this.win.CSS?.highlights?.delete(name);
    this.names = [];
  }
  destroy() {
    this.destroyed = true;
    this.clearMarks();
    this.tools?.remove();
    this.style?.remove();
  }
}
