"use strict";
(() => {
  const PL = PaperLoom;
  const labels = { layout: "直接解析 PDF", local: "本地 OCR", cloud: "飞桨云端 OCR", mineru: "MinerU 官方云端", "mineru-local": "MinerU 自部署" };
  PL.Recognition = {
    labels,
    populate(select) {
      select.replaceChildren();
      for (const [value, label] of Object.entries(labels)) {
        const option = select.ownerDocument.createElementNS("http://www.w3.org/1999/xhtml", "option");
        option.value = value;
        option.textContent = label;
        select.append(option);
      }
    },
    destination(mode) {
      return {cloud: "飞桨云端", mineru: "MinerU 官方云端", "mineru-local": "已配置的 MinerU 服务"}[mode];
    },
    defaultMode() {
      const mode = PL.pref("recognitionMode", "layout");
      return Object.hasOwn(labels, mode) ? mode : "layout";
    },
    label(mode) { return labels[mode] || "旧版识别结果"; },
    service(mode) { return mode === "local" ? PL.LocalOCR : PL.Service; },
    autoPref(mode = this.defaultMode()) {
      return { layout: "unifiedAutoLayout", local: "unifiedAutoLocal", cloud: "unifiedAutoCloud", mineru: "unifiedAutoMinerU", "mineru-local": "unifiedAutoMinerUServer" }[mode];
    },
  };
})();
