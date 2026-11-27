"use strict";
(() => {
  const PL = PaperLoom;
  PL.TitleTranslations = {
    paper(item) {
      if (!item || item.deleted) return null;
      if (item.isRegularItem()) return item;
      if (!item.isAttachment() || item.attachmentContentType !== "application/pdf") return null;
      if (!item.parentItemID) return item;
      const parent = Zotero.Items.get(item.parentItemID);
      return parent?.isRegularItem?.() && !parent.deleted ? parent : null;
    },
    original(paper) {
      const title = (paper.getField("title") || "").trim();
      return paper.isRegularItem() ? title : title.replace(/\.pdf$/i, "").trim();
    },
    text(paper, language = PL.language()) {
      if (!paper || paper.deleted) return "";
      const entry = PL.Store.get(paper)?.titles?.[language];
      return entry?.source === this.original(paper) ? entry.text : "";
    },
    column(item) {
      const paper = this.paper(item);
      return paper === item ? this.text(paper) : "";
    },
    async translate(task) {
      const paper = this.paper(task.source);
      const title = paper && this.original(paper);
      if (!title) throw new Error("这篇文献没有可翻译的标题");
      const saved = this.text(paper, task.language);
      if (saved) return saved;
      await PL.Service.ensure();
      task.serviceURL = PL.Service.url();
      const result = await PL.Tasks.request(task, "POST", "/api/zotero/translate-title",
        {title, lang_to: task.language});
      if (typeof result.title !== "string" || !result.title.trim()) throw new Error("未收到标题译文");
      const current = this.paper(task.source);
      if (!current || this.original(current) !== title) throw new Error("翻译期间标题已修改，请重试");
      const titles = PL.Store.ensure(current).titles ||= {};
      titles[task.language] = {source: title, text: result.title};
      await PL.Store.flush();
      PL.Store.refresh([current.id]);
      return result.title;
    },
    moveFromAttachment(source, parent) {
      const titles = PL.Store.get(source)?.titles;
      if (!titles) return;
      for (const [language, entry] of Object.entries(titles)) {
        if (entry.source !== this.original(parent)) continue;
        const parentTitles = PL.Store.ensure(parent).titles ||= {};
        parentTitles[language] ||= entry;
      }
    },
  };
})();
