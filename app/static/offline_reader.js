/* Self-contained saved-page controls. Native actions use a restricted Zotero actor. */
(() => {
  "use strict";
  const body = document.body;
  let sequence = 0;
  const requests = new Map();
  document.addEventListener("PaperLoomActionResult", event => {
    const result = JSON.parse(event.detail);
    const pending = requests.get(result.id);
    if (pending) { requests.delete(result.id); result.error ? pending.reject(new Error(result.error)) : pending.resolve(result.value); }
  });
  async function action(data) {
    if (body.dataset.zoteroBridge === "true") {
      const id = ++sequence;
      return new Promise((resolve, reject) => {
        requests.set(id, { resolve, reject });
        document.dispatchEvent(new CustomEvent("PaperLoomAction", { bubbles: true, detail: JSON.stringify({ id, ...data }) }));
      });
    }
    if (data.type === "copy-latex") {
      const area = document.createElement("textarea");
      area.value = data.text; document.body.append(area); area.select();
      const copied = document.execCommand("copy"); area.remove();
      if (!copied) throw new Error("请选中公式的 LaTeX 源码后复制");
    }
    if (data.type === "save-ocr-edits") throw new Error("请在 Zotero 的 OCR 阅读窗口中保存修改");
    if (data.type === "excel") {
      const blob = new Blob(["\ufeff" + PaperLoomExcel.buildWorkbook(data.items)], { type: "application/vnd.ms-excel;charset=utf-8" });
      const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "paper-tables.xls";
      link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 500);
    }
  }
  function button(parent, label, callback, name) {
    const node = document.createElement("button"); node.type = "button"; node.textContent = label; node.dataset.action = name;
    node.addEventListener("click", async event => {
      event.stopPropagation();
      try {
        await callback(); node.title = "已完成";
        if (name === "copy-latex") { node.textContent = "已复制"; setTimeout(() => { node.textContent = label; }, 1200); }
      }
      catch (error) { document.getElementById("pl-status").textContent = error.message; }
    });
    parent.append(node); return node;
  }
  function select(section, navigate = true, revealPDF = false) {
    for (const selected of document.querySelectorAll(".pl-block.selected")) selected.classList.remove("selected");
    section.classList.add("selected"); body.classList.add("has-selection");
    if (navigate) return action({ type: "locate", revealPDF, block: section.dataset.blockId, page: Number(section.dataset.page), bbox: JSON.parse(section.dataset.bbox || "[]") });
  }
  for (const section of document.querySelectorAll(".pl-block")) {
    const tools = document.createElement("div"); tools.className = "pl-block-tools";
    if (Number(section.dataset.page) > 0) button(tools, `第 ${section.dataset.page} 页 · 对照 PDF`, () => select(section, true, true), "locate");
    section.prepend(tools);
    section.addEventListener("click", event => {
      if (event.target.closest("a,button,input,textarea,select,.pl-edit-fields") || !window.getSelection().isCollapsed) return;
      select(section).catch(error => { document.getElementById("pl-status").textContent = error.message; });
    });
  }
  for (const math of document.querySelectorAll("[data-latex]")) {
    button(math, "复制 LaTeX", async () => {
      await action({ type: "copy-latex", text: math.dataset.latex });
    }, "copy-latex").classList.add("pl-math-copy");
  }
  const tables = Array.from(document.querySelectorAll(".pl-target table"));
  const items = () => tables.map((table, i) => ({ name: `表${i + 1}`, rows: PaperLoomExcel.tableRows(table) }));
  tables.forEach((table, i) => button(table.closest(".pl-block").querySelector(".pl-block-tools"), "导出 Excel", () => action({ type: "excel", items: [{ name: `表${i + 1}`, rows: PaperLoomExcel.tableRows(table) }] }), "export-table"));
  if (tables.length) button(document.querySelector("nav"), "导出全部表格", () => action({ type: "excel", items: items() }), "export-tables");
  for (const node of document.querySelectorAll("button[data-translation-mode]")) {
    node.addEventListener("click", () => {
      body.dataset.translationMode = node.dataset.translationMode;
      action({ type: "translation-mode", mode: node.dataset.translationMode }).catch(() => {});
    });
  }
  window.PaperLoomOffline = { select, action };
})();
