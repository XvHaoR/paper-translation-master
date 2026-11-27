"use strict";
window.addEventListener("DOMContentLoaded", () => {
  const { controller: PL, owner, action: initialAction } = window.arguments[0].wrappedJSObject;
  const $ = id => document.getElementById(id);
  const pageSize = 100;
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  let rows = [], page = 0, generation = 0, initialized = false, loading = false, busy = false;
  let visibleIDs = [], onScreen = [], currentBatch = null;
  const selection = new Set();
  const operation = () => $("action").value;
  let submission = 0;
  const titleOnly = () => operation() === "translate-title";
  const withTitle = () => operation() === "translate" && $("translateTitle").checked;
  const translatedTitle = row => PL.TitleTranslations.text(row.titleItem);
  const available = row => titleOnly() ? row.titleAvailable : row.available;
  const chosenRows = () => rows.filter(row => available(row) && selection.has(row.id));
  const taskKind = () => operation() === "ocr" && $("force").checked ? "force-ocr" : operation();
  const node = (name, parent, text) => {
    const el = document.createElementNS("http://www.w3.org/1999/xhtml", name);
    if (text != null) el.textContent = text;
    parent.append(el);
    return el;
  };
  function summary(row, kind) {
    if (kind === "translate-title") {
      if (!row.titleAvailable) return { key: "unavailable", text: "—", tone: "idle" };
      return translatedTitle(row) ? { key: "ready", text: "已翻译标题", tone: "ready" }
        : { key: "pending", text: "未翻译标题", tone: "idle" };
    }
    if (!row.source) return { key: "unavailable", text: "—", tone: "idle" };
    const record = PL.Store.get(row.source);
    const result = kind === "ocr" ? record?.ocr : record?.translations?.[PL.language()];
    const state = result?.state === "ready" && !result.attachmentID ? "pending" : result?.state || "pending";
    if (state === "ready" && kind === "translate" && PL.Editing.stale(record))
      return { key: "pending", text: "待更新", tone: "warning", title: "解析原文已修改" };
    if (state === "ready" && result?.needsReview)
      return { key: "ready", text: "已完成 · 待核对", tone: "warning" };
    const labels = { ready: kind === "ocr" ? "已识别" : "已翻译", failed: "失败 · 可重试",
      processing: kind === "ocr" ? "识别中" : "翻译中", queued: "排队中", missing: "待下载",
      pending: kind === "ocr" ? "未识别" : "未翻译" };
    const tones = { ready: "ready", failed: "error", processing: "running", queued: "running", missing: "warning" };
    return { key: state, text: labels[state] || labels.pending, tone: tones[state] || "idle", title: result?.message || "" };
  }
  function needsWork(row) {
    if (!available(row)) return false;
    const state = summary(row, operation()).key;
    return ["pending","failed","missing"].includes(state)
      || (state === "ready" && withTitle() && row.titleAvailable && !translatedTitle(row));
  }
  function filtered() {
    const query = $("search").value.trim().toLocaleLowerCase(), filter = $("statusFilter").value;
    const result = rows.filter(row => {
      if (query && ![row.title,row.creator,row.year,translatedTitle(row)].join(" ").toLocaleLowerCase().includes(query)) return false;
      if (filter === "all") return true;
      if (filter === "unavailable") return !available(row);
      if (!available(row)) return false;
      return filter === "pending" ? needsWork(row) : summary(row, operation()).key === filter;
    });
    if ($("sort").value === "recent") result.sort((a,b) => b.dateAdded.localeCompare(a.dateAdded));
    if ($("sort").value === "title") result.sort((a,b) => collator.compare(a.title,b.title));
    return result;
  }
  function counts(all = filtered()) {
    const chosen = chosenRows().length, shown = all.filter(row => available(row) && selection.has(row.id)).length;
    const translating = operation() === "translate", titles = titleOnly(), locked = loading || busy;
    $("selection").textContent = "已选 " + chosen + " 篇" + (chosen !== shown ? "（筛选内 " + shown + " 篇）" : "");
    $("batchTitle").textContent = document.title = titles ? "批量翻译标题" : translating ? "批量翻译" : "批量识别";
    const target = PL.language();
    const langName = {zh:"中文", "zh-CN":"简体中文", "zh-TW":"繁体中文", en:"英语"}[target] || target;
    $("language").textContent = translating || titles ? "目标语言 · " + langName : "";
    const startLabel = titles ? "开始翻译标题" : translating ? "开始翻译" : $("force").checked ? "重新识别" : "开始识别";
    $("start").textContent = busy ? "正在提交…" : startLabel + (chosen ? "（" + chosen + " 篇）" : "");
    $("start").disabled = locked || !chosen;
    $("close").textContent = busy || currentBatch && !currentBatch.finished ? "关闭并在后台继续" : "关闭";
    $("forceRow").hidden = translating || titles;
    $("schemeRow").hidden = !PL.config.unified || translating || titles;
    $("translateTitleRow").hidden = !translating;
    $("translationColumn").textContent = titles ? "标题翻译状态" : "翻译状态";
    let hint = titles ? "直接翻译文献条目的标题，支持没有 PDF 的文献。"
      : translating ? "已有译文直接复用，缺少解析原文时会先识别。"
      : $("force").checked ? "将重新识别选中的 PDF。" : "已有识别结果直接复用。";
    if (withTitle()) hint += " 译后标题会显示在「翻译标题」列。";
    if (PL.config.unified && !titles) {
      const destination = PL.Recognition.destination(translating ? PL.Recognition.defaultMode() : $("scheme").value);
      if (destination) hint += " 需要识别的 PDF 将提交到" + destination + "。";
    }
    $("actionHint").textContent = hint;
    $("actionHint").hidden = busy || !!$("notice").textContent;
    for (const id of ["scope","action","force","scheme","translateTitle","selectFiltered","selectPending","clearSelection","selectPage"]) $(id).disabled = locked;
    $("clearSelection").disabled = locked || !chosen;
    const selectable = all.slice(page*pageSize,(page+1)*pageSize).filter(available);
    $("selectPage").disabled = locked || !selectable.length;
    $("selectPage").checked = !!selectable.length && selectable.every(row => selection.has(row.id));
    $("selectPage").indeterminate = !$("selectPage").checked && selectable.some(row => selection.has(row.id));
  }
  function statusCell(el, data) {
    if (el.textContent !== data.text) el.textContent = data.text;
    if (el.dataset.tone !== data.tone) el.dataset.tone = data.tone;
    const title = data.title || data.text;
    if (el.title !== title) el.title = title;
  }
  function updateDetail(el,row) {
    const pdfLabel = row.reason || (row.pdfTitle !== row.title ? row.pdfTitle : "");
    const text = [translatedTitle(row), titleOnly() ? (row.titleAvailable ? "" : "无标题") : pdfLabel].filter(Boolean).join(" · ");
    if (el.textContent !== text) { el.textContent = text; el.title = text; }
    el.hidden = !text;
  }
  function render(all = filtered()) {
    const pages = Math.ceil(all.length/pageSize);
    page = Math.max(0,Math.min(page,Math.max(0,pages-1)));
    visibleIDs = all.map(row => row.id);
    onScreen = [];
    const frag = document.createDocumentFragment();
    for (const row of all.slice(page*pageSize,(page+1)*pageSize)) {
      const enabled = available(row);
      const tr = node("tr",frag), chosen = enabled && selection.has(row.id);
      tr.classList.toggle("checked",chosen);
      tr.classList.toggle("unavailable",!enabled);
      tr.dataset.sourceId = String(row.id);
      const box = node("input",node("td",tr)); box.type = "checkbox"; box.checked = chosen;
      box.disabled = busy || loading || !enabled; box.id = "select-paper-" + row.id; box.dataset.id = String(row.id);
      box.setAttribute("aria-label","选择论文 " + row.title);
      box.addEventListener("change",() => {
        if (box.checked) selection.add(row.id); else selection.delete(row.id);
        tr.classList.toggle("checked",box.checked); counts();
      });
      const cell = node("td",tr), title = node("label",cell,row.title);
      title.className = "paper-title"; title.htmlFor = box.id; title.title = row.title;
      if ($("sort").value === "recent" && row.localAdded)
        node("div",cell,"添加于 " + row.localAdded).className = "paper-detail";
      const detail = node("div",cell);
      detail.className = "paper-detail"; detail.dataset.detail = "true";
      updateDetail(detail,row);
      node("td",tr,[row.creator,row.year].filter(Boolean).join(" · ") || "—");
      const cells = {};
      for (const kind of ["ocr","translate"]) {
        cells[kind] = node("span",node("td",tr)); cells[kind].className = "state-label";
        cells[kind].dataset.status = kind;
        statusCell(cells[kind],summary(row,kind === "translate" && titleOnly() ? "translate-title" : kind));
      }
      onScreen.push({row,cells,detail});
    }
    $("papers").replaceChildren(frag);
    $("empty").hidden = !!all.length;
    $("empty").textContent = loading ? "正在读取文献…" : rows.length ? "没有符合当前条件的论文。可更换状态或搜索词。" : "当前范围没有可列出的文献。";
    $("pageInfo").textContent = all.length ? (page*pageSize+1) + "–" + Math.min(all.length,(page+1)*pageSize) + " / " + all.length + " 篇" : "0 篇";
    $("previous").disabled = page === 0; $("next").disabled = page+1 >= pages;
    counts(all);
  }
  function notice(text, tone = "") {
    $("notice").textContent = text; $("notice").dataset.tone = tone;
    $("actionHint").hidden = busy || !!text;
  }
  async function load() {
    const request = ++generation;
    loading = true; notice(""); render();
    try {
      const next = await PL.BatchPicker.rows(owner,$("scope").value);
      if (window.closed || request !== generation) return;
      const valid = new Set(next.filter(row => row.available || row.titleAvailable).map(row => row.id));
      for (const id of selection) if (!valid.has(id)) selection.delete(id);
      if (!initialized) for (const row of next) {
        if (row.preselected || ((titleOnly() || !row.source) && row.titlePreselected)) selection.add(row.id);
      }
      rows = next; initialized = true; page = 0;
    } catch (error) {
      if (!window.closed && request === generation) { rows = []; selection.clear(); notice(error.message,"error"); }
    } finally {
      if (!window.closed && request === generation) { loading = false; render(); }
    }
  }
  function setAction(value) {
    if (busy) return;
    $("action").value = ["translate","translate-title"].includes(value) ? value : "ocr";
    $("force").checked = value === "force-ocr"; notice(""); page=0; render();
  }
  $("search").addEventListener("input",() => { page=0; render(); });
  for (const id of ["sort","statusFilter"]) $(id).addEventListener("change",() => { page=0; render(); });
  $("scope").addEventListener("change",load);
  $("action").addEventListener("change",() => setAction(operation()));
  for (const id of ["force","scheme","translateTitle"]) $(id).addEventListener("change",() => render());
  $("selectFiltered").addEventListener("click",() => { for (const row of filtered()) if (available(row)) selection.add(row.id); render(); });
  $("selectPending").addEventListener("click",() => { selection.clear(); for (const row of filtered()) if (needsWork(row)) selection.add(row.id); render(); });
  $("clearSelection").addEventListener("click",() => { selection.clear(); render(); });
  $("selectPage").addEventListener("change",() => {
    for (const row of filtered().slice(page*pageSize,(page+1)*pageSize)) {
      if (!available(row)) continue;
      if ($("selectPage").checked) selection.add(row.id); else selection.delete(row.id);
    }
    render();
  });
  $("previous").addEventListener("click",() => { page--; render(); });
  $("next").addEventListener("click",() => { page++; render(); });
  $("close").addEventListener("click",() => window.close());
  function progress(batch) {
    if (window.closed || !batch) return;
    $("batchProgress").max = Math.max(batch.total,1); $("batchProgress").value = batch.done;
    notice("已处理 " + batch.done + " / " + batch.total + " 篇，成功 " + batch.success + " 篇，失败 " + batch.failed + " 篇");
  }
  $("start").addEventListener("click",async () => {
    const chosen = chosenRows().map(row => row.id);
    if (busy || loading || !chosen.length) return;
    const ticket = ++submission, kind = taskKind();
    busy = true; currentBatch = null;
    $("batchProgress").hidden = false; $("batchProgress").removeAttribute("value");
    notice("正在提交 " + chosen.length + " 篇文献…"); render();
    try {
      const batch = await PL.BatchPicker.submit(owner,kind,rows,chosen,
        $("schemeRow").hidden ? undefined : $("scheme").value, withTitle(), created => {
          if (!window.closed && ticket === submission) {
            currentBatch = created; busy = false; progress(created); render();
          }
        });
      if (!window.closed && ticket === submission)
        notice("处理完成，成功 " + (batch?.success || 0) + " 篇" + (batch?.skipped ? "，跳过 " + batch.skipped + " 篇" : "") + "。","success");
    } catch (error) {
      if (!window.closed && ticket === submission)
        notice((currentBatch ? "成功 " + currentBatch.success + " 篇，失败 " + currentBatch.failed + " 篇。\n" : "") + error.message,"error");
    } finally {
      if (!window.closed && ticket === submission) { busy = false; currentBatch = null; $("batchProgress").hidden = true; render(); }
    }
  });
  const timer = window.setInterval(() => {
    if (window.closed || loading) return;
    if ($("statusFilter").value !== "all" || $("search").value.trim()) {
      const all = filtered();
      if (all.length !== visibleIDs.length || all.some((r,i) => r.id !== visibleIDs[i])) { render(all); }
    }
    for (const {row,cells,detail} of onScreen) {
      statusCell(cells.ocr,summary(row,"ocr"));
      statusCell(cells.translate,summary(row,titleOnly() ? "translate-title" : "translate"));
      updateDetail(detail,row);
    }
    if (currentBatch && !currentBatch.finished) progress(currentBatch);
  },1000);
  window.addEventListener("unload",() => window.clearInterval(timer),{once:true});
  if (PL.config.unified) {
    PL.Recognition.populate($("scheme"));
    $("scheme").value = PL.Recognition.defaultMode();
  }
  window.PaperLoomBatch = {setAction,reload:load};
  setAction(initialAction); load();
});
