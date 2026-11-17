"use strict";
(() => {
  const PL = PaperLoom;
  const labels = { layout: "直接解析 PDF", local: "本地 OCR", cloud: "云端 OCR" };
  PL.Recognition = {
    labels,
    defaultMode() {
      const mode = PL.pref("recognitionMode", "layout");
      return Object.hasOwn(labels, mode) ? mode : "layout";
    },
    label(mode) { return labels[mode] || "旧版识别结果"; },
    service(mode) { return mode === "local" ? PL.LocalOCR : PL.Service; },
    autoPref(mode = this.defaultMode()) {
      return { layout: "unifiedAutoLayout", local: "unifiedAutoLocal", cloud: "unifiedAutoCloud" }[mode];
    },
  };
})();
