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
        menuitem(popup, "OCR 选中文献…（打开论文列表勾选）", () => PL.BatchPicker.open(window, "ocr"));
        menuitem(popup, "强制重新 OCR…（打开论文列表勾选）", () => PL.BatchPicker.open(window, "force-ocr"));
        menuitem(popup, "清除 OCR 结果（保留 PDF 和译文）", () => PL.Tasks.selected(window, "clear-ocr"));
        menuitem(popup, "翻译选中文献…（打开论文列表勾选）", () => PL.BatchPicker.open(window, "translate"));
        menuitem(popup, "删除当前语言译文（保留 PDF 和 OCR）", () => PL.Tasks.selected(window, "clear-translation"));
        menuitem(popup, "查看 OCR 版本", () => PL.Tasks.selected(window, "open-ocr"));
        menuitem(popup, "查看已保存译文", () => PL.Tasks.selected(window, "open-translation"));
        if (options) {
          popup.append(doc.createXULElement("menuseparator"));
          const automatic = menuitem(popup, "新文献添加后自动 OCR", () => {
            PL.setPref("autoOCR", !PL.pref("autoOCR", false));
            automatic.setAttribute("checked", String(PL.pref("autoOCR", false)));
          }, { type: "checkbox", checked: String(PL.pref("autoOCR", false)), autocheck: "false" });
          popup.addEventListener("popupshowing", () => automatic.setAttribute("checked", String(PL.pref("autoOCR", false))));
          menuitem(popup, "启动 OCR 服务", () => PL.Service.ensure());
          menuitem(popup, "停止 OCR 服务", () => PL.Service.stop());
          menuitem(popup, "设置…", () => this.settings(window));
        }
        parent.append(root);
        nodes.push(root);
      };
      menu(doc.getElementById("zotero-itemmenu"), "paperloom-item-menu", false);
      menu(doc.getElementById("menu_ToolsPopup"), "paperloom-tools-menu", true);
      const itemsToolbar = doc.getElementById("zotero-items-toolbar");
      if (itemsToolbar) {
        const batchGroup = doc.createElementNS(XHTML, "div");
        batchGroup.id = "paperloom-batch-toolbar";
        batchGroup.style.cssText = "display:flex;gap:4px;margin-inline:6px;align-items:center";
        for (const [kind,label] of [["ocr","批量 OCR"],["translate","批量翻译"]]) {
          const button = doc.createElementNS(XHTML,"button");
          button.id = `paperloom-batch-${kind}`; button.textContent = label;
          button.title = "打开论文列表，勾选要处理的论文后再开始；不会立即执行任务";
          button.style.cssText = "font:12px system-ui;white-space:nowrap;border:1px solid #8885;border-radius:4px;padding:4px 7px;background:transparent;color:inherit;cursor:pointer";
          button.addEventListener("click", PL.run(window,()=>PL.BatchPicker.open(window,kind)));
          batchGroup.append(button);
        }
        itemsToolbar.append(batchGroup); nodes.push(batchGroup);
      }
      const host = doc.getElementById("zotero-tabs-toolbar") || doc.getElementById("zotero-items-toolbar");
      if (host) {
        const group = doc.createElementNS(XHTML, "div");
        group.id = "paperloom-service-toolbar";
        group.style.cssText = "display:flex;align-items:center;gap:7px;margin-inline:8px;font:12px system-ui;min-width:0";
        const button = doc.createElementNS(XHTML, "button");
        button.id = "paperloom-start-service";
        button.style.cssText = "border:1px solid #8885;border-radius:4px;padding:4px 8px;font:inherit;color:inherit;background:transparent;white-space:nowrap;cursor:pointer";
        // Startup errors are shown beside this button; a modal would block the
        // repaint that hides the progress bar and obscure the retry action.
        button.addEventListener("click", () => PL.Service.ensure().catch(error => Zotero.logError(error)));
        const label = doc.createElementNS(XHTML, "span");
        label.id = "paperloom-startup-label";
        label.style.cssText = "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
        label.setAttribute("aria-live", "polite");
        const progress = doc.createElementNS(XHTML, "progress");
        progress.id = "paperloom-startup-progress";
        progress.style.cssText = "width:105px;height:7px;accent-color:#3875d7";
        progress.setAttribute("aria-label", "OCR 服务启动进度");
        const tasks = doc.createElementNS(XHTML, "span");
        tasks.id = "paperloom-task-status";
        tasks.style.cssText = "max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
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
    refreshService() {
      const state = PL.Service.state;
      const loading = ["checking", "checking_ocr", "starting_ocr", "loading_model", "starting_api", "stopping"].includes(state.phase);
      for (const window of PL.windows.keys()) {
        const doc = window.document;
        const button = doc.getElementById("paperloom-start-service");
        const progress = doc.getElementById("paperloom-startup-progress");
        const label = doc.getElementById("paperloom-startup-label");
        if (!button) continue;
        button.textContent = state.phase === "ready" ? "OCR 已就绪" : state.phase === "failed" ? "重试启动" : "启动 OCR";
        button.disabled = loading;
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
        "chrome,dialog=no,resizable,centerscreen,width=660,height=720", argument);
      return this.settingsWindow;
    },
    async applyLocal(values) {
      if (PL.Tasks.active || PL.Tasks.queue.length) throw new Error("请等当前文献处理完成后再修改设置");
      const engine = { serviceURL: PL.serviceURL(), projectRoot: PL.pref("projectRoot", PL.config.projectRoot),
        pythonPath: PL.pref("pythonPath", ""), backend: PL.pref("backend", PL.config.defaultBackend) };
      const restart = Object.keys(engine).some(key => key in values && values[key] !== engine[key]);
      const previous = Object.fromEntries(Object.keys(values).map(key => [key, PL.pref(key, undefined)]));
      try {
        for (const [key, value] of Object.entries(values)) PL.setPref(key, value);
        PL.serviceURL();
        PL.language();
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
