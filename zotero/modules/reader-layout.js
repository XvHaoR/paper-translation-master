"use strict";
(() => {
  PaperLoom.ReaderLayout = {
    apply(state) {
      const { doc, mode, reader } = state;
      doc.body.classList.toggle("paperloom-reading", mode !== "original");
      doc.body.classList.toggle("paperloom-ocr-only", mode === "ocr");
      doc.body.classList.toggle("paperloom-swapped", mode !== "original" && state.swapped);
      doc.documentElement.style.setProperty("--paperloom-pdf-width", `${state.ratio * 100}%`);
      if (state.pane) {
        state.pane.hidden = mode === "original";
        state.closeMenus?.();
      }
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
      state.pane.style.setProperty("--pl-pane-height", Math.max(80,placeholder.height - 80) + "px");
      Object.assign(state.pane.style, {
        left: `${outer.left + placeholder.left - container.left}px`,
        top: `${outer.top + placeholder.top - container.top}px`,
        width: `${placeholder.width}px`, height: `${placeholder.height}px`,
      });
    },
  };
})();
