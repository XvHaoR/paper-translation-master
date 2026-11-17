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
      const chromeStyle = html("style");
      chromeStyle.textContent = PL.ReaderLayout.css;
      doc.documentElement.append(chromeStyle);
      const pane = html("section");
      pane.className = "paperloom-ocr-pane";
      pane.style.cssText = "position:absolute;z-index:5;box-sizing:border-box;overflow:hidden";
      pane.setAttribute("aria-label", "论文翻译大师 解析 阅读窗口");
      const toolbar = html("div");
      toolbar.className = "paperloom-pane-toolbar";
      const heading = html("div");
      heading.className = "paperloom-pane-status"; heading.hidden = true;
      heading.setAttribute("role","status");
      const toolbarButtons=[];
      const button = (label, action, name) => {
        const node = html("button");
        node.textContent = label;
        node.dataset.action = name;
        node.addEventListener("click", PL.run(reader._window, action));
        toolbar.append(node);
        toolbarButtons.push(node);
        return node;
      };
      if (PL.config?.unified) {
        const scheme = html("select");
        scheme.setAttribute("aria-label", "识别方案");
        scheme.title = "为当前论文选择识别方案";
        for (const [mode, label] of Object.entries(PL.Recognition.labels)) {
          const option = html("option");
          option.value = mode;
          option.textContent = label;
          scheme.append(option);
        }
        scheme.value = PL.Recognition.defaultMode();
        state.recognitionSelect = scheme;
        toolbar.append(scheme);
      }
      button("识别 / 重试", () => PL.ReaderUI.process(reader, "ocr", state.recognitionSelect?.value), "ocr");
      if (PL.config?.unified)
        button("重新识别", () => PL.ReaderUI.process(reader, "force-ocr", state.recognitionSelect?.value), "force-ocr");
      button("清除解析", () => PL.Tasks.clearOCR(Zotero.Items.get(reader.itemID)), "clear-ocr");
      button("删除译文", () => PL.Tasks.clearTranslation(Zotero.Items.get(reader.itemID)), "clear-translation");
      button("翻译", () => PL.ReaderUI.process(reader, "translate"), "translate");
      button("看解析", () => PL.ReaderUI.load(reader, "ocr"), "show-ocr");
      button("看译文", () => PL.ReaderUI.load(reader, "translate"), "show-translation");
      button("编辑解析", () => PL.Editing.open(reader), "edit-ocr");
      button("逐段双语", () => PL.ReaderUI.translationMode("bilingual"), "bilingual");
      button("仅译文", () => PL.ReaderUI.translationMode("translation"), "translation-only");
      button("段落对照", () => PL.ReaderUI.toggleCompare(reader), "block-compare");
      button("联动滚动：关", () => PL.ReaderUI.toggleScrollSync(), "sync-scroll");
      const exportToggle=button("导出 ▾",()=>{
        overflow.hidden=true; more.setAttribute("aria-expanded","false");
        exportMenu.hidden=!exportMenu.hidden;
        exportMenu.style.top=`${toolbar.offsetHeight}px`;
        exportToggle.setAttribute("aria-expanded",String(!exportMenu.hidden));
      },"export-menu");
      exportToggle.setAttribute("aria-haspopup","true"); exportToggle.setAttribute("aria-expanded","false");
      button("导出 HTML", () => PL.Artifacts.exportHTML(reader._window, state), "export-html");
      button("导出 Markdown", async () => {
        const saved = state.saved;
        if (!saved) throw new Error("请先保存当前版本");
        await PL.Artifacts.exportMarkdown(reader._window, saved, state.kind);
      }, "export");
      button("导出全部表格",async()=>{
        const global=state.frame?.browsingContext?.currentWindowGlobal;
        const items=global && await PL.ReaderUI.readerActor(global).sendQuery("table-data");
        if (!items?.length) throw new Error("当前文献没有可导出的表格");
        return PL.Artifacts.exportTables(reader._window,items);
      },"export-tables");
      button("设置", () => PL.UI.settings(reader._window), "settings");
      const fontGroup = html("span");
      fontGroup.className = "paperloom-font-controls";
      fontGroup.textContent = "字号";
      const smaller = html("button");
      smaller.textContent = "A−";
      smaller.title = "缩小 解析 / 译文字号";
      smaller.dataset.action = "font-smaller";
      smaller.addEventListener("click", PL.run(reader._window, () => PL.ReaderUI.setFont(PL.ReaderUI.fontSize() - 1)));
      const fontSize = html("input");
      fontSize.type = "number";
      fontSize.min = "12";
      fontSize.max = "40";
      fontSize.value = String(PL.ReaderUI.fontSize());
      fontSize.setAttribute("aria-label", "解析 与译文字号");
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
      larger.title = "放大 解析 / 译文字号";
      larger.dataset.action = "font-larger";
      larger.addEventListener("click", PL.run(reader._window, () => PL.ReaderUI.setFont(PL.ReaderUI.fontSize() + 1)));
      const reset = html("button");
      reset.textContent = "重置";
      reset.dataset.action = "font-reset";
      reset.addEventListener("click", PL.run(reader._window, () => PL.ReaderUI.setFont(17)));
      fontGroup.append(smaller, fontSize, larger, reset);
      toolbar.prepend(fontGroup);
      const more=html("button"); more.className="paperloom-toolbar-more"; more.textContent="更多 ▾";
      more.setAttribute("aria-haspopup","true"); more.setAttribute("aria-expanded","false");
      const overflow=html("div"); overflow.className="paperloom-toolbar-overflow"; overflow.hidden=true;
      const exportMenu=html("div"); exportMenu.className="paperloom-toolbar-overflow"; exportMenu.hidden=true;
      for(const action of ["export-html","export","export-tables"]){
        const index=toolbarButtons.findIndex(node=>node.dataset.action===action);
        exportMenu.append(toolbarButtons.splice(index,1)[0]);
      }
      const closeOverflow=()=>{
        overflow.hidden=true;more.setAttribute("aria-expanded","false");
        exportMenu.hidden=true;exportToggle.setAttribute("aria-expanded","false");
      };
      more.addEventListener("click",()=>{
        exportMenu.hidden=true;exportToggle.setAttribute("aria-expanded","false");
        overflow.hidden=!overflow.hidden; more.setAttribute("aria-expanded",String(!overflow.hidden));
        overflow.style.top=`${toolbar.offsetHeight}px`;
      });
      overflow.addEventListener("click",event=>{if(event.target.closest("button"))closeOverflow();});
      exportMenu.addEventListener("click",event=>{if(event.target.closest("button"))closeOverflow();});
      pane.addEventListener("keydown",event=>{if(event.key==="Escape"&&(!overflow.hidden||!exportMenu.hidden)){const focus=exportMenu.hidden?more:exportToggle;closeOverflow();focus.focus();event.stopPropagation();}});
      toolbar.append(more);
      state.toolbarDismiss=event=>{if(!toolbar.contains(event.target)&&!overflow.contains(event.target)&&!exportMenu.contains(event.target))closeOverflow();};
      doc.addEventListener("pointerdown",state.toolbarDismiss,true);
      const content = html("div");
      content.className = "paperloom-pane-content";
      const message = html("div");
      message.className = "paperloom-pane-message";
      content.append(heading,message);
      pane.append(toolbar,overflow,exportMenu,content);
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
      Object.assign(state,{toolbar,toolbarButtons,toolbarMore:more,toolbarOverflow:overflow});
      state.toolbarObserver=new doc.defaultView.ResizeObserver(()=>this.fitToolbar(state));
      state.toolbarObserver.observe(toolbar);
      const observer = new nativeDoc.defaultView.ResizeObserver(() => PL.ReaderUI.position(state));
      observer.observe(placeholder);
      state.resizeObserver = observer;
      state.positionListener = () => PL.ReaderUI.position(state);
      reader._window.addEventListener("resize", state.positionListener);
      PL.ReaderUI.position(state);
    },
    fitToolbar(state) {
      const {toolbar,toolbarButtons:buttons,toolbarMore:more,toolbarOverflow:overflow}=state;
      if (!toolbar?.clientWidth) return;
      const focused=toolbar.ownerDocument.activeElement;
      for(const button of buttons)toolbar.insertBefore(button,more);
      more.hidden=false;
      const order=["settings","clear-translation","clear-ocr","force-ocr","ocr","translate","edit-ocr","show-ocr","show-translation","block-compare","bilingual","translation-only"];
      for(const name of order) {
        if(toolbar.scrollWidth<=toolbar.clientWidth+1)break;
        const button=buttons.find(node=>node.dataset.action===name);
        if(button&&!button.hidden)overflow.append(button);
      }
      more.hidden=!overflow.children.length;
      if(more.hidden){overflow.hidden=true;more.setAttribute("aria-expanded","false");}
      if(focused?.isConnected&&toolbar.ownerDocument.activeElement!==focused&&focused.closest(".paperloom-ocr-pane"))focused.focus({preventScroll:true});
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
      if (!active && state.saved?.recognitionMode) state.heading.textContent = PL.Recognition.label(state.saved.recognitionMode)
        + (state.saved.needsReview ? " · 部分内容待核对" : "");
      if (!active && state.kind === "translate" && PL.Editing.stale(record)) state.heading.textContent += " · 已有更新的识别结果，点击翻译可更新";
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
          if (action === "bilingual" || action === "translation-only") button.setAttribute("aria-pressed", String(PL.pref("translationView", "bilingual") === (action === "bilingual" ? "bilingual" : "translation")));
        }
        if (["ocr", "force-ocr", "clear-ocr"].includes(action)) button.hidden = state.kind === "translate";
        if (action === "edit-ocr") { button.hidden = state.kind !== "ocr"; button.title = "编辑 Markdown、调整类型、插入标题或删除图片；保存为新版本"; }
        if (action === "show-ocr" || action === "show-translation") button.setAttribute("aria-pressed", String(state.kind === (action === "show-ocr" ? "ocr" : "translate")));
        if (action === "block-compare") {
          button.textContent = PL.pref("blockCompare", true) ? "关闭段落高亮" : "开启段落高亮";
          button.setAttribute("aria-pressed", String(!legacy && PL.pref("blockCompare", true)));
        }
        if (action === "sync-scroll") {
          button.textContent=PL.pref("syncScroll",false) ? "联动滚动：开" : "联动滚动：关";
          button.setAttribute("aria-pressed",String(PL.pref("syncScroll",false)));
          button.title="在对照模式下同步 PDF 和 解析／译文的位置，开关会自动记住";
        }
        button.disabled = (["ocr", "force-ocr", "translate"].includes(action) && active)
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
      this.fitToolbar(state);
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
