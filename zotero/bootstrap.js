/* global Zotero, Services, ChromeUtils, IOUtils, PathUtils, Components */
"use strict";
var { classes: Cc, interfaces: Ci } = Components;
var PaperLoom = {
  name: "论文翻译大师",
  id: "paperloom@paperloom.local",
  alive: false,
  windows: new Map(),
  pref(name, fallback) {
    const value = Zotero.Prefs.get(`extensions.paperloom.${name}`, true);
    return value === undefined || value === null ? fallback : value;
  },
  setPref(name, value) { Zotero.Prefs.set(`extensions.paperloom.${name}`, value, true); },
  serviceURL(value = this.pref("serviceURL", "http://127.0.0.1:8765")) {
    const url = new URL(value);
    if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
      throw new Error("服务地址须为本机 http://127.0.0.1:端口");
    }
    return url.origin;
  },
  language() {
    const language = this.pref("language", "zh");
    if (!/^[A-Za-z][A-Za-z0-9-]{0,15}$/.test(language)) throw new Error("目标语言代码无效");
    return language;
  },
  async request(method, path, body, options = {}) {
    if (!this.alive) throw new Error("论文翻译大师已停用");
    const response = await Zotero.HTTP.request(method, this.serviceURL() + path, {
      timeout: 3600000, responseType: "json",
      ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }),
      ...options, successCodes: false,
    });
    if (response.status < 200 || response.status >= 300) {
      const detail = response.response?.detail;
      throw new Error(typeof detail === "string" ? detail : (detail?.message || JSON.stringify(detail || `HTTP ${response.status}`)));
    }
    return response.response;
  },
  tags(item) { return item.getTags().map(tag => tag.tag); },
  alert(window, error) {
    Zotero.logError(error);
    if (!window?.closed && this.alive) Services.prompt.alert(window, this.name, error.message || String(error));
  },
  run(window, callback) { return () => Promise.resolve().then(callback).catch(error => this.alert(window, error)); },
};

async function startup({ id, rootURI } = {}) {
  await Zotero.initializationPromise;
  PaperLoom.rootURI = rootURI;
  Services.scriptloader.loadSubScript(rootURI + "content/table-excel.js", globalThis, "UTF-8");
  PaperLoom.Excel = globalThis.PaperLoomExcel;
  for (const name of ["runtime", "store", "service", "artifacts", "editing", "appearance", "tasks", "batch-picker", "source-regions", "comparison", "reader", "ui"]) {
    Services.scriptloader.loadSubScript(rootURI + `modules/${name}.js`, globalThis, "UTF-8");
  }
  PaperLoom.config = JSON.parse(await Zotero.File.getResourceAsync(rootURI + "config.json"));
  PaperLoom.readerStyle = await Zotero.File.getResourceAsync(rootURI + "content/reader.css");
  PaperLoom.chrome = Cc["@mozilla.org/addons/addon-manager-startup;1"].getService(Ci.amIAddonManagerStartup)
    .registerChrome(Services.io.newURI(rootURI + "manifest.json"), [["content", "paperloom", "content/"]]);
  PaperLoom.alive = true;
  await PaperLoom.Store.load();
  PaperLoom.UI.registerColumns();
  PaperLoom.ReaderUI.register();
  PaperLoom.Tasks.register();
  for (const window of Zotero.getMainWindows()) PaperLoom.UI.addWindow(window);
}
function onMainWindowLoad({ window }) { if (PaperLoom.alive) PaperLoom.UI.addWindow(window); }
function onMainWindowUnload({ window }) { PaperLoom.UI?.removeWindow(window); }
async function shutdown(reason) {
  PaperLoom.alive = false;
  PaperLoom.Tasks?.shutdown();
  PaperLoom.ReaderUI?.shutdown({ applicationShutdown: reason === 2 }); // APP_SHUTDOWN
  PaperLoom.UI?.shutdown();
  await PaperLoom.Service?.shutdown();
  await PaperLoom.Store?.flush();
  PaperLoom.chrome?.destruct();
}
function install() {}
function uninstall() {}
