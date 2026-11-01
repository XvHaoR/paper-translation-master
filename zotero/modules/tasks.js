"use strict";
(() => {
  const PL = PaperLoom;
  PL.Tasks = {
    queue: [],
    pending: new Map(),
    lanes: new Map(),
    get running() { return [...this.lanes.values()]; },
    // Compatibility for callers that only need to know whether work is active.
    get active() { return this.running[0] || null; },
    forSource(source) { return this.running.find(task => task.source.id === source.id); },
    autoCandidates: new Map(),
    batches: new Map(),
    async sourceFor(item) {
      const key = PL.tags(item).find(tag => tag.startsWith("paperloom:pdf:"))?.slice(14);
      if (key) item = await Zotero.Items.getByLibraryAndKeyAsync(item.libraryID, key);
      if (!item) throw new Error("原 PDF 附件已删除，已保存结果仍可直接查看");
      if (item.deleted) throw new Error("这篇文献已删除，请刷新论文列表");
      if (item.isRegularItem()) {
        const best = await item.getBestAttachment();
        if (best?.attachmentContentType === "application/pdf") return best;
        const pdf = Zotero.Items.get(item.getAttachments()).find(a => a.attachmentContentType === "application/pdf");
        if (pdf) return pdf;
      }
      if (item.isAttachment() && item.attachmentContentType === "application/pdf") return item;
      throw new Error("请选择带 PDF 的文献，或直接选中 PDF 附件");
    },
    enqueue(source, kind, window = Zotero.getMainWindow(), { force = false, batch = null } = {}) {
      if (PL.Editing.jobs.has(PL.Store.key(source))) return Promise.reject(new Error("请等待 OCR 修改保存完成"));
      const language = PL.language();
      const key = `${PL.Store.key(source)}/${kind}/${language}/${force ? "force" : "process"}`;
      if (this.pending.has(key)) return this.pending.get(key);
      const promise = new Promise((resolve, reject) => {
        this.queue.push({ source, kind, language, window, key, force, batch, resolve, reject });
        PL.Store.set(source, kind, { state: "queued", message: "等待处理" }, language);
      });
      this.pending.set(key, promise);
      this.drain();
      return promise;
    },
    drain() {
      if (!PL.alive) return;
      // OCR and remote LLM requests use independent resources. Keep one worker
      // per kind, and serialize access to each PDF so a translation cannot read
      // a document while that same document is being regenerated.
      for (const kind of ["ocr", "translate"]) {
        if (this.lanes.has(kind)) continue;
        const index = this.queue.findIndex((task, index) => task.kind === kind && !this.forSource(task.source)
          && !this.queue.slice(0, index).some(earlier => earlier.source.id === task.source.id));
        if (index < 0) continue;
        const [task] = this.queue.splice(index, 1);
        this.lanes.set(kind, task);
        this.run(task);
      }
    },
    async run(task) {
      task.startedAt = Date.now();
      PL.UI.refreshTasks();
      try {
        PL.Store.set(task.source, task.kind, { state: "processing", message: "正在处理" }, task.language);
        const result = task.kind === "ocr" ? await this.ocrCore(task) : await this.translateCore(task);
        task.resolve(result);
      }
      catch (error) {
        if (!task.cancelled) PL.Store.set(task.source, task.kind, { state: "failed", message: error.message }, task.language);
        task.reject(error);
      }
      finally {
        this.pending.delete(task.key);
        this.lanes.delete(task.kind);
        PL.UI.refreshTasks();
        this.drain();
      }
    },
    async request(task, method, path, body, options = {}) {
      this.check(task);
      const result = await PL.request(method, path, body, {
        ...options, requestObserver: xhr => { task.xhr = xhr; if (task.cancelled) xhr.abort(); },
      });
      this.check(task);
      return result;
    },
    async fingerprint(source) {
      const path = await source.getFilePathAsync();
      if (!path) throw new Error("PDF 尚未下载，请先下载或定位文件；已有结果仍可离线查看");
      if ((await IOUtils.stat(path)).size > 200 * 1024 * 1024) throw new Error("PDF 超过 200MB 限制");
      return { path, hash: await Zotero.Utilities.Internal.md5Async(Zotero.File.pathToFile(path)) };
    },
    async parse(task, fingerprint) {
      await PL.Service.ensure();
      this.check(task);
      const window = task.window;
      if (!window || window.closed) throw new Error("Zotero 窗口已关闭");
      const form = new window.FormData();
      form.append("ocr_mode", PL.config.ocrMode || "official");
      form.append("force", String(!!task.force || !!PL.Store.get(task.source)?.forceNextOCR));
      form.append("upload", new window.Blob([await IOUtils.read(fingerprint.path)], { type: "application/pdf" }), PathUtils.filename(fingerprint.path));
      // Record the server job ID even if cancellation races with the upload
      // response, so the newly submitted job can still be cancelled below.
      const uploaded = await PL.request("POST", "/api/jobs", undefined, { body: form, headers: { "Content-Type": "multipart/form-data" },
        requestObserver: xhr => { task.xhr = xhr; if (task.cancelled) xhr.abort(); } });
      task.jobID = uploaded.job_id;
      if (task.cancelled) {
        await PL.request("POST", `/api/jobs/${encodeURIComponent(task.jobID)}/cancel`);
        this.check(task);
      }
      const deadline = Date.now() + 3600000;
      while (PL.alive && Date.now() < deadline) {
        this.check(task);
        const job = await this.request(task, "GET", `/api/jobs/${encodeURIComponent(uploaded.job_id)}`);
        this.check(task);
        if (["failed", "cancelled"].includes(job.state)) throw new Error(job.errors?.join("；") || "OCR 失败");
        if (["ready", "needs_review"].includes(job.state)) return job.document_id;
        const progress = job.progress;
        const seconds = Math.floor((Date.now() - task.startedAt) / 1000);
        const elapsed = seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
        task.message = `${progress?.message || `OCR ${progress?.page || 0}/${progress?.total || "…"}`} · 已用 ${elapsed}`;
        PL.Store.set(task.source, "ocr", { state: "processing", message: task.message });
        PL.UI.refreshTasks();
        await Zotero.Promise.delay(1500);
      }
      throw new Error(PL.alive ? "OCR 等待超时，可重试继续" : "处理已停止");
    },
    async ocrCore(task, fingerprint) {
      const library = Zotero.Libraries.get(task.source.libraryID);
      if (!library.editable || !library.filesEditable) throw new Error("当前文献库没有写入文件的权限");
      fingerprint ||= await this.fingerprint(task.source);
      const saved = await PL.Store.saved(task.source, "ocr", task.language, fingerprint.hash);
      this.check(task);
      if (saved && !task.force && !PL.Store.get(task.source)?.forceNextOCR) {
        PL.Store.set(task.source, "ocr", { ...saved, attachment: undefined, state: "ready", message: "" });
        return saved;
      }
      let documentID;
      try {
        documentID = await this.parse(task, fingerprint);
        const artifact = await this.request(task, "POST", `/api/documents/${encodeURIComponent(documentID)}/ocr-export`);
        this.check(task);
        if ((await this.fingerprint(task.source)).hash !== fingerprint.hash) throw new Error("PDF 在处理期间已修改，请重试");
        PL.Store.ensure(task.source).forceNextOCR = false;
        if (saved?.exportID === artifact.id) {
          PL.Store.set(task.source, "ocr", { ...saved, attachment: undefined, state: "ready", message: "" });
          return saved;
        }
        return await PL.Artifacts.download(task.source, "ocr", fingerprint.hash, artifact, task.language, () => this.check(task));
      }
      catch (error) {
        if (!task.cancelled) PL.Store.set(task.source, "ocr", { state: "failed", message: error.message });
        throw error;
      }
    },
    async translateCore(task) {
      const fingerprint = await this.fingerprint(task.source);
      const saved = await PL.Store.saved(task.source, "translate", task.language, fingerprint.hash);
      this.check(task);
      if (saved && !PL.Editing.stale(PL.Store.get(task.source), task.language)) {
        PL.Store.set(task.source, "translate", { ...saved, attachment: undefined, state: "ready", message: "" }, task.language);
        return saved;
      }
      const ocr = await this.ocrCore(task, fingerprint);
      await PL.Service.ensure();
      const config = await this.request(task, "GET", "/api/settings");
      if (!config.llm_base_url || !config.llm_model) throw new Error("请在论文翻译大师设置中填写翻译服务地址与模型；OCR 版本已保存");
      task.message = "正在翻译，完成后自动保存…";
      PL.UI.refreshTasks();
      // A saved OCR attachment can outlive the server workspace. If server data
      // was removed, uploading the PDF rebuilds it rather than failing forever.
      let documentID = ocr.documentID;
      if (ocr.editRevision) await PL.Editing.uploadSource(task, ocr);
      else try { await this.request(task, "GET", `/api/documents/${encodeURIComponent(documentID)}`, undefined, { timeout: 5000 }); }
      catch (_) {
        documentID = await this.parse(task, fingerprint);
        PL.Store.set(task.source, "ocr", { state: "ready", message: "", documentID });
      }
      const artifact = await this.request(task, "POST", `/api/documents/${encodeURIComponent(documentID)}/translations`, {
        lang_to: task.language, force: !!PL.Store.get(task.source)?.forceNextTranslation?.[task.language],
        source_revision: ocr.editRevision || undefined,
        revision: typeof PL.Store.get(task.source)?.forceNextTranslation?.[task.language] === "string"
          ? PL.Store.get(task.source).forceNextTranslation[task.language] : undefined,
      });
      this.check(task);
      if ((await this.fingerprint(task.source)).hash !== fingerprint.hash) throw new Error("PDF 在翻译期间已修改，请重试");
      const result = await PL.Artifacts.download(task.source, "translate", fingerprint.hash, artifact, task.language, () => this.check(task));
      if (PL.Store.get(task.source)?.forceNextTranslation) delete PL.Store.get(task.source).forceNextTranslation[task.language];
      await PL.Store.flush();
      return result;
    },
    check(task) {
      if (task.cancelled || !PL.alive) throw new Error("处理已取消");
    },
    async cancel(matches, message) {
      const queued = this.queue.filter(matches);
      this.queue = this.queue.filter(task => !matches(task));
      for (const task of queued) {
        task.cancelled = true; task.reject(new Error(message)); this.pending.delete(task.key);
      }
      const running = this.running.filter(matches);
      const settled = running.map(task => this.pending.get(task.key)?.catch(() => {}));
      for (const task of running) {
        task.cancelled = true;
        task.xhr?.abort();
        if (task.jobID) {
          try { await PL.request("POST", `/api/jobs/${encodeURIComponent(task.jobID)}/cancel`, undefined, { timeout: 3000 }); }
          catch (error) { Zotero.logError(error); }
        }
      }
      // Cancelling one paper must not stop the shared model/service or another
      // paper's concurrent translation. The backend cancels just this OCR job.
      await Promise.all(settled);
      PL.UI.refreshTasks();
    },
    async clearOCR(source) {
      if (PL.Editing.jobs.has(PL.Store.key(source))) throw new Error("请等待 OCR 修改保存完成");
      const library = Zotero.Libraries.get(source.libraryID);
      if (!library.editable || !library.filesEditable) throw new Error("当前文献库没有写入权限");
      await this.cancel(task => task.source.id === source.id, "OCR 结果已清除");
      PL.ReaderUI.releaseOCR(source);
      const search = new Zotero.Search();
      search.libraryID = source.libraryID;
      search.addCondition("tag", "is", `paperloom:pdf:${source.key}`);
      search.addCondition("tag", "is", "paperloom:ocr");
      const attachments = (await Zotero.Items.getAsync(await search.search()))
        .filter(item => item.isAttachment() && !item.deleted);
      if (attachments.length) await Zotero.Items.trashTx(attachments.map(item => item.id));
      const record = PL.Store.ensure(source);
      record.ocr = { state: "pending", message: "旧 OCR 已清除，下次将重新识别" };
      record.forceNextOCR = true;
      await PL.Store.flush();
      PL.Store.refresh();
    },
    async clearTranslation(source, language = PL.language()) {
      const library = Zotero.Libraries.get(source.libraryID);
      if (!library.editable || !library.filesEditable) throw new Error("当前文献库没有写入权限");
      const matches = task => task.source.id === source.id && task.kind === "translate" && task.language === language;
      await this.cancel(matches, "译文已清除");
      PL.ReaderUI.releaseTranslation(source, language);
      const search = new Zotero.Search(); search.libraryID = source.libraryID;
      search.addCondition("tag", "is", `paperloom:pdf:${source.key}`);
      search.addCondition("tag", "is", `paperloom:lang:${language}`);
      const attachments = (await Zotero.Items.getAsync(await search.search())).filter(item => item.isAttachment() && !item.deleted
        && PL.tags(item).some(tag => tag === "paperloom:reader" || tag === "paperloom:markdown"));
      if (attachments.length) await Zotero.Items.trashTx(attachments.map(item => item.id));
      const record = PL.Store.ensure(source);
      delete record.translations[language];
      (record.forceNextTranslation ||= {})[language] = Array.from(crypto.getRandomValues(new Uint8Array(12)), value => value.toString(16).padStart(2,"0")).join("");
      await PL.Store.flush(); PL.Store.refresh();
    },
    async selected(window, action) {
      const items = [...window.ZoteroPane.getSelectedItems()];
      return this.runItems(window,action,items);
    },
    async runItems(window, action, items) {
      if (!items.length) throw new Error("请先选中文献或 PDF 附件");
      const sources = [];
      const errors = [];
      for (const item of items) {
        try {
          const source = await this.sourceFor(item);
          if (!sources.some(value => value.id === source.id)) sources.push(source);
        }
        catch (error) { errors.push(error.message); }
      }
      const processing = ["ocr", "force-ocr", "translate"].includes(action);
      const batch = processing && sources.length ? {
        id: crypto.randomUUID(), kind: action === "translate" ? "translate" : "ocr",
        total: sources.length, done: 0, success: 0, failed: 0, skipped: errors.length, finished: false,
      } : null;
      if (batch) { this.batches.set(batch.id, batch); this.lastBatch = batch; PL.UI.refreshTasks(); }
      const promises = sources.map(async source => {
        try {
          if (action === "clear-ocr") { await this.clearOCR(source); return; }
          if (action === "clear-translation") { await this.clearTranslation(source); return; }
          const kind = ["translate", "open-translation"].includes(action) ? "translate" : "ocr";
          const saved = action.startsWith("open-") ? await PL.Store.saved(source, kind) : await this.enqueue(source, kind, window, { force: action === "force-ocr", batch });
          if (!saved) throw new Error(kind === "ocr" ? "尚无 OCR 版本，请先执行 OCR" : "尚无译文，请先执行翻译");
          // Processing is always a background operation, even for a one-paper
          // batch. Only the user's explicit View command creates a reader tab.
          if (action.startsWith("open-") && sources.length === 1) {
            const reader = await PL.ReaderUI.open(source);
            await PL.ReaderUI.setMode(reader, "split");
            await PL.ReaderUI.showSaved(reader, saved, kind);
          }
          if (batch) batch.success++;
        }
        catch (error) { if (batch) batch.failed++; errors.push(`${source.getField("title") || source.key}：${error.message}`); }
        finally { if (batch) { batch.done++; PL.UI.refreshTasks(); } }
      });
      await Promise.all(promises);
      if (batch) {
        batch.finished = true;
        this.batches.delete(batch.id); PL.UI.refreshTasks();
      }
      if (errors.length) throw new Error(errors.join("\n\n"));
      return batch;
    },
    register() {
      this.observer = Zotero.Notifier.registerObserver({ notify: (event, type, ids) => {
        if (type !== "item") return;
        if (event === "delete" || event === "trash") {
          PL.Store.reconcile().then(() => { PL.Store.flush(); PL.Store.refresh(); }).catch(error => Zotero.logError(error));
        }
        if (!PL.pref("autoOCR", false)) return;
        if (event === "add") for (const id of ids) this.autoCandidates.set(id, { started: Date.now() });
        if (event === "add" || (event === "modify" && ids.some(id => this.autoCandidates.has(id)))) {
          clearTimeout(this.autoTimer);
          this.autoTimer = setTimeout(() => this.processAuto().catch(error => Zotero.logError(error)), 1000);
        }
      } }, ["item"], "paperloom");
    },
    async processAuto() {
      if (!PL.alive || !PL.pref("autoOCR", false)) { this.autoCandidates.clear(); return; }
      for (const [id, candidate] of this.autoCandidates) {
        const item = await Zotero.Items.getAsync(id);
        if (!item || item.deleted || !item.isAttachment() || item.attachmentContentType !== "application/pdf") {
          this.autoCandidates.delete(id);
          continue;
        }
        if (!await item.getFilePathAsync()) {
          if (Date.now() - candidate.started > 300000) this.autoCandidates.delete(id);
          continue;
        }
        this.autoCandidates.delete(id);
        // Automatic processing is deliberately OCR-only, regardless of LLM configuration.
        this.enqueue(item, "ocr").catch(error => Zotero.logError(error));
      }
      if (this.autoCandidates.size) this.autoTimer = setTimeout(() => this.processAuto().catch(error => Zotero.logError(error)), 3000);
    },
    shutdown() {
      clearTimeout(this.autoTimer);
      if (this.observer) Zotero.Notifier.unregisterObserver(this.observer);
      for (const task of this.queue.splice(0)) {
        PL.Store.set(task.source, task.kind, { state: "failed", message: "处理已停止，可以重试" }, task.language);
        task.reject(new Error("插件已停用"));
      }
      for (const task of this.running) { task.cancelled = true; task.xhr?.abort(); }
      this.pending.clear();
    },
  };
  // Small public entry points are also useful for Zotero's developer console.
  PL.sourceFor = item => PL.Tasks.sourceFor(item);
  PL.ocr = (window, source) => PL.Tasks.enqueue(source, "ocr", window);
  PL.translate = (window, source) => PL.Tasks.enqueue(source, "translate", window);
  PL.savedFor = async (source, language = PL.language(), hash) => (await PL.Store.saved(source, "translate", language, hash))?.attachment;
})();
