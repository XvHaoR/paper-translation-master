"use strict";
window.addEventListener("DOMContentLoaded", async () => {
  const {owner, library, controller} = window.arguments[0].wrappedJSObject;
  const $ = id => document.getElementById(id);
  const ns = "http://www.w3.org/1999/xhtml";
  let busy = false, stop = false, searchGeneration = 0;
  function element(parent, tag, text) {
    const node = document.createElementNS(ns, tag);
    if (text) node.textContent = text;
    parent.append(node);
    return node;
  }
  function embedding() {
    if ($("backend").value === "local") return {backend: "local"};
    return {backend: "api", url: $("endpoint").value.trim(), model: $("model").value.trim()};
  }
  async function run(task, indexing = false) {
    if (busy) return;
    busy = true;
    stop = false;
    for (const id of ["build", "search", "scope", "backend", "endpoint", "model", "key"]) $(id).disabled = true;
    $("stop").hidden = !indexing;
    $("stop").disabled = false;
    try { await task(); }
    catch (error) { if (!window.closed) $("status").textContent = error.message; }
    finally {
      busy = false;
      if (!window.closed) {
        for (const id of ["build", "search", "scope", "backend", "endpoint", "model", "key"]) $(id).disabled = false;
        $("stop").hidden = true;
      }
    }
  }
  const preference = await controller.preference(library);
  $("backend").value = preference.embedding.backend;
  $("endpoint").value = preference.embedding.url || "";
  $("model").value = preference.embedding.model || "";
  $("key").value = preference.key;
  $("remote").hidden = $("backend").value === "local";
  $("auto").addEventListener("click", () => run(async () => {
    const chosen = embedding();
    if (chosen.backend === "api" && (!chosen.url || !chosen.model)) throw new Error("请填写嵌入接口地址和模型名");
    await controller.configure(library, chosen, $("key").value.trim());
    $("status").textContent = "已保存，本库已识别及以后识别的论文会自动加入该索引。";
  }));
  $("backend").addEventListener("change", () => {
    searchGeneration++;
    $("remote").hidden = $("backend").value === "local";
    $("results").replaceChildren();
    $("status").textContent = "搜索会使用当前嵌入方式对应的索引。";
  });
  $("build").addEventListener("click", () => run(async () => {
    $("status").textContent = "正在检查已保存的论文和识别结果…";
    const result = await controller.index(owner, library, $("scope").value, embedding(),
      $("remote").hidden ? "" : $("key").value.trim(),
      (done, total) => { if (!window.closed) $("status").textContent = `正在检查或索引 ${done}/${total} 篇…`; },
      () => stop || window.closed);
    if (!window.closed) $("status").textContent = `${stop ? "已停止" : "索引已更新"} · 新增或更新 ${result.updated} 篇 · 复用 ${result.reused} 篇`
      + (result.failed ? ` · 失败 ${result.failed} 篇。首个错误：${result.firstError}` : "");
  }, true));
  $("stop").addEventListener("click", () => {
    stop = true;
    $("stop").disabled = true;
    $("status").textContent = "正在完成当前论文的索引…";
  });
  $("queryForm").addEventListener("submit", event => {
    event.preventDefault();
    const query = $("query").value.trim();
    if (!query) { $("query").focus(); return; }
    run(async () => {
      $("results").replaceChildren();
      $("status").textContent = "正在搜索相关段落…";
      const hits = await controller.search(library, query, embedding(), $("remote").hidden ? "" : $("key").value.trim());
      if (window.closed) return;
      const failed = controller.indexError(library);
      $("status").textContent = hits.length ? `找到 ${hits.length} 篇候选论文` : failed
        ? "自动索引未完成：" + failed : "暂无匹配结果。新识别论文会在后台加入索引。";
      const generation = ++searchGeneration, missing = [];
      function pair(parent, tag, original, counterpart, work) {
        const chinese = /[\u3400-\u9fff]/.test(original) && (original.match(/[\u3400-\u9fff]/g) || []).length >= Math.max(2, original.trim().length * .12);
        if (counterpart?.trim() === original.trim()) counterpart = "";
        const top = element(parent, tag, chinese ? original : counterpart);
        const bottom = element(parent, "p", chinese ? counterpart : original);
        bottom.className = "english";
        if (!counterpart) work.push({text:original, node:chinese ? bottom : top});
      }
      for (const hit of hits) {
        const work = [];
        missing.push(work);
        const article = element($("results"), "article");
        pair(article, "h2", hit.title || "未命名文献", hit.titleZh, work);
        for (const passage of hit.passages) {
          const row = element(article, "div");
          row.className = "passage";
          const quote = element(row, "div");
          element(quote, "p", [passage.label, passage.page ? `PDF 第 ${passage.page} 页` : ""].filter(Boolean).join(" · ")).className = "meta";
          if (passage.section) pair(quote, "strong", passage.section, passage.sectionZh, work);
          pair(quote, "p", passage.text, passage.zh || (passage.label === "标题" ? hit.titleZh : passage.label === "翻译标题" ? hit.title : ""), work);
          const open = element(row, "button", passage.page ? "定位原文" : hit.pdf ? "打开 PDF" : "查看文献");
          open.addEventListener("click", () => run(async () => {
            await controller.jump(owner, library, hit, passage);
            owner.focus();
          }));
        }
      }
      // Show saved results immediately. Resolve only the visible excerpts and stop on provider failure.
      (async () => {
        for (const work of missing.filter(list => list.length)) {
          if (generation !== searchGeneration || window.closed) return;
          for (let i = 0; i < work.length; i += 4) {
            const group = work.slice(i, i + 4);
            const reply = await controller.request("excerpts", {texts:group.map(part => part.text)});
            if (generation !== searchGeneration || window.closed) return;
            group.forEach((part, index) => { part.node.textContent = reply.translations[index]; });
          }
        }
      })().catch(error => {
        if (generation === searchGeneration && !window.closed) $("status").textContent = "已显示原文与已有译文。补译片段失败：" + error.message;
      });
    });
  });
  $("query").focus();
});
