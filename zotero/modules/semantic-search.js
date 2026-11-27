"use strict";
(() => {
  const PL = PaperLoom;
  const roles = {abstract: "摘要", document_title: "标题", heading: "章节标题", paragraph: "正文",
    list: "正文", caption: "图表说明", footnote: "脚注", unknown: "正文"};

  function passages(text, data) {
    let remaining = String(text || "").replace(/\s+/g, " ").trim();
    const result = [];
    while (remaining) {
      // Stay within the multilingual encoder's context for both CJK and English.
      let end = Math.min(remaining.length, /[\p{Script=Han}]/u.test(remaining) ? 380 : 1200);
      if (end < remaining.length) {
        const breakAt = Math.max(remaining.lastIndexOf(". ", end), remaining.lastIndexOf("。", end),
          remaining.lastIndexOf("！", end), remaining.lastIndexOf("？", end));
        if (breakAt >= end * .55) end = breakAt + 1;
        else if (/\S/.test(remaining[end - 1]) && /\S/.test(remaining[end])) {
          const space = remaining.lastIndexOf(" ", end);
          if (space >= end * .7) end = space;
        }
        // Don't split a Unicode surrogate pair at the chunk boundary.
        if (/[\uD800-\uDBFF]/.test(remaining[end - 1])) end--;
      }
      result.push({text: remaining.slice(0, end).trim(), ...data});
      remaining = remaining.slice(end).trim();
    }
    return result;
  }

  PL.SemanticSearch = {
    windows: new Map(),
    queue: new Set(),
    generations: new Map(),
    async configure(library, embedding, key) {
      const path = PathUtils.join(PL.Store.directory, "semantic-" + Number(library) + ".json");
      await IOUtils.writeJSON(path, {embedding, key:embedding.backend === "api" ? key : ""}, {tmpPath:path + ".tmp", mode:0o600});
      this.generations.set(library, (this.generations.get(library) || 0) + 1);
      for (const record of Object.values(PL.Store.records)) {
        if (record.libraryID === library && (record.ocr?.attachmentID || record.semanticCleared)) {
          delete record.semanticIndexed;
          this.queue.add(record.itemID);
        }
      }
      await PL.Store.flush();
      this.start();
    },
    indexError(library) {
      return Object.values(PL.Store.records).find(r => r.libraryID === library && r.semanticError)?.semanticError || "";
    },
    async preference(library) {
      const path = PathUtils.join(PL.Store.directory, "semantic-" + Number(library) + ".json");
      return await IOUtils.exists(path) ? IOUtils.readJSON(path) : {embedding:{backend:"local"}, key:""};
    },
    resume() {
      if (!PL.config.semanticSearch) return;
      for (const record of Object.values(PL.Store.records)) {
        if (record.semanticCleared || record.ocr?.attachmentID && record.semanticIndexed !== record.ocr.attachmentKey) this.queue.add(record.itemID);
      }
      this.start();
    },
    schedule(source) {
      if (!PL.config.semanticSearch) return;
      this.queue.add(source.id);
      this.start();
    },
    start() {
      if (!this.running && this.queue.size && PL.alive && !this.stopping && Zotero.getMainWindow()) {
        this.running = this.drain().catch(error => Zotero.logError(error)).finally(() => { this.running = null; this.start(); });
      }
    },
    async drain() {
      while (this.queue.size && PL.alive && !this.stopping) {
        const id = this.queue.values().next().value;
        this.queue.delete(id);
        const pdf = await Zotero.Items.getAsync(id);
        if (!pdf || pdf.deleted) continue;
        const paper = PL.TitleTranslations.paper(pdf);
        if (!paper) continue;
        const record = PL.Store.get(pdf);
        if (!record) continue;
        try {
          const saved = await PL.Store.saved(pdf, "ocr");
          const snapshot = saved?.attachment.key || "";
          if ((!saved && !record.semanticCleared) || (snapshot && record.semanticIndexed === snapshot)) continue;
          const generation = this.generations.get(paper.libraryID);
          const {embedding, key} = await this.preference(paper.libraryID);
          await this.indexPaper(Zotero.getMainWindow(), paper, pdf, saved, embedding, key);
          if ((record.ocr?.attachmentKey || "") === snapshot && generation === this.generations.get(paper.libraryID)) {
            record.semanticIndexed = snapshot;
            delete record.semanticCleared;
            record.semanticError = "";
            await PL.Store.flush();
          }
        } catch (error) {
          record.semanticError = error.message;
          await PL.Store.flush();
          Zotero.logError(error);
        }
      }
    },
    open(owner) {
      this.resume();
      const library = owner.ZoteroPane.getSelectedLibraryID();
      if (!library) throw new Error("请先在 Zotero 选中一个文献库");
      const existing = this.windows.get(owner);
      if (existing && !existing.closed) { existing.focus(); return; }
      const args = {owner, library, controller: this}; args.wrappedJSObject = args;
      const dialog = owner.openDialog("chrome://paperloom/content/semantic.xhtml", "paperloom-semantic",
        "chrome,dialog=no,resizable,centerscreen,width=940,height=720", args);
      this.windows.set(owner, dialog);
      dialog.addEventListener("unload", event => {
        if (event.target?.documentURI === "chrome://paperloom/content/semantic.xhtml" && this.windows.get(owner) === dialog) {
          this.windows.delete(owner);
        }
      });
    },
    shutdown() {
      this.stopping = true;
      this.queue.clear();
      for (const dialog of this.windows.values()) if (!dialog.closed) dialog.close();
      this.windows.clear();
    },
    async papers(owner, library, scope) {
      let items;
      if (scope === "library") {
        const search = new Zotero.Search();
        search.libraryID = library;
        search.addCondition("deleted", "false");
        items = await Zotero.Items.getAsync(await search.search());
      } else items = owner.ZoteroPane.itemsView?.getSortedItems() || [];
      return [...new Map(items.map(item => PL.TitleTranslations.paper(item))
        .filter(item => item?.libraryID === library).map(item => [item.id, item])).values()];
    },
    async savedSource(paper) {
      try {
        const pdf = await PL.Tasks.sourceFor(paper);
        const saved = await PL.Store.saved(pdf, "ocr");
        return {pdf, saved};
      } catch (_) {
        // Metadata can still be searched for an item without a local PDF.
        return {};
      }
    },
    async revision(paper, saved) {
      const input = JSON.stringify([1, paper.getField("title"), paper.getField("abstractNote"),
        PL.TitleTranslations.text(paper), saved?.attachment?.key, saved?.sourceRevision, saved?.attachment?.dateModified]);
      const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input));
      return Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, "0")).join("");
    },
    async recognized(owner, saved) {
      if (!saved) return [];
      const path = await saved.attachment.getFilePathAsync();
      if (!path) return [];
      const snapshot = PathUtils.join(PathUtils.parent(path), "source.json");
      if (await IOUtils.exists(snapshot)) {
        const data = await IOUtils.readJSON(snapshot);
        return (data.blocks || []).flatMap(block => {
          const label = roles[block.type];
          const content = block.content || {};
          if (!label) return [];
          return passages(content.text || content.items?.join(" "), {page: Number(block.page) || 0, block: block.id, label});
        });
      }
      // Older results still expose text and page IDs in the saved HTML.
      const doc = new owner.DOMParser().parseFromString(await IOUtils.readUTF8(path), "text/html");
      return Array.from(doc.querySelectorAll(".pl-block"), node => {
        const label = roles[node.dataset.blockType];
        const content = node.querySelector(".pl-source,.pl-target");
        content?.querySelectorAll("button,.pl-block-tools,mjx-assistive-mml").forEach(tool => tool.remove());
        return label && content ? passages(content.textContent, {
          page: Number(node.dataset.page) || 0, block: node.dataset.blockId || "", label,
        }) : [];
      }).flat();
    },
    async request(path, body, key) {
      await PL.Service.ensure();
      return PL.request("POST", "/api/zotero/semantic/" + path, body, {
        logBodyLength: 0,
        ...(key ? {headers: {"Content-Type": "application/json", Authorization: "Bearer " + key}} : {}),
      });
    },
    async indexPaper(owner, paper, pdf, saved, embedding, key, previous) {
      const title = PL.TitleTranslations.original(paper);
      const revision = await this.revision(paper, saved);
      if (revision === previous) return false;
      const metadata = [...passages(title, {label:"标题"}), ...passages(PL.TitleTranslations.text(paper, "zh"), {label:"翻译标题"}),
        ...passages(paper.getField("abstractNote"), {label:"摘要"})];
      const content = await this.recognized(owner, saved);
      if (!metadata.length && !content.length) return false;
      await this.request("index", {library:paper.libraryID, embedding, paper:paper.key, title, revision,
        pdf:pdf?.key || "", snapshot:saved?.attachment.key || "", passages:[...metadata, ...content]}, key);
      return true;
    },
    async index(owner, library, scope, embedding, key, progress, stopped) {
      const papers = await this.papers(owner, library, scope);
      const {papers: indexed} = await this.request("status", {library, embedding});
      let updated = 0, reused = 0, failed = 0, firstError = "";
      for (let i = 0; i < papers.length && !stopped(); i++) {
        const paper = papers[i];
        if (i % 5 === 0) progress(i, papers.length);
        try {
          const {pdf, saved} = await this.savedSource(paper);
          if (await this.indexPaper(owner, paper, pdf, saved, embedding, key, indexed[paper.key])) updated++;
          else reused++;
        } catch (error) {
          failed++;
          firstError ||= error.message;
          if (!updated && /嵌入 API|本地语义模型/.test(firstError)) throw error;
        }
      }
      return {updated, reused, failed, firstError};
    },
    async search(library, query, embedding, key) {
      const {hits} = await this.request("search", {library, query, embedding}, key);
      const results = [];
      for (const hit of hits) {
        const item = await Zotero.Items.getByLibraryAndKeyAsync(library, hit.paper);
        if (!item || item.deleted) continue;
        hit.title = PL.TitleTranslations.original(item) || hit.title;
        hit.titleZh = PL.TitleTranslations.text(item, "zh");
        const pdf = hit.pdf && await Zotero.Items.getByLibraryAndKeyAsync(library, hit.pdf);
        const ocr = pdf && await PL.Store.saved(pdf, "ocr");
        if (hit.snapshot && hit.snapshot !== ocr?.attachment.key) {
          hit.passages = hit.passages.filter(p => !p.page && !p.block);
          if (!hit.passages.length) continue;
        }
        const current = ocr && ocr.attachment.key === hit.snapshot;
        if (current && hit.passages.some(p => p.block)) {
          const file = await ocr.attachment.getFilePathAsync();
          if (file) {
            try {
              const blocks = (await IOUtils.readJSON(PathUtils.join(PathUtils.parent(file), "source.json"))).blocks;
              let heading;
              for (const block of blocks) {
                if (block.type === "heading") heading = block;
                for (const p of hit.passages) {
                  if (p.block === block.id && heading?.id !== block.id) {
                    p.section = heading?.content?.text || "";
                    p.sectionID = heading?.id;
                  }
                }
              }
            } catch (_) { /* Older HTML snapshots may only have page and block locations. */ }
          }
        }
        const translated = current && await PL.Store.saved(pdf, "translate", "zh", ocr.hash);
        if (translated) {
          try {
            const directory = PathUtils.parent(await translated.attachment.getFilePathAsync());
            const manifest = await IOUtils.readJSON(PathUtils.join(directory,"manifest.json"));
            if (ocr.sourceRevision && (manifest.layout_revision || manifest.ocr_revision) === ocr.sourceRevision) {
              const pairs = await IOUtils.readJSON(PathUtils.join(directory,"translation-state.json"));
              const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
              const originals = new Map(pairs.originals.map(b => [b.id, normalize(b.content.text || b.content.items?.join(" "))]));
              const targets = new Map(pairs.translated.map(b => [b.id, b.content.text || b.content.items?.join(" ")]));
              for (const p of hit.passages) {
                if (p.sectionID && p.section === originals.get(p.sectionID) && !pairs.target_html?.[p.sectionID]
                    && !manifest.pending_blocks?.includes(p.sectionID)) p.sectionZh = normalize(targets.get(p.sectionID));
                if (originals.get(p.block) !== normalize(p.text) || manifest.pending_blocks?.includes(p.block)) continue;
                if (!pairs.target_html?.[p.block]) p.zh = normalize(targets.get(p.block));
              }
            }
          } catch (_) { /* Legacy and partially synced attachments can translate individual excerpts. */ }
        }
        results.push(hit);
      }
      return results;
    },
    async jump(owner, library, hit, passage) {
      const pdf = hit.pdf && await Zotero.Items.getByLibraryAndKeyAsync(library, hit.pdf);
      if (!PL.AttachmentGroups.isPDF(pdf)) {
        const paper = await Zotero.Items.getByLibraryAndKeyAsync(library, hit.paper);
        if (paper && !paper.deleted) await owner.ZoteroPane.selectItem(paper.id);
        return;
      }
      const reader = await PL.ReaderUI.open(pdf);
      if (!passage.page) return;
      const attachment = hit.snapshot && await Zotero.Items.getByLibraryAndKeyAsync(library, hit.snapshot);
      const saved = attachment && !attachment.deleted && await PL.Store.savedAttachment(attachment, "ocr");
      if (saved && passage.block) {
        await PL.ReaderUI.setMode(reader, "split");
        await PL.ReaderUI.showSaved(reader, saved, "ocr");
        const state = PL.ReaderUI.states.get(reader);
        const global = state.frame?.browsingContext?.currentWindowGlobal;
        if (global) {
          const block = await PL.ReaderUI.readerActor(global).sendQuery("select-block", {id: passage.block});
          if (block && await PL.ReaderUI.locate(state, block)) return;
        }
      }
      await reader.navigate({pageIndex: passage.page - 1});
    },
  };
})();
