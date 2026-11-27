"use strict";
(() => {
  const PL = PaperLoom;
  PL.Editing = {
    jobs: new Map(),
    async open(reader) {
      const source = Zotero.Items.get(reader.itemID);
      const library = Zotero.Libraries.get(source.libraryID);
      if (!library.editable || !library.filesEditable) throw new Error("当前文献库没有写入权限");
      if (PL.Tasks.forSource(source) || PL.Tasks.queue.some(task => task.source.id === source.id)) throw new Error("请等待这篇论文的任务完成后再编辑 OCR");
      let saved = await PL.Store.saved(source, "ocr");
      if (!saved) throw new Error("请先生成 OCR 版本");
      const path = await saved.attachment.getFilePathAsync();
      const directory = PathUtils.parent(path);
      const manifest = await IOUtils.readJSON(PathUtils.join(directory,"manifest.json"));
      // Refresh saved editing controls locally; keep their correction overlay
      // and original IDs instead of sending an old paper through OCR again.
      if (Number(manifest.schema || 0) < 14 ||
          !await IOUtils.exists(PathUtils.join(directory, "editor.json"))) saved = await this.save(reader, {}, false);
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
      let result;
      try { result = await job; } finally { this.jobs.delete(key); }
      if (save && result.pendingLanguages?.length) {
        const summary = result.pendingLanguages.map(({language,count}) => language + " · " + count + " 段").join("，");
        if (Services.prompt.confirm(reader._window, "更新已修改段落的译文", "原文文字有改动（" + summary + "）。现在只翻译这些段落吗？稍后也可点击「翻译」。")) {
          for (const {language} of result.pendingLanguages) await PL.Tasks.enqueue(source, "translate", reader._window, {language});
        }
      }
      return result;
    },
    async worker(request, directory) {
      const root = await PL.Service.project(), python = await PL.Service.python(root);
      const requestPath = PathUtils.join(directory, "request.json");
      const resultPath = PathUtils.join(directory, "result.json");
      await IOUtils.writeJSON(requestPath, {...request, result:resultPath});
      const {Subprocess} = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
      const process = await Subprocess.call({command:python,arguments:[PathUtils.join(root,"scripts","edit_saved_ocr.py"), requestPath],workdir:root,stderr:"stdout"});
      let log = "";
      const drain = (async()=>{let chunk;while((chunk=await process.stdout.readString()))log=(log+chunk).slice(-4000);})();
      const result = await process.wait(); await drain;
      if (!PL.alive) throw new Error("保存已停止");
      const response = await IOUtils.readJSON(resultPath).catch(()=>({error:log || "本地编辑程序未能完成"}));
      if (response.error || result.exitCode !== 0) throw new Error(response.error || log);
      return response;
    },
    async retranslate(task, ocr, saved, manifest) {
      const revision = ocr.sourceRevision;
      const {texts} = await PL.Tasks.request(task, "POST", "/api/documents/" + encodeURIComponent(ocr.documentID) + "/translated-edits",
        {revision,blocks:manifest.pending_blocks,lang_to:task.language});
      const temp = Zotero.getTempDirectory(); temp.append("paperloom-sync"); temp.createUnique(Ci.nsIFile.DIRECTORY_TYPE, 0o700);
      try {
        const output = PathUtils.join(temp.path, "content");
        const result = await this.worker({operation:"retranslate",revision,texts,output,
          ocr:PathUtils.parent(await ocr.attachment.getFilePathAsync()),translation:PathUtils.parent(await saved.attachment.getFilePathAsync())}, temp.path);
        PL.Tasks.check(task);
        if ((await PL.Store.saved(task.source,"ocr"))?.sourceRevision !== revision ||
          (await PL.Store.saved(task.source,"translate",task.language))?.attachmentID !== saved.attachmentID ||
          (await PL.Tasks.fingerprint(task.source)).hash !== saved.hash) throw new Error("论文版本已更新，请重试");
        PL.Tasks.check(task);
        return await PL.Artifacts.save(task.source,"translate",saved.hash,result.artifact,output,task.language);
      } finally { await IOUtils.remove(temp.path,{recursive:true,ignoreAbsent:true}); }
    },
    async write(reader, source, data, save) {
      const library = Zotero.Libraries.get(source.libraryID);
      if (!library.editable || !library.filesEditable) throw new Error("当前文献库没有写入权限");
      if (PL.Tasks.forSource(source) || PL.Tasks.queue.some(task => task.source.id === source.id)) throw new Error("请等待这篇论文的任务完成后再保存 OCR");
      if (JSON.stringify(data).length > 10 * 1024 * 1024) throw new Error("编辑内容超过 10MB");
      const saved = await PL.Store.saved(source, "ocr");
      if (!saved) throw new Error("OCR 版本已删除，请重新打开");
      const path = await saved.attachment.getFilePathAsync(), directory = PathUtils.parent(path);
      const manifest = await IOUtils.readJSON(PathUtils.join(directory, "manifest.json"));
      const current = PL.ReaderUI.states.get(reader);
      if (save && (current?.kind !== "ocr" || current.saved?.attachmentID !== saved.attachmentID)) throw new Error("OCR 版本已更新，请重新编辑");
      const root = await PL.Service.project();
      const temp = Zotero.getTempDirectory(); temp.append("paperloom-edit"); temp.createUnique(Ci.nsIFile.DIRECTORY_TYPE, 0o700);
      try {
        const output = PathUtils.join(temp.path, "content");
        const translations = [];
        for (const language of Object.keys(PL.Store.ensure(source).translations || {})) {
          const translation = await PL.Store.saved(source,"translate",language);
          if (!translation) continue;
          const file = await translation.attachment.getFilePathAsync();
          if (!file) continue;
          const directory = PathUtils.parent(file);
          const meta = await IOUtils.readJSON(PathUtils.join(directory,"manifest.json"));
          const revision = meta.layout_revision || meta.ocr_revision;
          if (PL.config?.unified && revision && revision !== manifest.source_revision) continue;
          translations.push({language,saved:directory,attachmentID:translation.attachmentID});
        }
        const response = await this.worker({saved:directory,output,translations,
          workspace:PL.Service.runtime?.environment?.PAPERBRIDGE_WORKSPACE || PathUtils.join(root,"workspace"),
          save,base_revision:manifest.edit_revision || null,changes:data.changes || {},insertions:data.insertions || [],
          order:data.order,moved:data.moved}, temp.path);
        const latest = await PL.Store.saved(source,"ocr");
        if (latest?.attachmentID !== saved.attachmentID) throw new Error("保存期间 OCR 版本已更新，请重新编辑");
        if (PL.config?.unified) {
          response.artifact.recognition_mode = saved.recognitionMode;
          response.artifact.local_engine = saved.localEngine;
        }
        const updated = await PL.Artifacts.save(source,"ocr",saved.hash,response.artifact,output,PL.language());
        updated.pendingLanguages = [];
        for (const translation of response.translations || []) {
          const latestTranslation = await PL.Store.saved(source,"translate",translation.language);
          if (latestTranslation?.attachmentID !== translation.attachmentID) continue;
          const synced = await PL.Artifacts.save(source,"translate",saved.hash,translation.artifact,translation.output,translation.language);
          if (translation.pending?.length) updated.pendingLanguages.push({language:translation.language,count:translation.pending.length});
          for (const [openReader,state] of PL.ReaderUI.states) {
            if (openReader.itemID === source.id && state.kind === "translate" &&
                state.saved?.attachmentID === translation.attachmentID) await PL.ReaderUI.showSaved(openReader,synced,"translate");
          }
        }
        if (response.warnings?.length) PL.alert(reader._window,new Error("OCR 已保存。"+response.warnings.join("\n")));
        await PL.Store.flush();
        if (PL.ReaderUI.states.has(reader)) await PL.ReaderUI.showSaved(reader,updated,"ocr");
        return updated;
      } finally { await IOUtils.remove(temp.path,{recursive:true,ignoreAbsent:true}); }
    },
    async uploadSource(task, ocr, revision = ocr.editRevision) {
      const path = await ocr.attachment.getFilePathAsync();
      const source = path && PathUtils.join(PathUtils.parent(path),"source.zip");
      if (!revision || !source || !await IOUtils.exists(source))
        throw new Error("这份旧识别结果缺少可验证的原文快照。请先重新识别，再翻译新结果。");
      const bytes = await IOUtils.read(source);
      const form = new task.window.FormData();
      form.append("revision",revision);
      form.append("upload",new task.window.Blob([bytes],{type:"application/zip"}),"source.zip");
      await PL.Tasks.request(task,"POST",`/api/documents/${encodeURIComponent(ocr.documentID)}/edited-source`,undefined,
        {body:form,headers:{"Content-Type":"multipart/form-data"}});
    },
    stale(record, language = PL.language()) {
      const translation = record?.translations?.[language];
      if (PL.config?.unified && translation?.attachmentID && record.ocr?.sourceRevision) {
        return (translation.ocrRevision || null) !== record.ocr.sourceRevision;
      }
      return !!translation?.attachmentID && (translation.ocrRevision || null) !== (record.ocr?.editRevision || null);
    },
  };
})();
