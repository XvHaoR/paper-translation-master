"use strict";
(() => {
  const PL = PaperLoom;
  PL.Store = {
    records: {},
    writeQueue: Promise.resolve(),
    key(source) { return `${source.libraryID}/${source.key}`; },
    get(source) { return this.records[this.key(source)]; },
    async load() {
      this.directory = PathUtils.join(Zotero.Profile.dir, "paperloom");
      this.path = PathUtils.join(this.directory, "state.json");
      await IOUtils.makeDirectory(this.directory, { ignoreExisting: true });
      try {
        const data = await IOUtils.readJSON(this.path);
        if (data.schema === 2 && data.records && typeof data.records === "object") this.records = data.records;
      }
      catch (error) { if (await IOUtils.exists(this.path)) Zotero.logError(error); }
      for (const record of Object.values(this.records)) {
        for (const status of [record.ocr, ...Object.values(record.translations || {})]) {
          if (status && ["queued", "processing"].includes(status.state)) {
            status.state = "failed";
            status.message = "上次处理被中断，请重试；已保存结果会保留";
          }
        }
      }
      await this.reconcile();
      await this.flush();
    },
    async reconcile() {
      // Native attachments survive profile-cache removal and sync to other machines.
      const search = new Zotero.Search();
      search.addCondition("joinMode", "any");
      search.addCondition("tag", "is", "paperloom:ocr");
      search.addCondition("tag", "is", "paperloom:reader");
      const items = await Zotero.Items.getAsync(await search.search());
      for (const attachment of items) {
        if (!attachment.isAttachment() || attachment.deleted) continue;
        const tags = PL.tags(attachment);
        const value = prefix => tags.find(tag => tag.startsWith(prefix))?.slice(prefix.length);
        const sourceKey = value("paperloom:pdf:");
        if (!sourceKey) continue;
        const source = await Zotero.Items.getByLibraryAndKeyAsync(attachment.libraryID, sourceKey);
        if (!source) continue;
        const kind = tags.includes("paperloom:ocr") ? "ocr" : "translate";
        const language = value("paperloom:lang:") || "zh";
        let documentID = value("paperloom:document:");
        let metadata = {};
        const path = await attachment.getFilePathAsync();
        if (path) {
          try { metadata = await IOUtils.readJSON(PathUtils.join(PathUtils.parent(path), "manifest.json")); documentID ||= metadata.document_id; }
          catch (_) { /* Legacy/synced attachments may have no metadata locally. */ }
        }
        const record = this.ensure(source);
        const previous = kind === "ocr" ? record.ocr : record.translations[language];
        if (!previous?.attachmentID || attachment.id >= previous.attachmentID) {
          const status = { state: path ? "ready" : "missing", attachmentID: attachment.id, attachmentKey: attachment.key,
            hash: value("paperloom:hash:"), documentID, exportID: value("paperloom:export:"),
            editRevision: kind === "ocr" && metadata.edited ? metadata.source_revision : null,
            ocrRevision: metadata.ocr_revision || null,
            message: path ? "" : "请先下载已同步的附件" };
          if (kind === "ocr") record.ocr = status;
          else record.translations[language] = status;
        }
      }
      for (const record of Object.values(this.records)) {
        for (const status of [record.ocr, ...Object.values(record.translations || {})]) {
          if (!status?.attachmentID) continue;
          const item = await Zotero.Items.getAsync(status.attachmentID);
          if (!item || item.deleted) {
            delete status.attachmentID;
            delete status.attachmentKey;
            status.state = "pending";
            status.message = "保存的附件已删除";
          }
        }
      }
    },
    ensure(source) {
      const key = this.key(source);
      const record = this.records[key] ||= { libraryID: source.libraryID, sourceKey: source.key, translations: {} };
      record.itemID = source.id;
      record.parentID = source.parentItemID || null;
      record.translations ||= {};
      return record;
    },
    set(source, kind, status, language = PL.language()) {
      const record = this.ensure(source);
      if (kind === "ocr") record.ocr = { ...record.ocr, ...status };
      else record.translations[language] = { ...record.translations[language], ...status };
      this.flush();
      this.refresh([source.id, source.parentItemID].filter(Boolean));
    },
    async flush() {
      if (!this.path) return;
      const snapshot = JSON.stringify({ schema: 2, records: this.records });
      this.writeQueue = this.writeQueue.catch(error => Zotero.logError(error)).then(() =>
        IOUtils.writeUTF8(this.path, snapshot, { tmpPath: this.path + ".tmp" }));
      return this.writeQueue;
    },
    refresh(ids = true) {
      for (const window of Zotero.getMainWindows()) {
        const view = window.ZoteroPane?.itemsView;
        view?.invalidateRowCache?.(ids);
        view?.tree?.invalidate();
      }
      PL.ReaderUI?.refresh();
    },
    stateForItem(item, kind) {
      const records = Object.values(this.records).filter(record => record.itemID === item.id || record.parentID === item.id);
      const statuses = records.map(record => kind === "ocr" ? record.ocr : record.translations?.[PL.language()]).filter(Boolean);
      for (const state of ["processing", "queued", "failed", "ready", "missing"]) {
        if (statuses.some(status => status.state === state)) return state;
      }
      return "pending";
    },
    label(item, kind) {
      if (!item.isRegularItem() && !(item.isAttachment() && item.attachmentContentType === "application/pdf")) return "";
      const state = this.stateForItem(item, kind);
      if (kind === "translate" && state === "ready" && Object.values(this.records).some(record =>
        (record.itemID === item.id || record.parentID === item.id) && PL.Editing?.stale(record))) return "原文已修改";
      return ({ processing: kind === "ocr" ? "识别中…" : "翻译中…", queued: "排队中", failed: "失败 · 可重试",
        ready: kind === "ocr" ? "已 OCR" : "已翻译", missing: "待下载", pending: kind === "ocr" ? "未 OCR" : "未翻译" })[state];
    },
    async saved(source, kind, language = PL.language(), hash) {
      const record = this.get(source);
      const status = kind === "ocr" ? record?.ocr : record?.translations?.[language];
      if (!status?.attachmentID || (hash && status.hash !== hash)) return null;
      const attachment = await Zotero.Items.getAsync(status.attachmentID);
      if (!attachment || attachment.deleted || !await attachment.getFilePathAsync()) return null;
      return { ...status, attachment };
    },
  };
})();
