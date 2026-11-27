"use strict";
(() => {
  const PL = PaperLoom;
  const XHTML = "http://www.w3.org/1999/xhtml";
  PL.UI = {
    registerColumns() {
      this.columns = [
        ["ocrStatus", "OCR 状态", "100", item => PL.Store.label(item, "ocr")],
        ["translationStatus", "翻译状态", "100", item => PL.Store.label(item, "translate")],
        ["translatedTitle", "翻译标题", "260", item => PL.TitleTranslations.column(item)],
      ].map(([dataKey, label, width, dataProvider]) => Zotero.ItemTreeManager.registerColumn({
        dataKey, label, pluginID: PL.id,
        // Zotero's item tree still uses defaultIn for initial visibility even
        // though the registration API marks it deprecated. Saved user choices win.
        enabledTreeIDs: ["main"], defaultIn: ["*"], showInColumnPicker: true, columnPickerSubMenu: false,
        width, flex: 0, minWidth: 75, zoteroPersist: ["width", "hidden", "sortDirection"], dataProvider,
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
        menuitem(popup, "OCR 当前论文", () => PL.Tasks.selected(window, "ocr"));
        menuitem(popup, "翻译当前论文（含标题）", () => PL.Tasks.selected(window, "translate"));
        menuitem(popup, "OCR 并翻译当前论文（含标题）", () => PL.Tasks.selected(window, "ocr-translate"));
        menuitem(popup, "仅翻译当前标题", () => PL.Tasks.selected(window, "translate-title"));
        popup.append(doc.createXULElement("menuseparator"));
        menuitem(popup, "清除 OCR 结果（保留 PDF 和译文）", () => PL.Tasks.selected(window, "clear-ocr"));
        menuitem(popup, "删除当前语言译文（保留 PDF 和 OCR）", () => PL.Tasks.selected(window, "clear-translation"));
        menuitem(popup, "查看 OCR 版本", () => PL.Tasks.selected(window, "open-ocr"));
        menuitem(popup, "查看已保存译文", () => PL.Tasks.selected(window, "open-translation"));
        if (options) {
          popup.append(doc.createXULElement("menuseparator"));
          menuitem(popup, "批量 OCR…", () => PL.BatchPicker.open(window, "ocr"));
          menuitem(popup, "批量翻译…", () => PL.BatchPicker.open(window, "translate"));
          const automatic = menuitem(popup, "新文献添加后自动 OCR", () => {
            PL.setPref(PL.Tasks.autoPref(), !PL.pref(PL.Tasks.autoPref(), false));
            automatic.setAttribute("checked", String(PL.pref(PL.Tasks.autoPref(), false)));
          }, { type: "checkbox", checked: String(PL.pref(PL.Tasks.autoPref(), false)), autocheck: "false" });
          popup.addEventListener("popupshowing", () => {
            automatic.setAttribute("checked", String(PL.pref(PL.Tasks.autoPref(), false)));
            const target = PL.config.unified ? PL.Recognition.destination(PL.Recognition.defaultMode())
              : PL.Tasks.autoPref() === "cloudAutoOCR" ? "飞桨云端" : null;
            automatic.setAttribute("label", target ? `新文献自动提交到${target}识别` : "新文献添加后自动识别");
          });
          if (PL.config.unified) {
            const localControl = menuitem(popup, "预热本地 OCR", () => PL.LocalOCRControl.toggle(window));
            localControl.id = "paperloom-local-ocr-control";
            localControl.hidden = PL.Recognition.defaultMode() !== "local";
            popup.addEventListener("popupshowing", () => {
              localControl.hidden = PL.Recognition.defaultMode() !== "local";
              if (localControl.hidden) return;
              const [label, hint, disabled] = PL.LocalOCRControl.status();
              localControl.setAttribute("label", label);
              localControl.setAttribute("tooltiptext", hint);
              localControl.disabled = !!disabled;
            });
          } else {
            menuitem(popup, "启动识别服务", () => PL.Service.ensure());
            menuitem(popup, "停止插件启动的服务", () => PL.Service.stop());
          }
          menuitem(popup, "设置…", () => this.settings(window));
          if (PL.config.semanticSearch) menuitem(popup, "按内容找论文…", () => PL.SemanticSearch.open(window));
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
        const actions = [["ocr",PL.config.unified ? "批量识别" : "批量 OCR"],["translate","批量翻译"]];
        if (PL.config.semanticSearch) actions.unshift(["semantic", "按内容找论文"]);
        for (const [kind,label] of actions) {
          const button = doc.createElementNS(XHTML,"button");
          button.id = `paperloom-batch-${kind}`; button.textContent = label;
          button.title = kind === "semantic" ? "搜索论文标题、摘要和已保存的识别段落" : "打开论文列表，勾选要处理的论文后再开始；不会立即执行任务";
          button.addEventListener("click", PL.run(window, () =>
            kind === "semantic" ? PL.SemanticSearch.open(window) : PL.BatchPicker.open(window, kind)));
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
        button.addEventListener("click", () => (PL.config.unified ? PL.LocalOCRControl.toggle(window) :
          this.recognitionService().ensure()).catch(error => Zotero.logError(error)));
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
      const unified = PL.config?.unified;
      const state = (unified ? PL.LocalOCR : this.recognitionService()).state;
      const loading = ["checking", "checking_ocr", "starting_ocr", "loading_model", "starting_api", "stopping"].includes(state.phase)
        || (unified && !!PL.LocalOCRControl?.warming);
      for (const window of PL.windows.keys()) {
        const doc = window.document;
        const button = doc.getElementById("paperloom-start-service");
        const localMenu = doc.getElementById("paperloom-local-ocr-control");
        if (localMenu) localMenu.hidden = PL.Recognition.defaultMode() !== "local";
        const progress = doc.getElementById("paperloom-startup-progress");
        const label = doc.getElementById("paperloom-startup-label");
        if (!button) continue;
        button.hidden = !!unified && PL.Recognition.defaultMode() !== "local";
        if (button.hidden) {
          label.hidden = true;
          progress.hidden = true;
          continue;
        }
        const [text, hint, disabled] = unified ? PL.LocalOCRControl.status() :
          [state.phase === "ready" ? "OCR已就绪" : state.phase === "failed" ? "重试启动" : "启动OCR", state.message, loading];
        button.textContent = text;
        button.disabled = !!disabled;
        button.title = hint;
        button.dataset.phase = state.phase;
        progress.hidden = !loading;
        // Loading a model has no honest percentage; indeterminate progress is
        // deliberate and the label reports the actual stage from the launcher.
        progress.removeAttribute("value");
        label.hidden = !loading && state.phase !== "failed";
        label.textContent = hint;
        label.title = hint;
      }
    },
    refreshTasks() {
      const task = PL.Tasks.active;
      const running = PL.Tasks.running || (task ? [task] : []);
      const batch = task ? task.batch : [...PL.Tasks.batches.values()].find(batch=>!batch.finished) || PL.Tasks.lastBatch;
      const taskLabel = kind => ({ocr: "OCR", title: "标题翻译", translate: "翻译"})[kind];
      for (const window of PL.windows.keys()) {
        const node = window.document.getElementById("paperloom-task-status");
        if (!node) continue;
        node.hidden = !task && !PL.Tasks.queue.length && !batch;
        const progress = batch ? `批量 ${taskLabel(batch.kind)} ${batch.done}/${batch.total}` : "";
        node.textContent = task ? `${progress || taskLabel(task.kind)} · ${task.message || "处理中…"}${PL.Tasks.queue.length ? ` · 等待 ${PL.Tasks.queue.length} 篇` : ""}`
          : batch ? `${progress} · ${batch.finished ? "完成" : "等待处理"} · 成功 ${batch.success}${batch.failed ? ` · 失败 ${batch.failed}` : ""}${batch.skipped ? ` · 跳过 ${batch.skipped}` : ""}` : "";
        if (running.length > 1) node.textContent = running.map(task => {
          const batch = task.batch;
          const label = taskLabel(task.kind);
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
      if ("localOCRIdleMinutes" in values && (!Number.isInteger(values.localOCRIdleMinutes) ||
          values.localOCRIdleMinutes < 1 || values.localOCRIdleMinutes > 120)) throw new Error("空闲释放时间请填写 1 至 120 的整数分钟");
      if (PL.config.cloudPreview || (PL.config.localHTMLPreview && PL.config.localRuntimeDescriptor)) {
        values = Object.fromEntries(Object.entries(values).filter(([key]) => !["projectRoot","pythonPath","backend"].includes(key)));
      }
      const engine = { serviceURL: PL.serviceURL(), projectRoot: PL.pref("projectRoot", PL.config.projectRoot),
        pythonPath: PL.pref("pythonPath", ""), backend: PL.pref("backend", PL.config.defaultBackend) };
      const restart = Object.keys(engine).some(key => key in values && values[key] !== engine[key]);
      const localDefaults = {unifiedLocalEngine:"auto", unifiedLocalBackend:"local",
        unifiedLocalPython:"", unifiedLocalRoot:"", unifiedLocalURL:"http://127.0.0.1:8765"};
      const restartLocal = PL.config.unified && Object.entries(localDefaults)
        .some(([key, fallback]) => key in values && values[key] !== PL.pref(key, fallback));
      const leaveLocal = PL.config.unified && PL.Recognition.defaultMode() === "local" &&
        "recognitionMode" in values && values.recognitionMode !== "local";
      const previous = Object.fromEntries(Object.keys(values).map(key => [key, PL.pref(key, undefined)]));
      try {
        for (const [key, value] of Object.entries(values)) PL.setPref(key, value);
        PL.serviceURL();
        PL.language();
        if (PL.config.unified) PL.LocalOCR.configuredURL();
        if (restartLocal || leaveLocal) {
          if (PL.LocalOCR.instance || PL.LocalOCR.readyURL || PL.LocalOCR.starting || PL.LocalOCRControl?.warming)
            await PL.LocalOCRControl.stop();
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
      if ("localOCRIdleMinutes" in values && previous.localOCRIdleMinutes !== values.localOCRIdleMinutes &&
          PL.LocalOCRControl?.deadline) PL.LocalOCRControl.schedule();
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
