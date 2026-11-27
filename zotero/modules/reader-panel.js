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
      if (!target) throw new Error("当前 Zotero 阅读器结构不支持 解析 面板");
      const divider = nativeDoc.createElement("div");
      divider.className = "paperloom-divider";
      divider.tabIndex = 0;
      divider.setAttribute("role", "separator");
      divider.setAttribute("aria-orientation", "vertical");
      divider.setAttribute("aria-label", "调整 PDF 与 解析 宽度");
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
      if (!doc.getElementById("paperloom-window-style")) {
        state.chromeStyle = html("style");
        state.chromeStyle.textContent = PL.chromeStyle;
        doc.documentElement.append(state.chromeStyle);
      }
      const pane = html("section");
      pane.className = "paperloom-ocr-pane";
      pane.style.cssText = "position:absolute;z-index:5;box-sizing:border-box;overflow:hidden";
      pane.setAttribute("aria-label", "文献阅读面板");
      const shell = html("div"); shell.className = "paperloom-toolbar-shell";
      const toolbar = html("div"); toolbar.className = "paperloom-pane-toolbar";
      toolbar.setAttribute("role","group"); toolbar.setAttribute("aria-label","阅读工具");
      const primary = html("div"); primary.className = "paperloom-reader-actions";
      const tools = html("div"); tools.className = "paperloom-toolbar-tools";
      toolbar.append(primary,tools); shell.append(toolbar);
      const heading = html("div"); heading.className = "paperloom-pane-status";
      heading.setAttribute("role","status"); heading.hidden = true;
      const makeButton = (parent, label, action, key) => {
        const button = html("button"); button.type = "button"; button.textContent = label;
        if (key) button.dataset.action = key;
        if (action) button.addEventListener("click",PL.run(reader._window,action));
        parent.append(button); return button;
      };
      let openPanel = null, opener = null;
      const closeMenus = (returnFocus = false) => {
        if (!openPanel) return;
        openPanel.hidden = true; opener.setAttribute("aria-expanded","false");
        const previous = opener;
        openPanel = opener = null;
        if (returnFocus) previous.focus();
      };
      const popover = (label, name) => {
        const button = makeButton(tools,label,null,name);
        const panel = html("div"); panel.className = "paperloom-toolbar-popover";
        panel.id = "paperloom-" + name + "-" + (reader.tabID || reader.itemID);
        panel.setAttribute("role","group"); panel.setAttribute("aria-label",label);
        panel.hidden = true; shell.append(panel);
        button.setAttribute("aria-controls",panel.id); button.setAttribute("aria-expanded","false");
        button.addEventListener("click",() => {
          const wasOpen = openPanel === panel; closeMenus();
          if (wasOpen) return;
          openPanel = panel; opener = button; panel.hidden = false;
          button.setAttribute("aria-expanded","true");
          const first = [...panel.querySelectorAll("button,input,select")].find(el => !el.disabled && !el.hidden);
          first?.focus({preventScroll:true});
        });
        if (name !== "font-menu") panel.addEventListener("click",event => {
          if (event.target.closest("button")) closeMenus(true);
        });
        return panel;
      };
      const font = popover("字号","font-menu"), exports = popover("导出","export-menu"), more = popover("更多","more-menu");
      const fontRow = html("div"); fontRow.className = "paperloom-font-controls"; font.append(fontRow);
      const smaller = makeButton(fontRow,"A−",() => PL.ReaderUI.setFont(PL.ReaderUI.fontSize()-1),"font-smaller");
      smaller.setAttribute("aria-label","缩小字号");
      const fontSize = html("input"); fontSize.type = "number"; fontSize.min = "12"; fontSize.max = "40";
      fontSize.setAttribute("aria-label","正文字号"); fontSize.value = String(PL.ReaderUI.fontSize()); fontRow.append(fontSize);
      fontSize.addEventListener("input",PL.run(reader._window,() => {
        const size = Number(fontSize.value);
        if (size >= 12 && size <= 40) return PL.ReaderUI.setFont(size);
      }));
      fontSize.addEventListener("change",PL.run(reader._window,() => PL.ReaderUI.setFont(Number(fontSize.value))));
      const larger = makeButton(fontRow,"A+",() => PL.ReaderUI.setFont(PL.ReaderUI.fontSize()+1),"font-larger");
      larger.setAttribute("aria-label","放大字号");
      makeButton(font,"恢复 17 px",() => PL.ReaderUI.setFont(17),"font-reset");
      makeButton(primary,"识别 / 重试",() => PL.ReaderUI.process(reader,"ocr",state.recognitionSelect?.value),"ocr");
      const translate = () => PL.ReaderUI.process(reader,"translate");
      makeButton(primary,"翻译",translate,"translate");
      makeButton(primary,"逐段双语",() => PL.ReaderUI.translationMode(reader,"bilingual"),"bilingual");
      makeButton(primary,"仅译文",() => PL.ReaderUI.translationMode(reader,"translation"),"translation-only");
      makeButton(primary,"联动滚动",() => PL.ReaderUI.toggleScrollSync(),"sync-scroll");
      makeButton(exports,"导出 HTML",() => PL.Artifacts.exportHTML(reader._window,state),"export-html");
      makeButton(exports,"导出 Markdown",async () => {
        if (!state.saved) throw new Error("请先打开已保存的内容");
        await PL.Artifacts.exportMarkdown(reader._window,state.saved,state.kind);
      },"export");
      makeButton(exports,"导出全部表格",async () => {
        const global = state.frame?.browsingContext?.currentWindowGlobal;
        const items = global && await PL.ReaderUI.readerActor(global).sendQuery("table-data");
        if (!items?.length) throw new Error("当前文献没有可导出的表格");
        return PL.Artifacts.exportTables(reader._window,items);
      },"export-tables");
      makeButton(more,"查看解析原文",() => PL.ReaderUI.load(reader,"ocr"),"show-ocr");
      makeButton(more,"查看已保存译文",() => PL.ReaderUI.load(reader,"translate"),"show-translation");
      makeButton(more,"段落高亮",() => PL.ReaderUI.toggleCompare(reader),"block-compare");
      more.append(html("hr"));
      if (PL.config?.unified) {
        const scheme = html("select"); scheme.setAttribute("aria-label","当前论文的识别方案");
        scheme.title = "当前论文的识别方案";
        PL.Recognition.populate(scheme);
        scheme.value = PL.Recognition.defaultMode(); state.recognitionSelect = scheme; more.append(scheme);
        makeButton(more,"重新识别",() => PL.ReaderUI.process(reader,"force-ocr",scheme.value),"force-ocr");
      }
      makeButton(more,"更新译文",translate,"update-translation");
      makeButton(more,"编辑解析原文",() => PL.Editing.open(reader),"edit-ocr");
      makeButton(more,"插件设置…",() => PL.UI.settings(reader._window),"settings");
      makeButton(more,"清除解析结果…",() => PL.Tasks.clearOCR(Zotero.Items.get(reader.itemID)),"clear-ocr");
      makeButton(more,"删除当前译文…",() => PL.Tasks.clearTranslation(Zotero.Items.get(reader.itemID)),"clear-translation");
      shell.addEventListener("keydown",event => {
        if (event.key === "Escape" && openPanel) { event.preventDefault(); event.stopPropagation(); closeMenus(true); }
      });
      shell.addEventListener("focusout",event => {
        if (event.relatedTarget && !shell.contains(event.relatedTarget)) closeMenus();
      });
      state.toolbarDismiss = event => { if (!shell.contains(event.target)) closeMenus(); };
      doc.addEventListener("pointerdown",state.toolbarDismiss,true);
      nativeDoc.addEventListener("pointerdown",state.toolbarDismiss,true);
      state.closeMenus = closeMenus;
      const content = html("div"); content.className = "paperloom-pane-content";
      const message = html("div"); message.className = "paperloom-pane-message"; message.setAttribute("role","status");
      content.append(message); pane.append(shell,heading,content);

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
      Object.assign(state, { pane, placeholder, container, oldPosition, divider, heading, content, message, fontSize });
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
      const active = PL.Tasks.forSource(source);
      const legacy = !!state.saved && state.exportSchema != null && state.exportSchema < 5;
      state.heading.textContent = active ? active.message || (active.kind === "ocr" ? "正在解析…" : "正在翻译…") : "";
      if (!active && legacy) state.heading.textContent = state.kind === "ocr" ? "旧版 解析：重新解析 后可使用段落对照" : "旧版译文，可删除后重新翻译";
      if (!active && !legacy && state.saved?.needsReview) state.heading.textContent = "部分识别内容待核对";
      if (!active && state.saved && state.mode === "split" && state.usingOCRFallback)
        state.heading.textContent = "尚无译文，当前显示解析原文";
      if (!active && state.kind === "translate" && PL.Editing.stale(record)) state.heading.textContent = "解析原文已更新，可在「更多」中更新译文";
      if (state.recognitionSelect) {
        state.recognitionSelect.hidden = state.kind === "translate";
        state.recognitionSelect.disabled = !!active;
      }
      state.heading.hidden=!state.heading.textContent;
      for (const button of state.pane.querySelectorAll("button[data-action]")) {
        const action = button.dataset.action;
        if (["bilingual", "translation-only", "clear-translation"].includes(action)) {
          button.hidden = state.kind !== "translate" && action !== "clear-translation";
          if (action === "clear-translation") button.hidden = !record?.translations?.[PL.language()]?.attachmentID;
          if (action === "bilingual" || action === "translation-only") button.setAttribute("aria-pressed", String(PL.ReaderPresentation.mode(state) === (action === "bilingual" ? "bilingual" : "translation")));
        }
        if (["ocr", "force-ocr", "clear-ocr", "translate"].includes(action)) button.hidden = state.kind === "translate" && (action !== "translate" || !!state.saved);
        if (action === "update-translation") button.hidden = state.kind !== "translate" || !state.saved;
        if (action === "edit-ocr") { button.hidden = state.kind !== "ocr"; button.title = "编辑 Markdown、调整类型、插入标题或删除图片；保存为新版本"; }
        if (action === "show-ocr" || action === "show-translation") button.setAttribute("aria-pressed", String(state.kind === (action === "show-ocr" ? "ocr" : "translate")));
        if (action === "block-compare") {
          button.textContent = "段落高亮";
          button.setAttribute("aria-pressed", String(!legacy && PL.pref("blockCompare", true)));
        }
        if (action === "sync-scroll") {
          button.hidden = state.mode !== "split";
          button.setAttribute("aria-pressed",String(PL.pref("syncScroll",false)));
          button.title="在对照模式下同步 PDF 和 解析／译文的位置，开关会自动记住";
        }
        button.disabled = (["ocr", "force-ocr"].includes(action) && active)
          || (["translate", "update-translation"].includes(action) && (active?.kind === "translate" ||
              PL.Tasks.queue.some(task => task.kind === "translate" && task.source.id === source.id)))
          || (legacy && ["bilingual", "translation-only", "block-compare"].includes(action))
          || (action === "show-translation" && !record?.translations?.[PL.language()]?.attachmentID)
          || (action === "show-ocr" && !record?.ocr?.attachmentID)
          || (["export", "export-html", "export-tables", "export-menu"].includes(action) && !state.saved)
          || (action === "sync-scroll" && (state.mode !== "split" || !state.saved || legacy));
        if (action === "edit-ocr") button.disabled = active || !state.saved;
        if (["bilingual", "translation-only", "block-compare"].includes(action)) button.title = legacy
          ? (state.kind === "ocr" ? "旧版解析不含段落坐标，请点击「解析 / 重试」生成新结果" : "旧版译文不支持此功能，请删除译文后基于当前解析重新翻译")
          : "切换显示与 PDF 段落对照，不重新识别或翻译";
        if (action === "block-compare" && !legacy) button.title = "仅切换区域高亮与背景淡化；双向段落定位始终可用";
      }
      if (!state.saved && !active) {
        const status = state.kind === "ocr" ? record?.ocr : record?.translations?.[PL.language()];
        if (status?.state === "failed") state.message.textContent = `处理失败：${status.message}\n可点击上方按钮重试，已完成的缓存会保留。`;
        if (state.mode !== "original" && status?.state === "ready" && !state.pendingLoad) {
          PL.ReaderUI.load(reader, state.kind).catch(error => Zotero.logError(error));
        }
      }
    },
  };
})();
