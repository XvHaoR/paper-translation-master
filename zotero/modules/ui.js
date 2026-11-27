"use strict";
(() => {
  const PL = PaperLoom;
  const XHTML = "http://www.w3.org/1999/xhtml";
  PL.UI = {
    registerColumns() {
      this.columns = ["ocr", "translate"].map(kind => Zotero.ItemTreeManager.registerColumn({
        dataKey: kind === "ocr" ? "ocrStatus" : "translationStatus",
        label: kind === "ocr" ? "OCR 状态" : "翻译状态", pluginID: PL.id,
        // Zotero's item tree still uses defaultIn for initial visibility even
        // though the registration API marks it deprecated. Saved user choices win.
        enabledTreeIDs: ["main"], defaultIn: ["*"], showInColumnPicker: true, columnPickerSubMenu: false,
        width: "100", flex: 0, minWidth: 75, zoteroPersist: ["width", "hidden", "sortDirection"],
        dataProvider: item => PL.Store.label(item, kind),
      }));
    },
    addWindow(window) {
      if (PL.windows.has(window) || !PL.alive) return;
      if (window.document.readyState !== "complete") {
        window.addEventListener("load", () => { if (PL.alive) this.addWindow(window); }, { once: true });
        return;
      }
      const doc = window.document;
      const nodes = [];
      const menuitem = (parent, label, action, extra = {}) => {
        const item = doc.createXULElement("menuitem");
        item.setAttribute("label", label);
        for (const [key, value] of Object.entries(extra)) item.setAttribute(key, value);
        item.addEventListener("command", PL.run(window, action));
        parent.append(item);
        return item;
      };
      const menu = (parent, id, options) => {
        if (!parent) return;
        const root = doc.createXULElement("menu");
        root.id = id;
        root.setAttribute("label", PL.name || "论文翻译大师");
        const popup = doc.createXULElement("menupopup");
        root.append(popup);
        menuitem(popup, PL.config.unified ? "识别选中文献…" : "OCR 选中文献…（打开论文列表勾选）", () => PL.BatchPicker.open(window, "ocr"));
        menuitem(popup, "强制重新识别…", () => PL.BatchPicker.open(window, "force-ocr"));
        if (PL.config.unified && !options) {
          const actions = doc.createXULElement("menu");
          actions.setAttribute("label", "使用指定方案识别");
          const choices = doc.createXULElement("menupopup");
          for (const [mode, label] of Object.entries(PL.Recognition.labels))
            menuitem(choices, label, () => PL.Tasks.selected(window, "ocr", mode));
          actions.append(choices);
          popup.append(actions);
        }
        menuitem(popup, "清除 OCR 结果（保留 PDF 和译文）", () => PL.Tasks.selected(window, "clear-ocr"));
        menuitem(popup, "翻译选中文献…（打开论文列表勾选）", () => PL.BatchPicker.open(window, "translate"));
        menuitem(popup, "删除当前语言译文（保留 PDF 和 OCR）", () => PL.Tasks.selected(window, "clear-translation"));
        menuitem(popup, "查看 OCR 版本", () => PL.Tasks.selected(window, "open-ocr"));
        menuitem(popup, "查看已保存译文", () => PL.Tasks.selected(window, "open-translation"));
        if (options) {
          popup.append(doc.createXULElement("menuseparator"));
          const automatic = menuitem(popup, "新文献添加后自动 OCR", () => {
            PL.setPref(PL.Tasks.autoPref(), !PL.pref(PL.Tasks.autoPref(), false));
            automatic.setAttribute("checked", String(PL.pref(PL.Tasks.autoPref(), false)));
          }, { type: "checkbox", checked: String(PL.pref(PL.Tasks.autoPref(), false)), autocheck: "false" });
          popup.addEventListener("popupshowing", () => {
            automatic.setAttribute("checked", String(PL.pref(PL.Tasks.autoPref(), false)));
            const cloud = ["cloudAutoOCR", "unifiedAutoCloud"].includes(PL.Tasks.autoPref());
            automatic.setAttribute("label", cloud ? "新文献自动上传飞桨进行 OCR" : "新文献添加后自动识别");
          });
          menuitem(popup, "启动识别服务", () => PL.Recognition.service(PL.config.unified ? PL.Recognition.defaultMode() : null).ensure());
          menuitem(popup, "停止插件启动的服务", async () => {
            if (PL.Tasks.pending.size) throw new Error("请等待正在处理的文献完成");
            for (const service of [PL.Service, PL.LocalOCR]) if (service.instance) await service.stop();
          });
          menuitem(popup, "设置…", () => this.settings(window));
        }
        parent.append(root);
        nodes.push(root);
      };
      const style = doc.createElementNS(XHTML, "style");
      style.id = "paperloom-window-style";
      style.textContent = PL.chromeStyle;
      doc.documentElement.append(style); nodes.push(style);
      menu(doc.getElementById("zotero-itemmenu"), "paperloom-item-menu", false);
      menu(doc.getElementById("menu_ToolsPopup"), "paperloom-tools-menu", true);
      const itemsToolbar = doc.getElementById("zotero-items-toolbar");
      if (itemsToolbar) {
        const batchGroup = doc.createElementNS(XHTML, "div");
        batchGroup.id = "paperloom-batch-toolbar";
        for (const [kind,label] of [["ocr",PL.config.unified ? "批量识别" : "批量 OCR"],["translate","批量翻译"]]) {
          const button = doc.createElementNS(XHTML,"button");
          button.id = `paperloom-batch-${kind}`; button.textContent = label;
          button.title = "打开论文列表，勾选要处理的论文后再开始；不会立即执行任务";
          button.addEventListener("click", PL.run(window,()=>PL.BatchPicker.open(window,kind)));
          batchGroup.append(button);
        }
        itemsToolbar.append(batchGroup); nodes.push(batchGroup);
      }
      const host = doc.getElementById("zotero-tabs-toolbar") || doc.getElementById("zotero-items-toolbar");
      if (host) {
        const group = doc.createElementNS(XHTML, "div");
        group.id = "paperloom-service-toolbar";
        const button = doc.createElementNS(XHTML, "button");
        button.id = "paperloom-start-service";
        // Startup errors are shown beside this button; a modal would block the
        // repaint that hides the progress bar and obscure the retry action.
        button.addEventListener("click", () => this.recognitionService().ensure().catch(error => Zotero.logError(error)));
        const label = doc.createElementNS(XHTML, "span");
        label.id = "paperloom-startup-label";
        label.setAttribute("aria-live", "polite");
        const progress = doc.createElementNS(XHTML, "progress");
        progress.id = "paperloom-startup-progress";
        progress.setAttribute("aria-label", "OCR 服务启动进度");
        const tasks = doc.createElementNS(XHTML, "span");
        tasks.id = "paperloom-task-status";
        group.append(button, label, progress, tasks);
        const anchor = doc.getElementById("zotero-tb-sync-error");
        if (anchor?.parentElement === host) host.insertBefore(group, anchor);
        else host.append(group);
        nodes.push(group);
      }
      PL.windows.set(window, nodes);
      this.refreshService();
      this.refreshTasks();
    },
    recognitionService() {
      return PL.config?.unified ? PL.Recognition.service(PL.Recognition.defaultMode()) : PL.Service;
    },
    refreshService() {
      const state = this.recognitionService().state;
      const loading = ["checking", "checking_ocr", "starting_ocr", "loading_model", "starting_api", "stopping"].includes(state.phase);
      for (const window of PL.windows.keys()) {
        const doc = window.document;
        const button = doc.getElementById("paperloom-start-service");
        const progress = doc.getElementById("paperloom-startup-progress");
        const label = doc.getElementById("paperloom-startup-label");
        if (!button) continue;
        const labelText = PL.config?.unified ? PL.Recognition.label(PL.Recognition.defaultMode()) : "OCR";
        button.textContent = state.phase === "ready" ? `${labelText}已就绪` : state.phase === "failed" ? "重试启动" : `启动${labelText}`;
        button.disabled = loading;
        button.dataset.phase = state.phase;
        button.title = state.message;
        progress.hidden = !loading;
        // Loading a model has no honest percentage; indeterminate progress is
        // deliberate and the label reports the actual stage from the launcher.
        progress.removeAttribute("value");
        label.hidden = !loading && state.phase !== "failed";
        label.textContent = state.message;
        label.title = state.message;
      }
    },
    refreshTasks() {
      const task = PL.Tasks.active;
      const running = PL.Tasks.running || (task ? [task] : []);
      const batch = task ? task.batch : [...PL.Tasks.batches.values()].find(batch=>!batch.finished) || PL.Tasks.lastBatch;
      for (const window of PL.windows.keys()) {
        const node = window.document.getElementById("paperloom-task-status");
        if (!node) continue;
        node.hidden = !task && !PL.Tasks.queue.length && !batch;
        const progress = batch ? `批量 ${batch.kind === "ocr" ? "OCR" : "翻译"} ${batch.done}/${batch.total}` : "";
        node.textContent = task ? `${progress || (task.kind === "ocr" ? "OCR" : "翻译")} · ${task.message || "处理中…"}${PL.Tasks.queue.length ? ` · 等待 ${PL.Tasks.queue.length} 篇` : ""}`
          : batch ? `${progress} · ${batch.finished ? "完成" : "等待处理"} · 成功 ${batch.success}${batch.failed ? ` · 失败 ${batch.failed}` : ""}${batch.skipped ? ` · 跳过 ${batch.skipped}` : ""}` : "";
        if (running.length > 1) node.textContent = running.map(task => {
          const batch = task.batch;
          const label = task.kind === "ocr" ? "OCR" : "翻译";
          return `${batch ? `批量 ${label} ${batch.done}/${batch.total}` : label} · ${task.message || "处理中…"}`;
        }).join(" ｜ ") + (PL.Tasks.queue.length ? ` · 等待 ${PL.Tasks.queue.length} 篇` : "");
        node.title = [node.textContent,...running.map(task => task.source.getField("title"))].filter(Boolean).join("\n");
      }
      PL.ReaderUI.refresh();
    },
    settings(window) {
      if (this.settingsWindow && !this.settingsWindow.closed) { this.settingsWindow.focus(); return this.settingsWindow; }
      const argument = { controller: PL };
      argument.wrappedJSObject = argument;
      this.settingsWindow = window.openDialog("chrome://paperloom/content/settings.xhtml", "paperloom-settings",
        "chrome,dialog=no,resizable,centerscreen,width=740,height=660", argument);
      return this.settingsWindow;
    },
    async applyLocal(values) {
      if (PL.Tasks.active || PL.Tasks.queue.length) throw new Error("请等当前文献处理完成后再修改设置");
      if (PL.config.cloudPreview || (PL.config.localHTMLPreview && PL.config.localRuntimeDescriptor)) {
        values = Object.fromEntries(Object.entries(values).filter(([key]) => !["projectRoot","pythonPath","backend"].includes(key)));
      }
      const engine = { serviceURL: PL.serviceURL(), projectRoot: PL.pref("projectRoot", PL.config.projectRoot),
        pythonPath: PL.pref("pythonPath", ""), backend: PL.pref("backend", PL.config.defaultBackend) };
      const restart = Object.keys(engine).some(key => key in values && values[key] !== engine[key]);
      const restartLocal = PL.config.unified && Object.keys(values)
        .some(key => key.startsWith("unifiedLocal") && values[key] !== PL.pref(key, undefined));
      const previous = Object.fromEntries(Object.keys(values).map(key => [key, PL.pref(key, undefined)]));
      try {
        for (const [key, value] of Object.entries(values)) PL.setPref(key, value);
        PL.serviceURL();
        PL.language();
        if (PL.config.unified) PL.LocalOCR.configuredURL();
        if (restartLocal) {
          if (PL.LocalOCR.starting) await PL.LocalOCR.starting.catch(() => {});
          if (PL.LocalOCR.instance) await PL.LocalOCR.stop();
          PL.LocalOCR.readyURL = null;
          PL.LocalOCR.runtime = null;
        }
        if (restart && PL.Service.instance) await PL.Service.stop();
      }
      catch (error) {
        for (const [key, value] of Object.entries(previous)) {
          if (value === undefined) Zotero.Prefs.clear(`extensions.paperloom.${key}`, true);
          else PL.setPref(key, value);
        }
        throw error;
      }
      PL.Store.refresh();
      this.refreshService();
    },
    removeWindow(window) {
      PL.BatchPicker?.close(window);
      for (const node of PL.windows.get(window) || []) node.remove();
      PL.windows.delete(window);
    },
    shutdown() {
      PL.BatchPicker?.shutdown();
      for (const window of [...PL.windows.keys()]) this.removeWindow(window);
      for (const column of this.columns || []) Zotero.ItemTreeManager.unregisterColumn(column);
      if (this.settingsWindow && !this.settingsWindow.closed) this.settingsWindow.close();
    },
  };
})();
