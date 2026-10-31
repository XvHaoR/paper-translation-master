export class PaperLoomReaderParent extends JSWindowActorParent {
  receiveMessage(message) {
    if (message.name === "ready") {
      Services.obs.notifyObservers(null, "paperloom-reader-ready", String(this.browsingContext.id));
      const frame = this.browsingContext.embedderElement;
      return { available: typeof frame?._paperloomAction === "function", ...frame?._paperloomOptions?.() };
    }
    if (message.name === "action") {
      const callback = this.browsingContext.embedderElement?._paperloomAction;
      if (!callback) throw new Error("阅读窗口已经关闭，请重新打开已保存版本");
      return callback(message.data);
    }
  }
}
