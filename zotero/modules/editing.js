"use strict";
(() => {
  const PL = PaperLoom;
  PL.Editing = {
    jobs: new Map(),
    async open(reader) {
      const source = Zotero.Items.get(reader.itemID);
      const library = Zotero.Libraries.get(source.libraryID);
      if (!library.editable || !library.filesEditable) throw new Error("当前文献库没有写入权限");
      if (PL.Tasks.pending.size) throw new Error("请等待处理任务完成后再编辑 OCR");
      let saved = await PL.Store.saved(source, "ocr");
      if (!saved) throw new Error("请先生成 OCR 版本");
      const path = await saved.attachment.getFilePathAsync();
      if (!await IOUtils.exists(PathUtils.join(PathUtils.parent(path), "editor.json"))) saved = await this.save(reader, {}, false);
      await PL.ReaderUI.showSaved(reader, saved, "ocr");
      const state = PL.ReaderUI.states.get(reader);
      const deadline = Date.now() + 5000;
      while (Date.now() < deadline) {
        try { await PL.ReaderUI.readerActor(state.frame.browsingContext.currentWindowGlobal).sendQuery("editor-command", {command:"open"}); return; }
        catch (_) { await Zotero.Promise.delay(100); }
      }
      throw new Error("编辑器加载超时，请重新打开 OCR 页面");
    },
    async save(reader, data, save = true) {
      const source = Zotero.Items.get(reader.itemID);
      const key = PL.Store.key(source);
      if (this.jobs.has(key)) throw new Error("正在保存此篇 OCR，请稍候");
      const job = this.write(reader, source, data, save);
      this.jobs.set(key, job);
      try { return await job; } finally { this.jobs.delete(key); }
    },
    async write(reader, source, data, save) {
      const library = Zotero.Libraries.get(source.libraryID);
      if (!library.editable || !library.filesEditable) throw new Error("当前文献库没有写入权限");
      if (PL.Tasks.pending.size) throw new Error("请等待处理任务完成后再保存 OCR");
      if (JSON.stringify(data).length > 10 * 1024 * 1024) throw new Error("编辑内容超过 10MB");
      const saved = await PL.Store.saved(source, "ocr");
      if (!saved) throw new Error("OCR 版本已删除，请重新打开");
      const path = await saved.attachment.getFilePathAsync(), directory = PathUtils.parent(path);
      const manifest = await IOUtils.readJSON(PathUtils.join(directory, "manifest.json"));
      const current = PL.ReaderUI.states.get(reader);
      if (save && (current?.kind !== "ocr" || current.saved?.attachmentID !== saved.attachmentID)) throw new Error("OCR 版本已更新，请重新编辑");
      const root = await PL.Service.project(), python = await PL.Service.python(root);
      const temp = Zotero.getTempDirectory(); temp.append("paperloom-edit"); temp.createUnique(Ci.nsIFile.DIRECTORY_TYPE, 0o700);
      try {
        const output = PathUtils.join(temp.path, "content"), resultPath = PathUtils.join(temp.path, "result.json");
        const requestPath = PathUtils.join(temp.path, "request.json");
        await IOUtils.writeJSON(requestPath, {saved:directory,output,result:resultPath,
          workspace:PL.Service.runtime?.environment?.PAPERBRIDGE_WORKSPACE || PathUtils.join(root,"workspace"),
          save,base_revision:manifest.edit_revision || null,changes:data.changes || {},insertions:data.insertions || []});
        const {Subprocess} = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
        const process = await Subprocess.call({command:python,arguments:[PathUtils.join(root,"scripts","edit_saved_ocr.py"),requestPath],workdir:root,stderr:"stdout"});
        let log = "";
        const drain = (async()=>{let chunk;while((chunk=await process.stdout.readString()))log=(log+chunk).slice(-4000);})();
        const result = await process.wait(); await drain;
        if (!PL.alive) throw new Error("保存已停止；原有 OCR 未修改");
        const response = await IOUtils.readJSON(resultPath).catch(()=>({error:log || "本地编辑程序未能完成"}));
        if (response.error || result.exitCode !== 0) throw new Error(response.error || log);
        const latest = await PL.Store.saved(source,"ocr");
        if (latest?.attachmentID !== saved.attachmentID) throw new Error("保存期间 OCR 版本已更新，请重新编辑");
        const updated = await PL.Artifacts.save(source,"ocr",saved.hash,response.artifact,output,PL.language());
        await PL.Store.flush();
        if (PL.ReaderUI.states.has(reader)) await PL.ReaderUI.showSaved(reader,updated,"ocr");
        return updated;
      } finally { await IOUtils.remove(temp.path,{recursive:true,ignoreAbsent:true}); }
    },
    async uploadSource(task, ocr) {
      const path = await ocr.attachment.getFilePathAsync();
      const bytes = await IOUtils.read(PathUtils.join(PathUtils.parent(path),"source.zip"));
      const form = new task.window.FormData();
      form.append("revision",ocr.editRevision);
      form.append("upload",new task.window.Blob([bytes],{type:"application/zip"}),"source.zip");
      await PL.request("POST",`/api/documents/${encodeURIComponent(ocr.documentID)}/edited-source`,undefined,
        {body:form,headers:{"Content-Type":"multipart/form-data"}});
    },
    stale(record, language = PL.language()) {
      const translation = record?.translations?.[language];
      return !!translation?.attachmentID && (translation.ocrRevision || null) !== (record.ocr?.editRevision || null);
    },
  };
})();
