import { actionPayload, frameConfig } from "./reader-protocol.mjs";

export class PaperLoomReaderParent extends JSWindowActorParent {
  receiveMessage(message) {
    if (message.name === "ready") {
      Services.obs.notifyObservers(null, "paperloom-reader-ready", String(this.browsingContext.id));
      const frame = this.browsingContext.embedderElement;
      return frameConfig(frame);
    }
    if (message.name === "action") {
      const frame = this.browsingContext.embedderElement;
      const callback = frame?._paperloomAction;
      if (typeof callback !== "function" || frame.isConnected === false) throw new Error("阅读窗口已经关闭，请重新打开已保存版本");
      return callback(actionPayload(message.data));
    }
  }
}
