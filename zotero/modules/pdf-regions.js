"use strict";
(() => {
  const PL = PaperLoom;
  const validBox = box => Array.isArray(box) && box.length === 4
    && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1)
    && box[2] > box[0] && box[3] > box[1];
  PL.PDFRegions = {
    box(value) {
      const box = Array.isArray(value) ? Array.from(value, Number) : [];
      return validBox(box) ? box : null;
    },
    normalize(values, pages) {
      const seen = new Set(), result = [];
      for (const value of values || []) {
        const page = Number(value?.page), bbox = this.box(value?.bbox);
        if (!bbox || !Number.isInteger(page) || page < 1 || page > pages) continue;
        const key = `${page}:${bbox.join(",")}`;
        if (seen.has(key)) continue;
        seen.add(key); result.push({ page, bbox });
      }
      return result;
    },
    async resolve(state, data, pages) {
      const bbox = this.box(data.bbox);
      const known = this.normalize([...(Array.isArray(data.regions) ? data.regions : []),
        { page: data.page, bbox }], pages);
      if (!bbox) return known;
      try {
        // SourceRegions owns text matching. A failed text lookup must not erase
        // valid OCR coordinates, including separately detected column regions.
        const recovered = await PL.SourceRegions.resolve(state, { ...data, bbox,
          ...(known.length > 1 ? { regions: known } : {}) });
        return this.normalize([...known, ...(Array.isArray(recovered) ? recovered : [])], pages);
      }
      catch (error) { Zotero.logError(error); return known; }
    },
    async rects(context, page, regions) {
      const boxes = regions.filter(region => region.page === page);
      if (!boxes.length) return [];
      try {
        const viewport = PL.PDFContext.page(context, page - 1)?.viewport
          || PL.SourceRegions.viewport(await context.pdf.getPage(page));
        return boxes.map(region => {
          const first = viewport.convertToPdfPoint(region.bbox[0] * viewport.width, region.bbox[1] * viewport.height);
          const second = viewport.convertToPdfPoint(region.bbox[2] * viewport.width, region.bbox[3] * viewport.height);
          return [Math.min(first[0], second[0]), Math.min(first[1], second[1]),
            Math.max(first[0], second[0]), Math.max(first[1], second[1])];
        }).filter(rect => rect.every(Number.isFinite));
      }
      catch (error) { Zotero.logError(error); return []; }
    },
  };
})();
