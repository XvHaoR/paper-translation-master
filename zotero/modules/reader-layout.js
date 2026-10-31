"use strict";
(() => {
  const CSS = `
    .paperloom-ocr-pane[hidden],.paperloom-ocr-placeholder[hidden],.paperloom-divider[hidden],.paperloom-pane-message[hidden]{display:none!important}
    .paperloom-reader-controls{display:flex;align-items:center;gap:4px;padding-inline:5px;font:12px system-ui}
    .paperloom-reader-controls .paperloom-mode-button{max-width:145px;overflow:hidden;text-overflow:ellipsis}
    .paperloom-reader-controls button,.paperloom-pane-toolbar button{font:inherit;white-space:nowrap;border:1px solid #8885;border-radius:4px;padding:4px 7px;background:var(--material-background,#fff);color:inherit;cursor:pointer}
    .paperloom-reader-controls button[aria-pressed="true"],.paperloom-pane-toolbar button[aria-pressed="true"]{background:#3875d7;color:white}
    .paperloom-reader-controls button:disabled,.paperloom-pane-toolbar button:disabled{opacity:.45;cursor:default}
    body.paperloom-reading #split-view{flex-direction:row!important}
    body.paperloom-reading #split-view>.primary-view{flex:0 0 var(--paperloom-pdf-width,50%)!important;width:var(--paperloom-pdf-width,50%)!important;min-width:0;order:0}
    body.paperloom-reading #split-view>.secondary-view,body.paperloom-reading #reader-ui .split-view>.secondary-view,body.paperloom-reading #reader-ui .split-view-resizer{display:none!important}
    body.paperloom-reading #reader-ui .split-view>.primary-view{flex:0 0 var(--paperloom-pdf-width,50%)!important;width:var(--paperloom-pdf-width,50%)!important;min-width:0}
    body.paperloom-swapped #split-view>.primary-view{order:2!important}
    body.paperloom-swapped #reader-ui .split-view>.primary-view{margin-inline-start:calc(100% - var(--paperloom-pdf-width,50%))}
    body.paperloom-ocr-only #split-view>.primary-view,body.paperloom-ocr-only #reader-ui .split-view>.primary-view{display:none!important}
    .paperloom-ocr-pane{display:flex;flex:1;min-width:0;flex-direction:column;position:relative;z-index:3;background:var(--material-background,#fff);color:var(--fill-primary,#222);order:2}
    body.paperloom-swapped .paperloom-ocr-pane{order:0}
    .paperloom-ocr-placeholder{flex:1;min-width:0;order:2}
    body.paperloom-swapped .paperloom-ocr-placeholder{order:0}
    .paperloom-pane-toolbar{display:flex;align-items:center;gap:5px;padding:6px 8px;flex-wrap:wrap;border-bottom:1px solid #8884;font:12px system-ui;flex-shrink:0}
    .paperloom-pane-toolbar strong{margin-inline-end:auto}
    .paperloom-pane-content{position:relative;display:flex;flex:1;min-height:0}
    .paperloom-pane-frame{position:relative!important;visibility:visible!important;width:100%;height:100%;flex:1;min-height:0;border:0;background:var(--material-background,#fff)}
    .paperloom-pane-message{padding:28px;line-height:1.7;max-width:540px;font:14px system-ui;white-space:pre-wrap}
    .paperloom-divider{flex:0 0 5px;background:#8882;cursor:col-resize;order:1;z-index:4}
    .paperloom-divider:focus,.paperloom-divider:hover{background:#3875d7}
    body.paperloom-ocr-only .paperloom-divider{display:none}
  `;
  PaperLoom.ReaderLayout = { css: CSS,
    apply(state) {
      const { doc, mode, reader } = state;
      doc.body.classList.toggle("paperloom-reading", mode !== "original");
      doc.body.classList.toggle("paperloom-ocr-only", mode === "ocr");
      doc.body.classList.toggle("paperloom-swapped", mode !== "original" && state.swapped);
      doc.documentElement.style.setProperty("--paperloom-pdf-width", `${state.ratio * 100}%`);
      if (state.pane) state.pane.hidden = mode === "original";
      if (state.placeholder) state.placeholder.hidden = mode === "original";
      if (state.divider) state.divider.hidden = mode === "original";
      for (const controls of doc.querySelectorAll(".paperloom-reader-controls")) {
        for (const button of controls.querySelectorAll(".paperloom-mode-button")) {
          button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
        }
        controls.querySelector(".paperloom-swap-button").disabled = mode !== "split";
      }
      doc.defaultView.dispatchEvent(new doc.defaultView.Event("resize"));
      this.position(state);
    },
    position(state) {
      if (!state.pane) return;
      const placeholder = state.placeholder.getBoundingClientRect();
      const outer = state.reader._iframe.getBoundingClientRect();
      const container = state.container.getBoundingClientRect();
      state.pane.hidden = state.mode === "original" || !placeholder.width || !outer.width;
      Object.assign(state.pane.style, {
        left: `${outer.left + placeholder.left - container.left}px`,
        top: `${outer.top + placeholder.top - container.top}px`,
        width: `${placeholder.width}px`, height: `${placeholder.height}px`,
      });
    },
  };
})();
