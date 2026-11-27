"use strict";
(() => {
  const PL = PaperLoom;
  const pdfTag = "paperloom:pdf:";
  const resultRoles = ["paperloom:ocr", "paperloom:reader", "paperloom:markdown"];

  PL.AttachmentGroups = {
    pending: new Map(),

    sourceKey(item) {
      if (!item?.isAttachment() || item.deleted) return null;
      const tags = PL.tags(item);
      const roles = resultRoles.filter(role => tags.includes(role));
      if (roles.length !== 1) return null;
      const types = roles[0] === "paperloom:markdown" ? ["text/markdown", "text/plain"] : ["text/html"];
      if (!types.includes(item.attachmentContentType)) return null;
      const keys = tags.filter(tag => tag.startsWith(pdfTag));
      return keys.length === 1 ? keys[0].slice(pdfTag.length) || null : null;
    },

    isPDF(item) {
      return item?.isAttachment() && !item.deleted && item.attachmentContentType === "application/pdf";
    },

    writable(source) {
      const library = Zotero.Libraries.get(source.libraryID);
      return !!library?.editable && !!library.filesEditable;
    },

    async orphans(source) {
      const search = new Zotero.Search();
      search.libraryID = source.libraryID;
      search.addCondition("tag", "is", pdfTag + source.key);
      const items = await Zotero.Items.getAsync(await search.search());
      return items.filter(item => item?.libraryID === source.libraryID && !item.parentItemID && this.sourceKey(item) === source.key);
    },

    async ensure(source) {
      if (!this.isPDF(source)) throw new Error("无法为此结果找到有效的原 PDF");
      const key = PL.Store.key(source);
      if (this.pending.has(key)) return this.pending.get(key);
      const operation = this.group(source.id, source.libraryID).finally(() => this.pending.delete(key));
      this.pending.set(key, operation);
      return operation;
    },

    async recordPreviousHierarchy(items) {
      const path = PathUtils.join(PL.Store.directory, `attachment-groups-${Date.now()}-${Zotero.Utilities.randomString(8)}.json`);
      const before = items.map(item => ({ id:item.id, key:item.key, libraryID:item.libraryID,
        parentID:item.parentItemID || null, collections:item.getCollections() }));
      await IOUtils.writeJSON(path, { schema:1, createdAt:new Date().toISOString(), items:before }, { tmpPath:path + ".tmp" });
    },

    async group(sourceID, libraryID) {
      // Zotero loads items and children separately. Do not change that hierarchy during the initial load.
      const library = Zotero.Libraries.get(libraryID);
      if (!library?.editable || !library.filesEditable) throw new Error("当前文献库没有整理附件的权限");
      await library.waitForDataLoad("item");
      const result = await Zotero.DB.executeTransaction(async () => {
        const source = await Zotero.Items.getAsync(sourceID);
        if (!this.isPDF(source)) throw new Error("原 PDF 已删除或已变更");
        if (!this.writable(source)) throw new Error("当前文献库没有整理附件的权限");
        const created = !source.parentItemID;
        let parent = created ? null : await Zotero.Items.getAsync(source.parentItemID);
        if (!created && (!parent?.isRegularItem() || parent.deleted || parent.libraryID !== source.libraryID)) {
          throw new Error("原 PDF 的父条目无效");
        }
        const orphans = await this.orphans(source);
        if (created || orphans.length) await this.recordPreviousHierarchy([source, ...orphans, ...(parent ? [parent] : [])]);
        if (created) {
          parent = new Zotero.Item("document");
          parent.libraryID = source.libraryID;
          const title = source.getField("title") || source.attachmentFilename || source.key;
          parent.setField("title", title.replace(/\.pdf$/i, "").trim() || source.key);
          await parent.save();
        }
        const moved = [];
        for (const item of created ? [source, ...orphans] : orphans) {
          if (item.deleted || item.parentItemID) continue;
          // Zotero transfers collection memberships when a top-level item becomes a child.
          item.parentID = parent.id;
          await item.save();
          moved.push(item.id);
        }
        return { parent, created, moved, source };
      });
      if (result.moved.length) {
        PL.Store.ensure(result.source);
        if (result.created) PL.TitleTranslations?.moveFromAttachment(result.source, result.parent);
        await PL.Store.flush();
        PL.Store.refresh([result.parent.id, result.source.id, ...result.moved.filter(id => id !== result.source.id)]);
      }
      return result;
    },

    async migrateExisting() {
      const search = new Zotero.Search();
      search.addCondition("joinMode", "any");
      for (const role of resultRoles) search.addCondition("tag", "is", role);
      const items = await Zotero.Items.getAsync(await search.search());
      const sources = new Map();
      const report = { parentsCreated:0, resultsMoved:0, errors:0 };
      for (const item of items) {
        if (item?.parentItemID) continue;
        const key = this.sourceKey(item);
        if (!key) continue;
        const source = await Zotero.Items.getByLibraryAndKeyAsync(item.libraryID, key);
        if (this.isPDF(source) && this.writable(source)) sources.set(PL.Store.key(source), source);
      }
      for (const source of sources.values()) {
        if (!PL.alive) break;
        try {
          const { created, moved } = await this.ensure(source);
          report.parentsCreated += Number(created);
          report.resultsMoved += moved.length - Number(moved.includes(source.id));
        }
        catch (error) { report.errors++; Zotero.logError(error); }
      }
      return report;
    },
  };
})();
