"use strict";
(() => {
  const PL = PaperLoom;
  const roles = ["paperloom:ocr", "paperloom:reader", "paperloom:markdown"];
  PL.AttachmentGroups = {
    pending: new Map(),
    sourceKey(item) {
      if (!item?.isAttachment() || item.deleted) return null;
      const tags = PL.tags(item);
      if (!roles.some(role => tags.includes(role))) return null;
      const keys = tags.filter(tag => tag.startsWith("paperloom:pdf:")).map(tag => tag.slice(14));
      return keys.length === 1 && keys[0] ? keys[0] : null;
    },
    writable(source) {
      const library = source && Zotero.Libraries.get(source.libraryID);
      return !!library?.editable && !!library.filesEditable;
    },
    async orphans(source) {
      const search = new Zotero.Search();
      search.libraryID = source.libraryID;
      search.addCondition("tag", "is", `paperloom:pdf:${source.key}`);
      return (await Zotero.Items.getAsync(await search.search())).filter(item =>
        item?.libraryID === source.libraryID && !item.parentItemID && this.sourceKey(item) === source.key);
    },
    async ensure(source) {
      if (!source?.isAttachment() || source.attachmentContentType !== "application/pdf" || source.deleted) {
        throw new Error("无法为此结果找到有效的原 PDF");
      }
      const key = `${source.libraryID}/${source.key}`;
      if (this.pending.has(key)) return this.pending.get(key);
      const operation = this.group(source).finally(() => this.pending.delete(key));
      this.pending.set(key, operation);
      return operation;
    },
    async group(source) {
      if (!this.writable(source)) throw new Error("当前文献库没有整理附件的权限");
      const orphans = await this.orphans(source);
      let parent, created = false, moved = 0;
      await Zotero.DB.executeTransaction(async () => {
        source = await Zotero.Items.getAsync(source.id);
        if (!source || source.deleted) throw new Error("原 PDF 已删除，未整理附件");
        if (source.parentItemID) {
          parent = await Zotero.Items.getAsync(source.parentItemID);
          if (!parent?.isRegularItem() || parent.deleted || parent.libraryID !== source.libraryID) {
            throw new Error("原 PDF 的父条目无效，未整理附件");
          }
        }
        else {
          // A PDF attachment cannot itself contain attachments. Use Zotero's
          // generic document type; do not invent authors, DOI or publication data.
          parent = new Zotero.Item("document");
          parent.libraryID = source.libraryID;
          const title = source.getField("title") || source.attachmentFilename || source.key;
          parent.setField("title", title.replace(/\.pdf$/i, ""));
          await parent.save();
          source.parentID = parent.id;
          await source.save();
          created = true;
        }
        for (const item of orphans) {
          // Recheck after awaits: another action may have moved/deleted an item.
          if (item.deleted || item.parentItemID || item.libraryID !== source.libraryID || this.sourceKey(item) !== source.key) continue;
          item.parentID = parent.id;
          // Zotero transfers a standalone item's collection membership to its
          // parent on save. IDs, keys, attachment files and annotations stay put.
          await item.save();
          moved++;
        }
      });
      PL.Store.ensure(source);
      await PL.Store.flush();
      PL.Store.refresh([source.id, parent.id, ...orphans.map(item => item.id)]);
      return { parent, created, moved, sourceID: source.id };
    },
    async migrateExisting() {
      if (this.migrating) return this.migrating;
      this.migrating = this.migrate().finally(() => { this.migrating = null; });
      return this.migrating;
    },
    async migrate() {
      const search = new Zotero.Search();
      search.addCondition("joinMode", "any");
      for (const role of roles) search.addCondition("tag", "is", role);
      const items = await Zotero.Items.getAsync(await search.search());
      const sources = new Map(), snapshot = new Map();
      const report = { parentsCreated: 0, resultsMoved: 0, skipped: 0, errors: 0 };
      const remember = item => snapshot.set(item.id, { id: item.id, key: item.key, libraryID: item.libraryID,
        parentID: item.parentItemID || null, collections: item.getCollections() });
      for (const item of items) {
        const key = this.sourceKey(item);
        if (!key || item.parentItemID) continue;
        const source = await Zotero.Items.getByLibraryAndKeyAsync(item.libraryID, key);
        if (!source?.isAttachment() || source.deleted || source.attachmentContentType !== "application/pdf" || !this.writable(source)) {
          report.skipped++; continue;
        }
        sources.set(`${source.libraryID}/${source.key}`, source);
        remember(source); remember(item);
        if (source.parentItemID) {
          const parent = await Zotero.Items.getAsync(source.parentItemID);
          if (parent) remember(parent);
        }
      }
      if (sources.size) {
        // Keep the previous hierarchy locally for diagnosis/recovery. Nothing
        // from this record is exported with the paper or sent to a server.
        const backup = { schema: 1, createdAt: new Date().toISOString(), items: [...snapshot.values()], results: [] };
        const path = PathUtils.join(PL.Store.directory, `attachment-groups-${Date.now()}.json`);
        await IOUtils.writeJSON(path, backup, { tmpPath: path + ".tmp" });
        for (const source of sources.values()) {
          try {
            const result = await this.ensure(source);
            report.parentsCreated += Number(result.created);
            report.resultsMoved += result.moved;
            backup.results.push({ sourceID: source.id, parentID: result.parent.id, created: result.created });
          }
          catch (error) { report.errors++; Zotero.logError(error); }
        }
        await IOUtils.writeJSON(path, backup, { tmpPath: path + ".tmp" });
        report.backup = path;
      }
      this.lastReport = report;
      return report;
    },
  };
})();
