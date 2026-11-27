import {alignedSourceRange, textModel, selectedOffsets} from "./text-alignment.mjs";
import {ReaderAnnotations, textAnchor} from "./reader-annotations.mjs";

const ANCHOR = 0.28;
export class ReaderInteractions {
  constructor(actor) {
    this.actor = actor; this.doc = actor.document; this.win = actor.contentWindow;
    this.listeners = []; this.options = {}; this.sequence = 0; this.lastInput = 0;
    this.annotations = new ReaderAnnotations(actor);
    this.listen(this.doc, "click", event => this.annotations.openAt(event), true);
    this.listen(this.doc, "scroll", () => this.queueScroll(), true);
    for (const event of ["wheel", "pointerdown", "touchstart", "keydown"]) this.listen(this.doc, event, () => {
      this.lastInput = Date.now(); this.mutedUntil = 0;
    }, true);
    this.listen(this.doc, "selectionchange", () => this.queueSelection(180));
    this.listen(this.doc, "pointerup", () => this.queueSelection(0));
    this.listen(this.doc, "keyup", () => this.queueSelection(0));
    this.listen(this.doc, "keydown", event => {
      if (event.key === "Escape") this.annotations.hide();
    });
    this.listen(this.win, "pagehide", () => this.destroy());
  }
  listen(target, name, callback, capture = false) {
    target.addEventListener(name, callback, capture); this.listeners.push([target, name, callback, capture]);
  }
  configure(options) {
    const wasActive = this.active(); this.options = options;
    if (wasActive !== this.active()) { this.suppressScroll(); this.lastInput = 0; }
  }
  active() { return !this.destroyed && this.options.split && this.options.syncScroll && !this.doc.body.classList.contains("pl-editing"); }
  suppressScroll() { this.mutedUntil = Date.now() + 500; this.win.clearTimeout(this.scrollTimer); this.scrollTimer = null; }
  queueScroll() {
    if (!this.active() || Date.now() < this.mutedUntil || !this.lastInput) return;
    if (this.scrollTimer) return;
    this.scrollTimer = this.win.setTimeout(() => { this.scrollTimer = null; this.reportScroll(); }, 70);
  }
  async reportScroll() {
    if (!this.active() || Date.now() < this.mutedUntil) return;
    const anchor = this.win.innerHeight * ANCHOR;
    const candidates = Array.from(this.doc.querySelectorAll(".pl-block")).map(node => ({node, rect: node.getBoundingClientRect()}))
      .filter(({rect}) => rect.height > 0 && rect.width > 0);
    candidates.sort((a, b) => Math.max(a.rect.top - anchor, anchor - a.rect.bottom, 0) - Math.max(b.rect.top - anchor, anchor - b.rect.bottom, 0));
    const hit = candidates[0];
    if (!hit) return;
    const progress = Math.max(0, Math.min(1, (anchor - hit.rect.top) / hit.rect.height));
    try { await this.actor.sendQuery("action", {...this.actor.source(hit.node), type: "reader-scroll", progress, inputAt: this.lastInput}); }
    catch (_) { /* A departing frame must not interrupt scrolling. */ }
  }
  scrollTo(data) {
    if (!this.active() || this.lastInput > data.inputAt) return false;
    const node = Array.from(this.doc.querySelectorAll(".pl-block")).find(node => node.dataset.blockId === data.block);
    if (!node || !Number.isFinite(data.progress)) return false;
    this.suppressScroll();
    const rect = node.getBoundingClientRect(), scrolling = this.doc.scrollingElement;
    scrolling.scrollTop += rect.top + rect.height * Math.max(0, Math.min(1, data.progress)) - this.win.innerHeight * ANCHOR;
    return true;
  }
  queueSelection(delay) {
    this.win.clearTimeout(this.selectionTimer);
    this.selectionTimer = this.win.setTimeout(() => this.selection(), delay);
  }
  async selection() {
    if (this.destroyed) return;
    if (this.doc.body.classList.contains("pl-editing")) { this.annotations.hide(); return; }
    const selection = this.win.getSelection();
    const sequence = ++this.sequence;
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
      if (!this.annotations.tools?.contains(this.doc.activeElement)) this.annotations.hide();
      if (this.lastSelection) {
        this.lastSelection = "";
        try { await this.actor.sendQuery("action", {type: "selection-clear"}); } catch (_) {}
      }
      return;
    }
    const range = selection.getRangeAt(0), element = node => node.nodeType === 1 ? node : node.parentElement;
    const block = element(range.startContainer)?.closest(".pl-block");
    if (!block || element(range.endContainer)?.closest(".pl-block") !== block) { this.annotations.hide(); return; }
    const target = element(range.startContainer)?.closest(".pl-target,.pl-source");
    if (!target || !target.contains(range.endContainer) || element(range.startContainer)?.closest("button,input,textarea,.pl-edit-fields")) {
      this.annotations.hide(); return;
    }
    const model = textModel(target), offsets = selectedOffsets(model, range);
    if (!offsets || !selection.toString().trim()) { this.annotations.hide(); return; }
    const original = target.classList.contains("pl-source") || this.options.kind !== "translate" && !block.querySelector(".pl-source");
    const source = original ? model : null;
    const letters = original && model.text.match(/\p{L}/gu);
    const localSource = !!letters && !/[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u.test(model.text)
      && (model.text.match(/\p{Script=Han}/gu) || []).length > letters.length / 2;
    const aligned = original && alignedSourceRange(source.text, model.text, ...offsets, true);
    this.annotations.show(range, {
      block: block.dataset.blockId,
      localSource,
      source: localSource ? textAnchor(model, ...offsets) : aligned?.text ? textAnchor(source, aligned.start, aligned.end) : null,
      target: original ? null : textAnchor(model, ...offsets),
    });
    if (!this.options.split || !original || localSource) return;
    const sentence = aligned?.text || "";
    const signature = `${block.dataset.blockId}:${original}:${offsets.join(":")}:${sentence || "paragraph"}`;
    if (signature === this.lastSelection) return;
    this.lastSelection = signature;
    for (const old of this.doc.querySelectorAll(".pl-block.selected")) old.classList.remove("selected");
    block.classList.add("selected"); this.doc.body.classList.add("has-selection");
    try {
      const result = await this.actor.sendQuery("action", {...this.actor.source(block), type: "selection-locate",
        selectionText: sentence || "", highlight: "selection"});
      if (this.destroyed || sequence !== this.sequence) return;
      this.doc.body.dataset.selectionMatch = result?.precision || "paragraph";
    } catch (_) { /* Normal text selection and copying must always remain available. */ }
  }
  destroy() {
    if (this.destroyed) return;
    this.destroyed = true; this.sequence++;
    this.win.clearTimeout(this.selectionTimer); this.win.clearTimeout(this.scrollTimer);
    this.annotations.destroy();
    for (const [target, name, callback, capture] of this.listeners) target.removeEventListener(name, callback, capture);
    this.listeners = [];
  }
}
