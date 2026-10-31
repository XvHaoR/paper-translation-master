"use strict";
(() => {
  const PL = PaperLoom;
  PL.ReaderPanel = {
    create(state) {
      if (state.pane) return;
      const { reader } = state;
      const nativeDoc = state.doc;
      const doc = reader._window.document;
      const target = nativeDoc.getElementById("split-view");
      if (!target) throw new Error("当前 Zotero 阅读器结构不支持 OCR 面板");
      const divider = nativeDoc.createElement("div");
      divider.className = "paperloom-divider";
      divider.tabIndex = 0;
      divider.setAttribute("role", "separator");
      divider.setAttribute("aria-orientation", "vertical");
      divider.setAttribute("aria-label", "调整 PDF 与 OCR 宽度");
      const setRatio = ratio => {
        state.ratio = Math.max(.2, Math.min(.8, ratio));
        PL.setPref("splitRatio", String(state.ratio));
        PL.ReaderUI.layout(state);
      };
      divider.addEventListener("keydown", event => {
        if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
          event.preventDefault();
          setRatio(state.ratio + (event.key === "ArrowLeft" ? -.05 : .05) * (state.swapped ? -1 : 1));
        }
      });
      divider.addEventListener("pointerdown", event => {
        event.preventDefault();
        divider.setPointerCapture(event.pointerId);
        const move = event => {
          const bounds = target.getBoundingClientRect();
          const ratio = (event.clientX - bounds.left) / bounds.width;
          setRatio(state.swapped ? 1 - ratio : ratio);
        };
        const stop = () => {
          divider.removeEventListener("pointermove", move);
          divider.removeEventListener("pointerup", stop);
          divider.removeEventListener("pointercancel", stop);
        };
        divider.addEventListener("pointermove", move);
        divider.addEventListener("pointerup", stop);
        divider.addEventListener("pointercancel", stop);
      });
      const html = tag => doc.createElementNS("http://www.w3.org/1999/xhtml", tag);
      const chromeStyle = html("style");
      chromeStyle.textContent = PL.ReaderLayout.css;
      doc.documentElement.append(chromeStyle);
      const pane = html("section");
      pane.className = "paperloom-ocr-pane";
      pane.style.cssText = "position:absolute;z-index:5;box-sizing:border-box;overflow:hidden";
      pane.setAttribute("aria-label", "论文翻译大师 OCR 阅读窗口");
      const toolbar = html("div");
      toolbar.className = "paperloom-pane-toolbar";
      const heading = html("strong");
      heading.textContent = "OCR 版本";
      toolbar.append(heading);
      const button = (label, action, name) => {
        const node = html("button");
        node.textContent = label;
        node.dataset.action = name;
        node.addEventListener("click", PL.run(reader._window, action));
        toolbar.append(node);
        return node;
      };
      button("识别 / 重试", () => PL.ReaderUI.process(reader, "ocr"), "ocr");
      button("重新 OCR", () => PL.ReaderUI.process(reader, "force-ocr"), "force-ocr");
      button("清除 OCR", () => PL.Tasks.clearOCR(Zotero.Items.get(reader.itemID)), "clear-ocr");
      button("删除译文", () => PL.Tasks.clearTranslation(Zotero.Items.get(reader.itemID)), "clear-translation");
      button("翻译", () => PL.ReaderUI.process(reader, "translate"), "translate");
      button("看 OCR", () => PL.ReaderUI.load(reader, "ocr"), "show-ocr");
      button("看译文", () => PL.ReaderUI.load(reader, "translate"), "show-translation");
      button("编辑 OCR", () => PL.Editing.open(reader), "edit-ocr");
      button("逐段双语", () => PL.ReaderUI.translationMode("bilingual"), "bilingual");
      button("仅译文", () => PL.ReaderUI.translationMode("translation"), "translation-only");
      button("段落对照", () => PL.ReaderUI.toggleCompare(reader), "block-compare");
      button("导出 Markdown", async () => {
        const source = Zotero.Items.get(reader.itemID);
        const saved = await PL.Store.saved(source, state.kind);
        if (!saved) throw new Error("请先保存当前版本");
        await PL.Artifacts.exportMarkdown(reader._window, saved, state.kind);
      }, "export");
      button("设置", () => PL.UI.settings(reader._window), "settings");
      const fontGroup = html("span");
      fontGroup.style.cssText = "display:flex;align-items:center;gap:4px;font-size:12px";
      fontGroup.textContent = "字号";
      const smaller = html("button");
      smaller.textContent = "A−";
      smaller.title = "缩小 OCR / 译文字号";
      smaller.dataset.action = "font-smaller";
      smaller.addEventListener("click", PL.run(reader._window, () => PL.ReaderUI.setFont(PL.ReaderUI.fontSize() - 1)));
      const fontSize = html("input");
      fontSize.type = "number";
      fontSize.min = "12";
      fontSize.max = "40";
      fontSize.value = String(PL.ReaderUI.fontSize());
      fontSize.setAttribute("aria-label", "OCR 与译文字号");
      fontSize.style.cssText = "width:49px;padding:3px;border:1px solid #8885;border-radius:4px;background:transparent;color:inherit;font:inherit";
      fontSize.addEventListener("input", PL.run(reader._window, () => {
        const size = Number(fontSize.value);
        // Let incomplete input remain editable; valid values and spinner steps
        // apply immediately without requiring a blur/change event.
        if (Number.isFinite(size) && size >= 12 && size <= 40) return PL.ReaderUI.setFont(size);
      }));
      fontSize.addEventListener("change", PL.run(reader._window, () => PL.ReaderUI.setFont(Number(fontSize.value))));
      const larger = html("button");
      larger.textContent = "A+";
      larger.title = "放大 OCR / 译文字号";
      larger.dataset.action = "font-larger";
      larger.addEventListener("click", PL.run(reader._window, () => PL.ReaderUI.setFont(PL.ReaderUI.fontSize() + 1)));
      const reset = html("button");
      reset.textContent = "重置";
      reset.dataset.action = "font-reset";
      reset.addEventListener("click", PL.run(reader._window, () => PL.ReaderUI.setFont(17)));
      fontGroup.append(smaller, fontSize, larger, reset);
      toolbar.append(fontGroup);
      const content = html("div");
      content.className = "paperloom-pane-content";
      const message = html("div");
      message.className = "paperloom-pane-message";
      content.append(message);
      pane.append(toolbar, content);
      // The PDF reader's resource:// document cannot embed file:// pages. Keep
      // its layout placeholder inside the reader, and host a sandboxed content
      // browser in the surrounding Zotero chrome at the exact same rectangle.
      const placeholder = nativeDoc.createElement("div");
      placeholder.className = "paperloom-ocr-placeholder";
      target.append(divider, placeholder);
      const container = reader._tabContainer || reader._iframe.parentElement;
      const oldPosition = container.style.position;
      container.style.position = "relative";
      container.append(pane);
      Object.assign(state, { pane, placeholder, container, oldPosition, chromeStyle, divider, heading, content, message, fontSize });
      const observer = new nativeDoc.defaultView.ResizeObserver(() => PL.ReaderUI.position(state));
      observer.observe(placeholder);
      state.resizeObserver = observer;
      state.positionListener = () => PL.ReaderUI.position(state);
      reader._window.addEventListener("resize", state.positionListener);
      PL.ReaderUI.position(state);
    },
    refresh(state) {
      const reader = state.reader;
      if (!state.pane) return;
      const source = Zotero.Items.get(reader.itemID);
      if (!source) return;
      const record = PL.Store.get(source);
      const active = PL.Tasks.active?.source.id === source.id;
      const legacy = !!state.saved && state.exportSchema != null && state.exportSchema < 5;
      state.heading.textContent = active ? PL.Tasks.active.message || (PL.Tasks.active.kind === "ocr" ? "正在 OCR…" : "正在翻译…")
        : state.kind === "ocr" ? "OCR 版本" : `译文 · ${PL.language()}`;
      if (!active && legacy) state.heading.textContent += state.kind === "ocr" ? " · 旧版 OCR：重新 OCR 后可使用段落对照" : " · 旧版译文，可删除后重新翻译";
      if (!active && state.kind === "translate" && PL.Editing.stale(record)) state.heading.textContent += " · 原文已修改，点击翻译可更新";
      for (const button of state.pane.querySelectorAll("button")) {
        const action = button.dataset.action;
        if (["bilingual", "translation-only", "clear-translation"].includes(action)) {
          button.hidden = state.kind !== "translate" && action !== "clear-translation";
          if (action === "clear-translation") button.hidden = !record?.translations?.[PL.language()]?.attachmentID;
          if (action === "bilingual" || action === "translation-only") button.setAttribute("aria-pressed", String(PL.pref("translationView", "bilingual") === (action === "bilingual" ? "bilingual" : "translation")));
        }
        if (["ocr", "force-ocr", "clear-ocr"].includes(action)) button.hidden = state.kind === "translate";
        if (action === "edit-ocr") { button.hidden = state.kind !== "ocr"; button.title = "编辑 Markdown、调整类型、插入标题或删除图片；保存为新版本"; }
        if (action === "show-ocr" || action === "show-translation") button.setAttribute("aria-pressed", String(state.kind === (action === "show-ocr" ? "ocr" : "translate")));
        if (action === "block-compare") {
          button.textContent = PL.pref("blockCompare", true) ? "关闭段落高亮" : "开启段落高亮";
          button.setAttribute("aria-pressed", String(!legacy && PL.pref("blockCompare", true)));
        }
        button.disabled = (["ocr", "force-ocr", "translate"].includes(action) && active)
          || (legacy && ["bilingual", "translation-only", "block-compare"].includes(action))
          || (action === "show-translation" && !record?.translations?.[PL.language()]?.attachmentID)
          || (action === "show-ocr" && !record?.ocr?.attachmentID)
          || (action === "export" && !state.saved);
        if (action === "edit-ocr") button.disabled = active || !state.saved;
        if (["bilingual", "translation-only", "block-compare"].includes(action)) button.title = legacy
          ? (state.kind === "ocr" ? "旧版 OCR 不含段落坐标，请点击「重新 OCR」生成新结果" : "旧版译文不支持此功能，请删除译文后基于当前 OCR 重新翻译")
          : "切换显示与 PDF 段落对照，不重新识别或翻译";
        if (action === "block-compare" && !legacy) button.title = "仅切换区域高亮与背景淡化；双向段落定位始终可用";
      }
      if (!state.saved && !active) {
        const status = state.kind === "ocr" ? record?.ocr : record?.translations?.[PL.language()];
        if (status?.state === "failed") state.message.textContent = `处理失败：${status.message}\n可点击上方按钮重试，已完成的缓存会保留。`;
        if (state.mode !== "original" && status?.state === "ready" && !state.loadingSaved) {
          state.loadingSaved = true;
          PL.Store.saved(source, state.kind).then(saved => saved && PL.ReaderUI.showSaved(reader, saved, state.kind))
            .catch(error => Zotero.logError(error)).finally(() => { state.loadingSaved = false; });
        }
      }
    },
  };
})();
