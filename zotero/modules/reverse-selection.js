"use strict";
(() => {
  const PL=PaperLoom;
  PL.ReverseSelection={
    register() {
      this.listener=({reader,params})=>{
        const state=PL.ReaderUI.states.get(reader);
        if (state) this.selected(state,params?.annotation).catch(error=>Zotero.logError(error));
      };
      Zotero.Reader.registerEventListener("renderTextSelectionPopup",this.listener,PL.id);
    },
    active(state) { return PL.alive && state.mode==="split" && state.saved && state.frame && PL.pref("blockCompare", true); },
    async selected(state,annotation) {
      if (!this.active(state) || typeof annotation?.text!=="string" || annotation.text.trim().length<4 || annotation.text.length>20000) return;
      const position=annotation.position, page=Number(position?.pageIndex)+1;
      if (!Number.isInteger(page) || page<1) return;
      const signature=JSON.stringify([annotation.text,position]);
      if(state.reverseSignature===signature) return;
      const frame=state.frame, global=frame.browsingContext?.currentWindowGlobal;
      const revision=state.reverseRevision=(state.reverseRevision||0)+1, inputAt=Date.now();
      const context=PL.PDFContext.current(state);
      if (!global || !context.pdf) return;
      let point;
      const rect=position.rects?.[0];
      if(rect?.length===4 && Array.from(rect).every(Number.isFinite)) {
        const viewport=PL.SourceRegions.viewport(await context.pdf.getPage(page));
        const xy=viewport.convertToViewportPoint((rect[0]+rect[2])/2,(rect[1]+rect[3])/2);
        point={x:xy[0]/viewport.width,y:xy[1]/viewport.height};
      }
      if(!this.active(state) || state.frame!==frame || state.reverseRevision!==revision) return;
      PL.ScrollSync.mutePDF(state);
      // Dismiss a previous Chinese-to-English overlay; keep the user's real
      // PDF selection untouched and create no persistent annotation.
      PL.PDFHighlight.clear(state);
      if(state.lastLocated?.highlight==="selection")state.lastLocated=null;
      const matched = await PL.ReaderUI.readerActor(global).sendQuery("highlight-translation",{text:annotation.text,page,point,revision,inputAt});
      if (matched && state.reverseRevision === revision) state.reverseSignature = signature;
      return matched;
    },
    clear(state) {
      state.reverseSignature=null;
      const revision=state.reverseRevision=(state.reverseRevision||0)+1;
      const global=state.frame?.browsingContext?.currentWindowGlobal;
      if (global?.documentURI?.scheme !== "file") return;
      try { PL.ReaderUI.readerActor(global).sendQuery("highlight-translation",{clear:true,revision,inputAt:Date.now()}).catch(()=>{}); }
      catch (_) { /* The reader may have detached before the clear command. */ }
    },
    shutdown() { Zotero.Reader.unregisterEventListener("renderTextSelectionPopup",this.listener); },
  };
})();
