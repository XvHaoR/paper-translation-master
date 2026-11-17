const originalStartup = startup;
startup = async function (...args) {
  await Zotero.initializationPromise;
  let legacy;
  try { legacy = await seedStandaloneAttachments(); }
  catch (error) { legacy = {error}; }
  await originalStartup(...args);
  setTimeout(() => runUnifiedSmoke(legacy), 0);
};
async function seedStandaloneAttachments() {
  await Zotero.Libraries.userLibrary.waitForDataLoad("item");
  const collection = new Zotero.Collection();
  collection.name = "Existing attachments collection";
  await collection.saveTx();
  const otherCollection = new Zotero.Collection();
  otherCollection.name = "Existing results collection";
  await otherCollection.saveTx();
  const source = await Zotero.Attachments.importFromFile({file:smokePDFPath, contentType:"application/pdf",
    title:"Standalone fixture.pdf", collections:[collection.id]});
  const annotation = await Zotero.Annotations.saveFromJSON(source, {
    key:Zotero.DataObjectUtilities.generateKey(), type:"highlight", text:"Smoke-test annotation", comment:"",
    color:"#ffd400", pageLabel:"1", sortIndex:"00000|000000|00000", position:{pageIndex:0, rects:[[10, 10, 30, 20]]},
  });
  const directory = PathUtils.parent(smokeResultPath);
  const html = PathUtils.join(directory, "legacy.html");
  const markdown = PathUtils.join(directory, "legacy.md");
  await IOUtils.writeUTF8(html, "<!doctype html><p>Historical recognition</p>");
  await IOUtils.writeUTF8(markdown, "Historical translation");
  const specs = [[html, "text/html", "ocr"], [html, "text/html", "reader"], [markdown, "text/markdown", "markdown"]];
  const results = [];
  for (const [file, contentType, kind] of specs) {
    const item = await Zotero.Attachments.importFromFile({file, contentType, collections:[otherCollection.id]});
    item.addTag(`paperloom:${kind}`);
    item.addTag(`paperloom:pdf:${source.key}`);
    await item.saveTx();
    results.push({item, key:item.key, path:await item.getFilePathAsync()});
  }
  const unrelated = await Zotero.Attachments.importFromFile({file:html, contentType:"text/html"});
  unrelated.addTag("paperloom:ocr");
  unrelated.addTag(`paperloom:pdf:${source.key}`);
  unrelated.addTag("paperloom:pdf:ABCD2345");
  await unrelated.saveTx();
  const path = await source.getFilePathAsync();
  return {source, key:source.key, path, hash:await Zotero.Utilities.Internal.md5Async(path), collection, otherCollection,
    annotation, results, unrelated};
}
async function runUnifiedSmoke(legacy) {
  const report = {version:Zotero.version, checks:[]};
  function check(ok, name) {
    if (!ok) throw new Error(name);
    report.checks.push(name);
    Zotero.debug(`Unified smoke: ${name}`);
  }
  async function waitFor(predicate, timeout = 25000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await predicate()) return;
      await Zotero.Promise.delay(100);
    }
    throw new Error("Timed out waiting for Zotero");
  }
  try {
    await Zotero.uiReadyPromise;
    const PL = PaperLoom, window = Zotero.getMainWindow();
    if (legacy.error) throw legacy.error;
    const grouped = await Zotero.Items.getAsync(legacy.source.parentItemID);
    check(grouped?.isRegularItem() && grouped.getField("title") === "Standalone fixture" &&
      grouped.getCollections().includes(legacy.collection.id) && grouped.getCollections().includes(legacy.otherCollection.id),
      "Addon startup groups existing results and preserves both original Zotero collections");
    check(legacy.source.key === legacy.key && await legacy.source.getFilePathAsync() === legacy.path &&
      await Zotero.Utilities.Internal.md5Async(legacy.path) === legacy.hash &&
      legacy.source.getAnnotations().some(item => item.id === legacy.annotation.id),
      "Grouping keeps the original PDF key, path, bytes, and Zotero annotation");
    for (const {item, key, path} of legacy.results) {
      check(item.parentItemID === grouped.id && item.key === key && await item.getFilePathAsync() === path,
        `Existing ${item.attachmentContentType} result keeps its key and path under the shared parent`);
    }
    check(!legacy.unrelated.parentItemID && PL.Store.get(legacy.source)?.parentID === grouped.id &&
      PL.Store.get(legacy.source).ocr.attachmentID === legacy.results[0].item.id,
      "Ambiguous source links stay ungrouped and recognized status follows the new parent");
    const snapshots = (await IOUtils.getChildren(PL.Store.directory)).filter(path => PathUtils.filename(path).startsWith("attachment-groups-"));
    check(snapshots.length === 1 && (await IOUtils.readJSON(snapshots[0])).items.some(item =>
      item.key === legacy.key && item.parentID === null && item.collections.includes(legacy.collection.id)),
      "Previous hierarchy is recorded in the isolated Zotero profile");
    const repeated = await PL.AttachmentGroups.migrateExisting();
    check(repeated.parentsCreated === 0 && repeated.resultsMoved === 0 && repeated.errors === 0,
      "Repeating startup migration creates no duplicate parent or moves");
    PL.Service.configuredURL = () => smokeManagedURL;
    PL.setPref("unifiedLocalURL", unifiedFixtureURL);
    PL.setPref("autoOCR", true);
    check(PL.Recognition.defaultMode() === "layout" && !PL.pref(PL.Tasks.autoPref(), false),
      "Fresh profile defaults to layout and leaves legacy automation disabled");
    const settings = PL.UI.settings(window);
    await waitFor(() => settings.document.getElementById("recognitionMode"));
    check(Array.from(settings.document.getElementById("recognitionMode").options).length === 3,
      "Native Zotero settings shows all three recognition choices");
    settings.close();
    await PL.Service.ensure();
    check(PL.Service.owned && (await PL.request("GET", "/api/zotero/status")).ocr_modes.join() === "layout,cloud",
      "Packaged core service starts from an isolated descriptor");
    const coreURL = PL.Service.url();
    const parent = new Zotero.Item("journalArticle");
    parent.setField("title", "Unified edition smoke fixture");
    await parent.saveTx();
    const pdf = await Zotero.Attachments.importFromFile({file:smokePDFPath, parentItemID:parent.id, contentType:"application/pdf"});
    const native = await PL.Tasks.enqueue(pdf, "ocr", window, {mode:"layout"});
    check(native.recognitionMode === "layout" && native.sourceRevision && native.attachmentID,
      "Real PyMuPDF layout is stored as a Zotero attachment");
    const local = await PL.Tasks.enqueue(pdf, "ocr", window, {mode:"local"});
    check(local.recognitionMode === "local" && local.attachmentID !== native.attachmentID &&
      await native.attachment.getFilePathAsync() && PL.LocalOCR.url() === unifiedFixtureURL,
      "Local OCR routes to its own compatible service and preserves the layout attachment");
    check(PL.Service.url() === coreURL && PL.Recognition.defaultMode() === "layout",
      "One-paper choice does not change the core service or the default scheme");
    await PL.request("PUT", "/api/settings", {llm_base_url:unifiedFixtureURL + "/fixture/v1",llm_model:"fixture",llm_temperature:null});
    const translated = await PL.Tasks.enqueue(pdf, "translate", window);
    check(translated.ocrRevision === local.sourceRevision && translated.recognitionMode === "local" && translated.attachmentID,
      "Translation uses the saved local OCR snapshot via the core service");
    const children = await Zotero.Items.getAsync(parent.getAttachments());
    check([native.attachment, local.attachment, translated.attachment].every(item => item.parentItemID === parent.id) &&
      children.some(item => item.attachmentContentType === "text/markdown" && PL.tags(item).includes(`paperloom:pdf:${pdf.key}`)),
      "All new results on an existing paper, including translated Markdown, share its existing parent");
    if (unifiedCloudPDF) {
      const fixture = await Zotero.Attachments.importFromFile({file:unifiedCloudPDF, parentItemID:parent.id, contentType:"application/pdf"});
      const cloud = await PL.Tasks.enqueue(fixture, "ocr", window, {mode:"cloud"});
      check(cloud.recognitionMode === "cloud" && cloud.sourceRevision && cloud.documentID.startsWith("cloud-"),
        "The same XPI runs real AI Studio OCR and saves its PDF regions");
    }
    const standalone = await Zotero.Attachments.importFromFile({file:smokePDFPath, contentType:"application/pdf", title:"Fresh standalone PDF"});
    const standaloneResult = await PL.Tasks.enqueue(standalone, "ocr", window, {mode:"layout"});
    check(standalone.parentItemID && standaloneResult.attachment.parentItemID === standalone.parentItemID,
      "Saving the first result of a standalone PDF automatically creates a single shared parent");
    await Promise.race([PL.Tasks.runItems(window, "open-ocr", [native.attachment]), Zotero.Promise.delay(50000).then(() => {
      throw new Error("Opening the saved recognition result in Zotero timed out");
    })]);
    const reader = Zotero.Reader._readers.find(value => value.itemID === pdf.id && !value._isTabClosed);
    const state = PL.ReaderUI.states.get(reader);
    await waitFor(() => state?.controls?.isConnected && state.mode === "split" && state.bridgeStatus === "ready");
    check(state.recognitionSelect?.options.length === 3 &&
      state.pane.querySelector('[data-action="sync-scroll"]') && state.pane.querySelector('[data-action="block-compare"]'),
      "Shared reader shows recognition, scroll sync and highlighting controls");
    check(state.saved.attachmentID === native.attachmentID, "Opening a historical attachment selects that exact recognition snapshot");
    await PL.ReaderUI.showSaved(reader, translated, "translate");
    await waitFor(() => state.bridgeStatus === "ready");
    check(state.saved.attachmentID === translated.attachmentID && state.mode === "split",
      "The translated result opens in the same comparison reader");
    if (reader.tabID) window.Zotero_Tabs.close(reader.tabID);
    await PL.Store.flush();
    await Zotero.DB.waitForTransaction();
    report.quitService = {url:PL.Service.url()};
    report.ok = true;
  } catch (error) {
    report.ok = false;
    report.error = String(error);
    report.stack = error.stack;
  }
  await IOUtils.writeJSON(smokeResultPath, report);
  Services.startup.quit(Ci.nsIAppStartup.eForceQuit);
}
