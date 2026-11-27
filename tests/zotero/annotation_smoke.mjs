export class PaperLoomAnnotationSmokeChild extends JSWindowActorChild {
  receiveMessage({name, data}) {
    const doc = this.document, win = this.contentWindow;
    if (name === "select" || name === "mark-point") {
      for (const node of doc.querySelectorAll(data.selector)) {
        const walker = doc.createTreeWalker(node, 4);
        let text;
        while ((text = walker.nextNode())) {
          const start = text.data.indexOf(data.words);
          if (start === -1) continue;
          text.parentElement.scrollIntoView({block: "center"});
          const range = doc.createRange();
          range.setStart(text, start);
          range.setEnd(text, start + data.words.length);
          win.getSelection().removeAllRanges();
          if (name === "mark-point") return range.getClientRects()[0]?.toJSON();
          win.getSelection().addRange(range);
          return true;
        }
      }
      throw new Error("Missing annotation fixture text: " + data.words);
    }
    if (name === "palette") {
      const tools = doc.querySelector(".pl-annotation-tools:not([hidden])");
      return {point: tools?.querySelector("button:not([disabled])")?.getBoundingClientRect().toJSON(),
        remove: [...(tools?.querySelectorAll("button") || [])].find(b => !b.hidden && b.textContent === "删除高亮与批注")?.getBoundingClientRect().toJSON(),
        status: tools?.querySelector('[role="status"]')?.textContent};
    }
    if (name === "has-highlight") return win.CSS.highlights.has("paperloom-annotation-" + data);
    return null;
  }
}
