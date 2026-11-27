"use strict";
(() => {
  const PL = PaperLoom;
  const colorPattern = /^#[0-9a-f]{6}$/i;
  const asText = (value, maximum) => typeof value === "string" && value.length <= maximum ? value : "";
  const asAnchor = value => ({
    quote: asText(value?.quote, 12000).trim(),
    before: asText(value?.before, 160),
    after: asText(value?.after, 160),
  });
  function sortIndex(page, offset, rects, viewport) {
    const top = Math.max(0, viewport.viewBox[3] - Math.max(...rects.map(rect => rect[3])));
    return [page, offset, Math.floor(top)].map((value, index) =>
      String(value).slice(0, index === 1 ? 6 : 5).padStart(index === 1 ? 6 : 5, "0")).join("|");
  }

  PL.ReaderAnnotations = {
    register() {
      this.observer = Zotero.Notifier.registerObserver({notify: (event, _type, ids, extraData) => {
        if (!["add", "modify", "delete", "trash"].includes(event)) return;
        const parents = new Set(event === "delete" ? [] : ids.map(id => Zotero.Items.get(id))
          .filter(item => item?.isAnnotation?.()).map(item => item.parentID));
        const deleted = event === "delete" ? ids.map(id => extraData?.[id]).filter(Boolean) : [];
        const cleanup = [];
        for (const state of PL.ReaderUI.states.values()) {
          const pdf = Zotero.Items.get(state.reader.itemID);
          const anchors = pdf?.key && PL.Store.get(pdf)?.annotationAnchors;
          if (!anchors) continue;
          const deletedHere = deleted.filter(item => item.libraryID === pdf.libraryID && anchors[item.key]);
          if (!parents.has(pdf.id) && !deletedHere.length) continue;
          for (const item of deletedHere) cleanup.push([anchors, item.key]);
          this.refresh(state).catch(error => Zotero.logError(error));
        }
        for (const [anchors, key] of cleanup) delete anchors[key];
        if (cleanup.length) PL.Store.flush().catch(error => Zotero.logError(error));
      }}, ["item"], PL.id + "-annotations");
    },
    shutdown() {
      if (this.observer) Zotero.Notifier.unregisterObserver(this.observer);
      this.observer = null;
    },
    list(state) {
      const pdf = Zotero.Items.get(state.reader.itemID);
      if (!pdf) return [];
      const record = PL.Store.get(pdf);
      const anchors = record?.annotationAnchors || {};
      const language = state.kind === "translate" && this.language(state);
      const local = Object.values(record?.translationMarks || {}).filter(mark => (mark.scope === "ocr" || mark.language === language)
        && (!mark.hash || mark.hash === state.saved?.hash));
      return local.concat(pdf.getAnnotations().flatMap(item => {
        const anchor = anchors[item.key];
        if (!anchor || item.deleted || item.annotationType !== "highlight" ||
            (anchor.hash && state.saved?.hash && anchor.hash !== state.saved.hash)) return [];
        return [{...anchor, color: item.annotationColor}];
      }));
    },
    language(state) {
      const item = state.saved?.attachment;
      return (item && PL.tags(item).find(tag => tag.startsWith("paperloom:lang:"))?.slice(15)) || PL.language();
    },
    async refresh(state) {
      if (!PL.alive || !state.saved || !PL.ReaderSession.matches(state)) return;
      const global = state.frame.browsingContext?.currentWindowGlobal;
      if (global) await PL.ReaderUI.readerActor(global).sendQuery("annotation-marks", this.list(state));
    },
    async save(state, data) {
      const {frame, saved} = state;
      const windowGlobal = frame?.browsingContext?.currentWindowGlobal;
      const current = () => PL.alive && state.saved === saved && PL.ReaderSession.matches(state, frame) &&
        frame.browsingContext?.currentWindowGlobal === windowGlobal;
      const pdf = Zotero.Items.get(state.reader.itemID);
      if (!pdf || pdf.deleted || !pdf.isEditable()) throw new Error("这个 PDF 附件没有批注编辑权限");
      const source = asAnchor(data.source), target = data.target ? asAnchor(data.target) : null;
      if (target ? !target.quote || state.kind !== "translate" : !source.quote) throw new Error("请选择当前页面中的文字");
      const block = asText(data.block, 128);
      const actor = PL.ReaderUI.readerActor(windowGlobal);
      const original = block && await actor.sendQuery("block-source", {id: block});
      if (!current() || !original) throw new Error("阅读页面已更新，请重新选择文字");
      if (target || data.localSource === true) {
        const scope = target ? "translation" : "ocr";
        const marks = PL.Store.ensure(pdf).translationMarks ||= {};
        const language = target ? this.language(state) : "";
        const existing = Object.values(marks).find(mark => mark.scope === scope && mark.language === language
          && mark.hash === saved.hash && mark.block === block
          && JSON.stringify(target ? mark.target : mark.source) === JSON.stringify(target || source));
        const note = {value: existing?.comment || ""};
        if (data.remove && !existing) throw new Error("此批注已被删除或已更新");
        if (data.note && !Services.prompt.prompt(state.reader._window, target ? "译文批注" : "识别文本批注", "批注内容", note, null, {})) return {canceled:true};
        if (note.value.length > 20000 || !current()) throw new Error("选区已改变或批注过长");
        const key = existing?.key || Zotero.DataObjectUtilities.generateKey();
        if (data.remove) delete marks[key];
        else marks[key] = {key, scope, language, hash:saved.hash, block, page:original.page, ...(target ? {target} : {source}),
          comment:note.value.trim(), color:colorPattern.test(data.color) ? data.color.toLowerCase() : "#ffd400"};
        try { await PL.Store.flush(); }
        catch (error) { if (existing) marks[key] = existing; else delete marks[key]; throw error; }
        for (const other of PL.ReaderUI.states.values()) {
          if (other !== state && other.reader.itemID === pdf.id) this.refresh(other).catch(error => Zotero.logError(error));
        }
        return {key, removed:!!data.remove, marks:this.list(state)};
      }
      if (data.remove) throw new Error("请在 Zotero 原生批注列表中管理 PDF 批注");
      const context = await PL.PDFContext.ready(state);
      const known = await PL.PDFRegions.resolve(state, original, context.pdf.numPages);
      let positionRects, offset;
      if (typeof context.pdf.getPageData === "function") {
        const native = await PL.PDFSelection.nativeMatch(context, source, known);
        positionRects = native?.rects; offset = native?.offset;
      } else {
        const match = await PL.PDFSelection.resolve(context,
          {selectionText: source.quote, before: source.before, after: source.after}, known, {strict: true});
        if (match) {
          positionRects = new Map(); offset = match.offset;
          for (const page of new Set(match.regions.map(region => region.page))) {
            positionRects.set(page, await PL.PDFRegions.rects(context, page, match.regions));
          }
        }
      }
      if (!positionRects?.size) throw new Error("无法在 PDF 文字层准确定位这段文字，批注未保存");
      const pages = [...positionRects.keys()];
      if (pages.length > 2 || pages.length === 2 && pages[1] !== pages[0] + 1) {
        throw new Error("请选择同一页或相邻两页内的文字");
      }
      const page = pages[0];
      const rects = positionRects.get(page), nextPageRects = positionRects.get(pages[1]);
      if (!rects.length || nextPageRects?.length === 0) throw new Error("无法取得 PDF 批注坐标");
      const viewport = PL.SourceRegions.viewport(await context.pdf.getPage(page));
      const position = {pageIndex: page - 1, rects, ...(nextPageRects ? {nextPageRects} : {})};
      const nativeMeta = context.view.getAnnotationMeta?.(Components.utils.cloneInto(position, context.view));
      const pageLabel = nativeMeta?.pageLabel || (await context.pdf.getPageLabels())?.[page - 1] || String(page);
      if (!current() || !PL.PDFContext.same(state, context)) throw new Error("PDF 阅读窗口已更新，请重试");
      const comment = {value: ""};
      if (data.note === true && !Services.prompt.prompt(state.reader._window, "添加批注", "输入批注内容",
        comment, null, {})) return {canceled: true};
      if (comment.value.length > 20000) throw new Error("批注内容过长");
      const key = Zotero.DataObjectUtilities.generateKey();
      const anchors = PL.Store.ensure(pdf).annotationAnchors ||= {};
      anchors[key] = {block, page: original.page, source, target, hash: saved.hash || null};
      try {
        await PL.Store.flush();
        if (!current() || !PL.PDFContext.same(state, context)) throw new Error("阅读页面已更新，请重新选择文字");
        await Zotero.Annotations.saveFromJSON(pdf, {
          key, type: "highlight", text: source.quote, position,
          comment: [target?.quote, comment.value.trim()].filter(Boolean).join("\n\n"),
          color: colorPattern.test(data.color) ? data.color.toLowerCase() : "#ffd400",
          pageLabel, sortIndex: nativeMeta?.sortIndex || sortIndex(page - 1, offset, rects, viewport),
        });
      } catch (error) {
        delete anchors[key];
        await PL.Store.flush().catch(writeError => Zotero.logError(writeError));
        throw error;
      }
      return {key, marks: current() ? this.list(state) : []};
    },
  };
})();
