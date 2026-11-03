/* Saved-reader wire protocol. Keep these identifiers independent of UI names. */
export const READER_PROTOCOL = "paperloom.saved-reader/1";
export const BRIDGE_VERSION = 1;
export const ACTION_TYPES = new Set(["locate", "copy-latex", "excel", "save-ocr-edits", "translation-mode"]);

export function isSavedReader(document) {
  const body = document?.body;
  if (!body) return false;
  if (body.dataset.paperStandalone === "true") return false;
  if (body.dataset.readerProtocol === READER_PROTOCOL) return true;
  // Old artifacts are immutable. Recognize their structure, never their title,
  // translated UI labels or product name.
  if (body.hasAttribute("data-export-schema")) return true;
  if (document.querySelector(".pl-block[data-block-id]")) return true;
  return !!(document.querySelector("main") &&
    document.querySelector('script[src="assets/offline_reader.js"]') &&
    document.querySelector('nav a[href="ocr.md"],nav a[href="translation.md"]'));
}

export function actionPayload(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !ACTION_TYPES.has(value.type)) {
    throw new Error("不支持的阅读器操作，请更新插件后重新打开已保存版本");
  }
  if (value.id !== undefined && !((typeof value.id === "number" && Number.isFinite(value.id)) ||
      (typeof value.id === "string" && value.id.length > 0 && value.id.length <= 128))) {
    throw new Error("阅读器操作编号无效");
  }
  return value;
}

export function frameConfig(frame) {
  // Presentation preferences are optional. Their provider (or one of its
  // property getters) must never decide whether native actions are connected.
  const available = typeof frame?._paperloomAction === "function" && frame.isConnected !== false;
  let options = {}, optionError;
  try {
    const value = frame?._paperloomOptions?.();
    if (value && typeof value === "object" && !Array.isArray(value)) options = { ...value };
  } catch (error) { optionError = error?.message || String(error); }
  return { view: {}, css: "", ...options, protocol: READER_PROTOCOL, bridgeVersion: BRIDGE_VERSION,
    available, ...(optionError ? { optionError } : {}) };
}
