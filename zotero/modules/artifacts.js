"use strict";
(() => {
  const PL = PaperLoom;
  PL.Artifacts = {
    async pickSaveFile(window, title, filename, label, pattern) {
      const { FilePicker } = ChromeUtils.importESModule("chrome://zotero/content/modules/filePicker.mjs");
      const picker = new FilePicker();
      picker.init(window, title, picker.modeSave); picker.defaultString = filename; picker.appendFilter(label, pattern);
      return (await picker.show()) === picker.returnCancel ? null : picker.file;
    },
    async exportTables(window, items) {
      if (!Array.isArray(items) || !items.length || items.length > 200) throw new Error("表格内容无效");
      let count = 0;
      for (const item of items) {
        if (!Array.isArray(item.rows) || item.rows.length > 10000) throw new Error("表格过大");
        for (const row of item.rows) {
          if (!Array.isArray(row)) throw new Error("表格行无效");
          count += row.length;
          if (count > 100000 || row.some(cell => typeof cell !== "string" || cell.length > 100000)) throw new Error("表格过大");
        }
      }
      const xml = "\ufeff" + PL.Excel.buildWorkbook(items);
      if (xml.length > 10 * 1024 * 1024) throw new Error("导出表格超过 10MB");
      const file = await this.pickSaveFile(window, "导出 Excel 表格", "paper-tables.xls", "Excel 工作簿", "*.xls");
      if (!file) return false;
      await IOUtils.writeUTF8(file, xml);
      return true;
    },
    async extract(zipPath, destination, kind) {
      const reader = Cc["@mozilla.org/libjar/zip-reader;1"].createInstance(Ci.nsIZipReader);
      reader.open(Zotero.File.pathToFile(zipPath));
      try {
        await IOUtils.makeDirectory(destination, { ignoreExisting: true });
        const entries = reader.findEntries(null);
        let size = 0;
        while (entries.hasMore()) {
          const name = entries.getNext();
          if (name.endsWith("/")) continue;
          if (!name || name.includes("\\") || name.startsWith("/") || name.includes(":")
            || name.split("/").some(part => !part || part === "." || part === "..")) throw new Error("译文包包含不安全的路径");
          size += reader.getEntry(name).realSize;
          if (size > 300 * 1024 * 1024) throw new Error("文档包超过 300MB 限制");
          const path = PathUtils.join(destination, ...name.split("/"));
          await IOUtils.makeDirectory(PathUtils.parent(path), { createAncestors: true, ignoreExisting: true });
          reader.extract(name, Zotero.File.pathToFile(path));
        }
      }
      finally { reader.close(); }
      for (const name of ["index.html", "manifest.json", kind === "ocr" ? "ocr.md" : "translation.md"]) {
        if (!await IOUtils.exists(PathUtils.join(destination, name))) throw new Error(`文档包缺少 ${name}`);
      }
    },
    async download(source, kind, hash, artifact, language, check = () => {}) {
      if (!PL.alive) throw new Error("处理已停止");
      const temporary = Zotero.getTempDirectory();
      temporary.append("paperloom");
      temporary.createUnique(Ci.nsIFile.DIRECTORY_TYPE, 0o700);
      try {
        const route = kind === "ocr" ? "ocr" : "translations";
        const expected = `/api/documents/${encodeURIComponent(artifact.document_id)}/${route}/${artifact.id}/bundle.zip`;
        if (!/^[a-f0-9]{24}$/.test(artifact.id) || artifact.download_url !== expected) throw new Error("服务返回了无效的文档地址");
        const zip = PathUtils.join(temporary.path, "bundle.zip");
        await Zotero.HTTP.download(PL.serviceURL() + expected, zip);
        const directory = PathUtils.join(temporary.path, "content");
        await this.extract(zip, directory, kind);
        if (!PL.alive) throw new Error("处理已停止；服务端结果已保留");
        check();
        return await this.save(source, kind, hash, artifact, directory, language);
      }
      finally { await IOUtils.remove(temporary.path, { recursive: true, ignoreAbsent: true }); }
    },
    async save(source, kind, hash, artifact, directory, language) {
      const parentItemID = source.parentItemID || undefined;
      const parent = parentItemID ? Zotero.Items.get(parentItemID) : source;
      const title = `论文翻译大师 ${kind === "ocr" ? "OCR" : `译文 [${language}]`} — ${parent.getField("title") || source.key}`;
      const created = [];
      try {
        let reader;
        if (parentItemID) {
          reader = await Zotero.Attachments.importSnapshotFromFile({
            file: PathUtils.join(directory, "index.html"), parentItemID, title,
            contentType: "text/html", charset: "utf-8", singleFile: false, url: source.getField("url") || "",
          });
        }
        else {
          reader = await Zotero.Attachments.importFromFile({
            file: PathUtils.join(directory, "index.html"), libraryID: source.libraryID,
            title, contentType: "text/html", collections: source.getCollections(),
          });
        }
        created.push(reader);
        if (!parentItemID) await Zotero.File.copyDirectory(directory, Zotero.Attachments.getStorageDirectory(reader).path);
        if (kind === "translate") {
          const md = await Zotero.Attachments.importFromFile({
            file: PathUtils.join(directory, "translation.md"), parentItemID, libraryID: source.libraryID,
            title: `${title} (Markdown)`, contentType: "text/markdown",
            ...(parentItemID ? {} : { collections: source.getCollections() }),
          });
          created.push(md);
          await Zotero.File.copyDirectory(PathUtils.join(directory, "assets"), PathUtils.join(Zotero.Attachments.getStorageDirectory(md).path, "assets"));
        }
        for (const item of created) {
          for (const tag of [`paperloom:pdf:${source.key}`, `paperloom:hash:${hash}`, `paperloom:export:${artifact.id}`,
            `paperloom:document:${artifact.document_id}`]) item.addTag(tag);
          if (kind === "translate") item.addTag(`paperloom:lang:${language}`);
          item.addTag(item !== reader ? "paperloom:markdown" : kind === "ocr" ? "paperloom:ocr" : "paperloom:reader");
          await item.saveTx();
        }
        const status = { state: "ready", message: "", attachmentID: reader.id, attachmentKey: reader.key,
          hash, documentID: artifact.document_id, exportID: artifact.id,
          editRevision: kind === "ocr" && artifact.edited ? artifact.source_revision : null,
          ocrRevision: kind === "translate" ? artifact.ocr_revision || null : null };
        PL.Store.set(source, kind, status, language);
        return { ...status, attachment: reader };
      }
      catch (error) {
        for (const item of created.reverse()) await item.eraseTx();
        throw error;
      }
    },
    async exportMarkdown(window, saved, kind) {
      const path = await saved.attachment.getFilePathAsync();
      if (!path) throw new Error("附件尚未下载到本机");
      const directory = PathUtils.parent(path);
      const source = PathUtils.join(directory, kind === "ocr" ? "ocr.md" : "translation.md");
      const { FilePicker } = ChromeUtils.importESModule("chrome://zotero/content/modules/filePicker.mjs");
      const picker = new FilePicker();
      picker.init(window, "保存 Markdown", picker.modeSave);
      picker.defaultString = kind === "ocr" ? "paper-ocr.md" : `paper-${PL.language()}.md`;
      picker.appendFilter("Markdown", "*.md");
      if ((await picker.show()) === picker.returnCancel) return;
      await IOUtils.copy(source, picker.file);
      const media = PathUtils.join(directory, "assets", "media");
      if (await IOUtils.exists(media)) {
        const destination = PathUtils.join(PathUtils.parent(picker.file), "assets", "media");
        await IOUtils.makeDirectory(destination, { createAncestors: true, ignoreExisting: true });
        await Zotero.File.copyDirectory(media, destination);
      }
    },
  };
})();
