/* Local HTML exploration. Owns presentation only; source IDs and coordinates
 * stay on the original .pl-block nodes, along with their existing listeners. */
const MEDIA = new Set(["figure", "table"]);
const LAYOUT_VERSION = "2";

function kind(block) {
  const value = block.dataset.role || block.dataset.blockType || "paragraph";
  if (value !== "caption") return value;
  return /^\s*(?:table\b|表\s*\d)/i.test(block.querySelector(".pl-source,.pl-target")?.textContent || "")
    ? "table_caption" : "figure_caption";
}

function captionIdentity(block) {
  const text = block.querySelector(".pl-source,.pl-target")?.textContent || "";
  const match = text.match(/^\s*((?:(?:extended\s+data|supplementary)\s+)?(?:fig(?:ure)?\.?|table)|[图表])\s*(S?\d+[a-z]?)(?=\b|[.：:|\s])/i);
  return match ? match[0].replace(/[.\s]/g, "").toLowerCase() : "";
}

function captionOwner(block, index, blocks) {
  const role = kind(block), wanted = role === "table_caption" ? "table" : "figure";
  // Associate only neighbouring content blocks. Never guess that intervening
  // body text belongs to a figure or change the OCR's paragraph boundaries.
  for (const offset of wanted === "table" ? [1, -1] : [-1, 1]) {
    let identity = captionIdentity(block);
    for (let next=index+offset; next>=0 && next<blocks.length; next+=offset) {
      const candidate = blocks[next];
      if (candidate.dataset.page !== block.dataset.page) break;
      if (kind(candidate) === wanted) return candidate;
      // Consecutive caption fragments, including column continuations, share
      // an owner. Body text, another caption type/number or page is a barrier.
      if (kind(candidate) !== role) break;
      const number = captionIdentity(candidate);
      if (identity && number && identity !== number) break;
      identity ||= number;
    }
  }
  return null;
}

function groupAbstracts(doc, blocks) {
  for (let index=0; index<blocks.length; index++) {
    if (kind(blocks[index]) !== "abstract") continue;
    const start=index;
    while (index+1<blocks.length && kind(blocks[index+1]) === "abstract") index++;
    const run=blocks.slice(start,index+1), previous=blocks[start-1];
    const explicit=previous && kind(previous)==="heading" && /^(?:abstract|summary|摘要)[.：:\s]*$/i.test(
      previous.querySelector(".pl-source,.pl-target")?.textContent.trim() || "");
    const panel=doc.createElement("section"); panel.className="paper-abstract";
    panel.setAttribute("role","doc-abstract");
    (explicit ? previous : run[0]).before(panel);
    if (explicit) panel.append(previous);
    run.forEach((block,i)=>{
      // Older saved HTML also has an automatically generated Abstract title.
      for (const title of block.querySelectorAll(".pl-source > h2:first-child,.pl-target > h2:first-child")) {
        if (/^Abstract$/i.test(title.textContent.trim()) && (explicit || i>0)) {
          title.hidden=true; title.dataset.paperSuppressedTitle="true";
        }
      }
      panel.append(block);
    });
    if (!explicit && !run[0].querySelector("h2")) {
      const title=doc.createElement("h2"); title.textContent="Abstract"; panel.prepend(title);
    }
  }
}

function groupUnownedCaptions(doc, blocks, owners) {
  for (let index=0; index<blocks.length; index++) {
    const first=blocks[index], role=kind(first);
    if (!role.endsWith("_caption") || owners.has(first)) continue;
    const run=[first]; let identity=captionIdentity(first);
    while (index+1<blocks.length) {
      const next=blocks[index+1], number=captionIdentity(next);
      if (owners.has(next) || kind(next)!==role || next.dataset.page!==first.dataset.page ||
          (identity && number && identity!==number)) break;
      run.push(next); identity ||= number; index++;
    }
    const panel=doc.createElement("div"); panel.className="paper-caption-group paper-media-caption";
    first.before(panel); panel.append(...run);
  }
}

function resetPresentation(doc, main) {
  // Upgrade an already enhanced offline export using the original nodes. IDs,
  // coordinates, selections and event handlers survive this presentation pass.
  const blocks=Array.from(main.querySelectorAll(".pl-block"));
  main.replaceChildren(...blocks);
  for (const block of blocks) {
    block.classList.remove("paper-abstract","paper-caption","paper-reference","paper-embedded-marker");
    block.querySelectorAll("[data-paper-suppressed-title]").forEach(title=>{
      title.hidden=false; delete title.dataset.paperSuppressedTitle;
    });
  }
  doc.querySelector(".paper-outline")?.remove();
}

function groupMedia(doc, block) {
  const type = kind(block), figure = doc.createElement("figure");
  figure.className = `paper-media paper-${type}`;
  figure.dataset.mediaBlock = block.dataset.blockId;
  block.before(figure);
  const stage = doc.createElement("div"); stage.className = "paper-media-stage";
  const caption = doc.createElement("figcaption"); caption.className = "paper-media-caption";
  if (type === "table") figure.append(caption, stage); else figure.append(stage, caption);
  stage.append(block);
  if (type === "figure") {
    const image = block.querySelector(".pl-target img");
    if (image) {
      const button = doc.createElement("button");
      button.type = "button"; button.className = "paper-image-open";
      button.dataset.paperZoom = "true"; button.textContent = "查看大图";
      button.setAttribute("aria-label", "打开完整图片"); stage.append(button);
    }
  }
  return caption;
}

function addOutline(doc, blocks) {
  if (doc.querySelector(".paper-outline")) return;
  const headings = blocks.filter(block => kind(block)==="heading" || (kind(block)==="abstract" &&
    block.closest(".paper-abstract")?.querySelector('.pl-block')===block));
  if (!headings.length) return;
  const details = doc.createElement("details"); details.className = "paper-outline";
  const summary = doc.createElement("summary"); summary.textContent = "文章目录"; details.append(summary);
  const list = doc.createElement("ol");
  headings.forEach((block, index) => {
    if (!block.id) block.id = `paper-section-${index}`;
    const title = block.querySelector("h1,h2,h3,h4,h5,h6")?.textContent
      || (kind(block)==="abstract" ? "Abstract" : block.querySelector(".pl-target")?.textContent) || "摘要";
    const li = doc.createElement("li"), link = doc.createElement("a");
    link.href = `#${block.id}`; link.textContent = title.trim().slice(0, 150);
    li.append(link); list.append(li);
  });
  details.append(list); doc.querySelector("main").before(details);
}

function imageViewer(doc) {
  let dialog = doc.querySelector("dialog.paper-image-dialog");
  if (dialog) return dialog;
  dialog = doc.createElement("dialog"); dialog.className = "paper-image-dialog";
  dialog.setAttribute("aria-label", "完整论文图片");
  const bar = doc.createElement("div"); bar.className = "paper-dialog-bar";
  const hint = doc.createElement("span"); hint.textContent = "完整图片 · 可滚动查看";
  const close = doc.createElement("button"); close.type = "button";
  close.textContent = "关闭"; close.dataset.paperClose = "true";
  const image = doc.createElement("img"); image.alt = "论文完整图片";
  bar.append(hint, close); dialog.append(bar, image); doc.body.append(dialog);
  return dialog;
}

function bind(doc) {
  if (doc.__paperHTMLBound) return;
  doc.__paperHTMLBound = true;
  doc.addEventListener("click", event => {
    const close = event.target.closest?.("[data-paper-close]");
    if (close) { event.stopPropagation(); close.closest("dialog").close(); return; }
    const button = event.target.closest?.("[data-paper-zoom]");
    if (!button) return;
    event.stopPropagation(); event.preventDefault();
    const source = button.closest(".paper-media").querySelector(".pl-target img");
    const dialog = imageViewer(doc), image = dialog.querySelector("img");
    image.src = source.currentSrc || source.src; image.alt = source.alt || "论文完整图片";
    if (!dialog.open) dialog.showModal();
  });
  // A downloaded HTML document has no source PDF controller. Plain paragraph
  // selection stays local; links, formulas and table controls still work.
  if (doc.body.dataset.paperStandalone === "true") doc.addEventListener("click", event => {
    if (event.target.closest?.(".pl-block") && !event.target.closest?.("a,button,input,textarea,select")) event.stopPropagation();
  }, true);
}

function enhance(doc) {
  const main = doc.querySelector("main"), body = doc.body;
  if (!main || !body) return false;
  body.classList.add("pl-paper-reader");
  if (body.dataset.paperLayoutVersion !== LAYOUT_VERSION) {
    if (body.dataset.paperEnhanced === "1") resetPresentation(doc,main);
    const blocks = Array.from(main.querySelectorAll(".pl-block"));
    const owners = new Map(), captions = new Map();
    blocks.forEach((block, index) => {
      const type = kind(block); block.dataset.paperRole = type;
      if (type.endsWith("_caption")) {
        const owner = captionOwner(block, index, blocks);
        if (owner) owners.set(block, owner);
        block.classList.add("paper-caption");
      }
      if (type === "reference") block.classList.add("paper-reference");
      if (type === "footnote") {
        const note = doc.createElement("aside");
        note.className = "paper-note"; note.setAttribute("role", "doc-footnote");
        block.before(note); note.append(block);
      }
      if (MEDIA.has(type)) captions.set(block, groupMedia(doc, block));
    });
    for (const [caption, owner] of owners) {
      captions.get(owner)?.append(caption);
      const text=caption.querySelector('.pl-target')?.textContent||'';
      const box=JSON.parse(caption.dataset.bbox||'[]'), media=JSON.parse(owner.dataset.bbox||'[]');
      if (/^\s*\(?[a-z]\)?\s*$/i.test(text) && box.length===4 && media.length===4
          && box[0]>=media[0]-.01 && box[1]>=media[1]-.01 && box[2]<=media[2]+.01 && box[3]<=media[3]+.01) {
        caption.classList.add('paper-embedded-marker');
      }
    }
    groupAbstracts(doc,blocks);
    groupUnownedCaptions(doc,blocks,owners);
    addOutline(doc, blocks);
    const nav = doc.querySelector("nav");
    if (nav && !nav.querySelector(".paper-view-badge")) {
      const badge = doc.createElement("span"); badge.className = "paper-view-badge";
      badge.textContent = "HTML 阅读"; nav.prepend(badge);
    }
    main.classList.add("paper-article"); body.dataset.paperEnhanced = "1";
    body.dataset.paperLayoutVersion = LAYOUT_VERSION;
  }
  imageViewer(doc); bind(doc); return true;
}

function exportPage(doc, assets = "assets") {
  if (!/^[A-Za-z0-9_.%()-]{1,240}$/.test(assets)) throw new Error("HTML 资源目录名称无效");
  if (doc.body.classList.contains("pl-editing")) throw new Error("请先保存或退出 OCR 编辑，再导出 HTML");
  enhance(doc);
  const root = doc.documentElement.cloneNode(true), body = root.querySelector("body");
  body.classList.remove("pl-focus", "has-selection"); body.dataset.paperStandalone = "true";
  for (const key of Object.keys(body.dataset)) {
    if (/^(?:readerBridge|zoteroBridge|actionReceived|mathControls)/.test(key)) delete body.dataset[key];
  }
  root.querySelectorAll(".pl-block.selected").forEach(node => node.classList.remove("selected"));
  root.querySelectorAll(".pl-block-tools,.pl-math-copy,.pl-edit-fields,.pl-editor-bar,#pl-editor-data,script[src$='ocr_editor.js'],style[id^='MJX']").forEach(node => node.remove());
  root.querySelectorAll("dialog").forEach(node => { node.removeAttribute("open"); node.querySelector("img")?.removeAttribute("src"); });
  root.querySelectorAll("[data-latex]").forEach(node => {
    const display = node.classList.contains("math-display") || node.closest(".pl-block")?.dataset.blockType === "equation_block";
    node.textContent = (display ? "\\[" : "\\(") + node.dataset.latex + (display ? "\\]" : "\\)");
  });
  root.querySelector("#pl-status")?.replaceChildren();
  if (!root.querySelector('script[src="assets/paper-reader.js"]')) {
    const script = doc.createElement("script"); script.src = "assets/paper-reader.js"; script.defer = true;
    root.querySelector("head").append(script);
  }
  for (const node of root.querySelectorAll("[src],[href]")) for (const attribute of ["src", "href"]) {
    const value = node.getAttribute(attribute);
    if (value?.startsWith("assets/")) node.setAttribute(attribute, assets + value.slice(6));
    else if (attribute === "href" && /^(?:ocr|translation|original)\.md$/.test(value || "")) node.setAttribute(attribute, `${assets}/${value}`);
  }
  return "<!DOCTYPE html>\n" + root.outerHTML;
}

export { enhance, exportPage };
