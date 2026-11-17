import {textModel, sourceSelectionOffsets, alignSourceSelection, modelRange} from "./text-alignment.mjs";

const NAME = "paperloom-reverse-selection";
export class ReverseHighlight {
  constructor(actor) {
    this.actor = actor; this.doc = actor.document; this.win = actor.contentWindow;
    this.revision = 0;
    this.resize = () => this.paintOverlay();
    this.win.addEventListener("resize", this.resize);
    this.input = () => { this.inputAt = Date.now(); this.clear(); };
    this.doc.addEventListener("pointerdown", this.input, true);
    this.doc.addEventListener("keydown", this.input, true);
  }
  receive(data) {
    if (data.revision < this.revision || this.inputAt > data.inputAt) return false;
    this.revision = data.revision;
    this.clear();
    if (data.clear || !this.actor.interactions?.options.split || this.doc.body.classList.contains("pl-editing")) return false;
    const candidates = [];
    for (const block of this.doc.querySelectorAll(".pl-block")) {
      const source = block.querySelector(".pl-source"), target = block.querySelector(".pl-target");
      if (!source || !target || !target.getBoundingClientRect().height) continue;
      const meta = this.actor.source(block);
      const regions = [{page:meta.page,bbox:meta.bbox},...(meta.regions || [])].filter(r=>r.page===data.page && r.bbox?.length===4);
      const near = regions.some(r=>data.point && data.point.x>=r.bbox[0]-.02 && data.point.x<=r.bbox[2]+.02
        && data.point.y>=r.bbox[1]-.02 && data.point.y<=r.bbox[3]+.02);
      if (!regions.length && meta.page !== data.page-1) continue;
      const model = textModel(source), offsets = sourceSelectionOffsets(model.text, data.text);
      if (offsets || near) candidates.push({block,target,source:model,offsets,near});
    }
    const exact = candidates.filter(value=>value.offsets), nearExact = exact.filter(value=>value.near);
    const pool = nearExact.length ? nearExact : exact.length ? exact : candidates.filter(value=>value.near);
    if (pool.length !== 1) return false;
    const hit = pool[0], model = textModel(hit.target);
    const aligned = hit.offsets && alignSourceSelection(hit.source.text, model.text, ...hit.offsets);
    const range = aligned && modelRange(model, aligned.start, aligned.end);
    this.block = hit.block; this.range = range;
    if (!range) hit.target.classList.add("pl-reverse-paragraph");
    this.target = hit.target;
    this.ensureStyle();
    this.actor.interactions.suppressScroll();
    // Showing the match must not focus the translated page or replace the
    // user's native PDF selection. It also must not trigger scroll feedback.
    const rect = range?.getBoundingClientRect() || hit.target.getBoundingClientRect();
    if (rect.top < 30 || rect.bottom > this.win.innerHeight-30) {
      this.doc.scrollingElement.scrollTop += rect.top - this.win.innerHeight*.3;
    }
    if (range && this.win.CSS?.highlights && this.win.Highlight) {
      this.win.CSS.highlights.set(NAME, new this.win.Highlight(range));
    } else if (range) {
      this.paintOverlay();
      this.observer = new this.win.ResizeObserver(this.resize); this.observer.observe(this.doc.body);
    }
    const precision = range ? "sentence" : "paragraph";
    this.doc.body.dataset.reverseSelectionMatch = precision;
    return {block:hit.block.dataset.blockId, precision};
  }
  ensureStyle() {
    if (this.doc.getElementById("paperloom-reverse-style")) return;
    const style = this.doc.createElement("style"); style.id = "paperloom-reverse-style";
    style.textContent = `::highlight(${NAME}){background:#ffd95d;color:#191919}
      .pl-reverse-paragraph{background:#ffd95d55!important;outline:2px solid #e9b900}
      .pl-reverse-overlay{position:absolute;pointer-events:none;z-index:20;inset:0;overflow:visible}
      .pl-reverse-overlay>i{position:absolute;background:#ffd95d88;mix-blend-mode:multiply;pointer-events:none}`;
    this.doc.head.append(style);
  }
  paintOverlay() {
    if (!this.range || this.win.CSS?.highlights && this.win.Highlight) return;
    if (!this.overlay) {
      this.overlay=this.doc.createElement("div"); this.overlay.className="pl-reverse-overlay";
      this.overlay.setAttribute("aria-hidden","true"); this.doc.body.append(this.overlay);
    }
    this.overlay.replaceChildren();
    const origin=this.overlay.getBoundingClientRect();
    for (const rect of this.range.getClientRects()) {
      if (!rect.width || !rect.height) continue;
      const item=this.doc.createElement("i");
      item.style.cssText=`left:${rect.left-origin.left}px;top:${rect.top-origin.top}px;width:${rect.width}px;height:${rect.height}px`;
      this.overlay.append(item);
    }
  }
  clear() {
    this.win.CSS?.highlights?.delete(NAME);
    this.target?.classList.remove("pl-reverse-paragraph");
    this.observer?.disconnect(); this.observer=null;
    this.overlay?.remove(); this.overlay=null; this.range=null; this.target=null; this.block=null;
    if (this.doc.body) delete this.doc.body.dataset.reverseSelectionMatch;
  }
  destroy() {
    this.clear(); this.win.removeEventListener("resize",this.resize);
    this.doc.removeEventListener("pointerdown",this.input,true); this.doc.removeEventListener("keydown",this.input,true);
  }
}
