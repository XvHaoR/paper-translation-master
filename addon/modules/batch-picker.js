"use strict";
(() => {
  const PL = PaperLoom;
  PL.BatchPicker = {
    windows: new Map(),
    open(owner, action = "ocr") {
      const existing = this.windows.get(owner);
      if (existing && !existing.closed) {
        existing.PaperLoomBatch?.setAction(action);
        existing.focus(); return existing;
      }
      const argument = {controller:PL,owner,action}; argument.wrappedJSObject = argument;
      const dialog = owner.openDialog("chrome://paperloom/content/batch.xhtml", "paperloom-batch",
        "chrome,dialog=no,resizable,centerscreen,width=1060,height=740", argument);
      this.windows.set(owner,dialog);
      dialog.addEventListener("unload",event=>{
        // openDialog first replaces about:blank. Only the picker document's
        // own unload closes this registration; subdocuments may unload too.
        if(event.target?.documentURI!=="chrome://paperloom/content/batch.xhtml")return;
        if(this.windows.get(owner)===dialog)this.windows.delete(owner);
      });
      return dialog;
    },
    async rows(owner, scope = "view") {
      let items;
      const selected = [...owner.ZoteroPane.getSelectedItems()];
      if (scope === "library") {
        const libraryID = owner.ZoteroPane.getSelectedLibraryID();
        if (!libraryID) throw new Error("请先选择一个文献库，或切回当前列表范围");
        const search = new Zotero.Search(); search.libraryID = libraryID;
        search.addCondition("deleted","false");
        items = await Zotero.Items.getAsync(await search.search());
      } else items = [...(owner.ZoteroPane.itemsView?.getSortedItems() || [])];
      const parents = new Set(items.filter(item=>item.isRegularItem?.()).map(item=>item.id));
      // Ordinary child attachments collapse into their paper. Explicitly
      // selected PDFs can still identify a different/supplementary attachment.
      const candidates = [...items.filter(item=>item.isRegularItem?.() ||
        (item.isAttachment?.() && item.attachmentContentType === "application/pdf" && !parents.has(item.parentItemID))), ...selected];
      const preselected = new Set();
      for (const item of selected) {
        try { preselected.add((await PL.Tasks.sourceFor(item)).id); } catch (_) {}
      }
      const seen = new Set(), rows = [];
      for (let index=0; index<candidates.length; index++) {
        const item = candidates[index];
        if (item.deleted || (!item.isRegularItem?.() && !(item.isAttachment?.() && item.attachmentContentType === "application/pdf"))) continue;
        let source, reason = "";
        try { source = await PL.Tasks.sourceFor(item); }
        catch (_) { reason = "无 PDF"; }
        const id = source?.id || `item-${item.id}`;
        if (seen.has(id)) continue;
        seen.add(id);
        const parent = source?.parentItemID ? Zotero.Items.get(source.parentItemID) : null;
        const paper = parent || item;
        if (source) {
          const library = Zotero.Libraries.get(source.libraryID);
          if (!library.editable || !library.filesEditable) reason = "文献库只读";
          else {
            const path = await source.getFilePathAsync();
            if (!path || !await IOUtils.exists(path)) reason = "PDF 待下载或文件缺失";
          }
        }
        rows.push({id,source,item:paper,title:paper.getField("title") || source?.getField("title") || "未命名文献",
          creator:paper.getField("firstCreator") || "",year:(paper.getField("date") || "").match(/\d{4}/)?.[0] || "",
          pdfTitle:source?.getField("title") || "",available:!!source && !reason,reason,preselected:preselected.has(id)});
        if (index % 50 === 49) await Zotero.Promise.delay(0);
      }
      return rows;
    },
    async submit(owner, action, rows, ids) {
      const wanted = new Set(ids);
      const items = rows.filter(row=>row.available && wanted.has(row.id)).map(row=>row.source);
      if (!items.length) throw new Error("请先勾选至少一篇有本地 PDF 的文献");
      return PL.Tasks.runItems(owner,action,items);
    },
    close(owner) {
      const dialog = this.windows.get(owner);
      if (dialog && !dialog.closed) dialog.close();
      this.windows.delete(owner);
    },
    shutdown() { for (const owner of [...this.windows.keys()]) this.close(owner); },
  };
})();
