const originalStartup = startup;
startup = async function (...args) {
  await Zotero.initializationPromise;
  let legacy;
  try { legacy = await seedStandaloneAttachments(); }
  catch (error) { legacy = {error}; }
  await originalStartup(...args);
  ChromeUtils.registerWindowActor("PaperLoomAnnotationSmoke", {allFrames:true, matches:["file:///*"],
    child:{esModuleURI:`resource://${PaperLoom.ReaderUI.actorResource}/annotation-smoke.mjs`}});
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
    await waitFor(() => settings.document.getElementById("mineruServerURL")?.value);
    settings.document.getElementById("tab-process").click();
    check(!settings.document.getElementById("saveLocal").hidden, "Recognition settings expose the processing save action");
    check(Array.from(settings.document.getElementById("recognitionMode").options, o => o.value).join() === "layout,local,cloud,mineru,mineru-local",
      "Native Zotero settings shows all discovery and MinerU choices");
    const setting = id => settings.document.getElementById(id);
    const localButton = window.document.getElementById("paperloom-start-service");
    const localMenu = window.document.getElementById("paperloom-local-ocr-control");
    check(localButton.hidden && localMenu.hidden, "Default PDF parsing hides both local OCR switches");
    for (const mode of ["cloud", "mineru", "mineru-local", "local"]) {
      await PL.UI.applyLocal({recognitionMode:mode});
      check(localButton.hidden === (mode !== "local") && localMenu.hidden === (mode !== "local"),
        "Local OCR switch visibility follows the saved " + mode + " default");
    }
    localButton.click();
    await waitFor(() => PL.LocalOCR.state.phase === "ready" && !PL.LocalOCRControl.warming);
    check((await PL.request("GET", "/api/testing/calls", undefined, {serviceURL:unifiedFixtureURL})).prewarmed === "official"
      && localButton.textContent === "断开本地 OCR", "Toolbar prewarm exercises the OCR engine before the first paper");
    localButton.click();
    await waitFor(() => PL.LocalOCR.state.phase === "stopped" && !PL.LocalOCRControl.closing);
    check(await PL.LocalOCR.online(unifiedFixtureURL), "Disconnecting in Zotero preserves a separately managed OCR service");
    const request = PL.request.bind(PL);
    PL.request = (method, path, ...args) => path === "/api/zotero/local-ocr/prewarm" ?
      Promise.reject(Object.assign(new Error("HTTP 404"), {status:404})) : request(method, path, ...args);
    try {
      await PL.LocalOCRControl.prewarm(window);
      check((await request("GET", "/api/testing/calls", undefined, {serviceURL:unifiedFixtureURL})).ocr === 1,
        "Older local service warms through its normal OCR queue with the one-page XPI resource");
    } finally { PL.request = request; }
    await PL.UI.applyLocal({recognitionMode:"layout"});
    const mineruURL = unifiedMineruURL || (unifiedFixtureURL + "/mineru-fixture");
    setting("mineruServerURL").value = mineruURL;
    setting("saveLocal").click();
    await waitFor(() => !setting("saveLocal").disabled);
    if (!setting("status").textContent.includes("设置已保存")) {
      throw new Error("保存 MinerU 设置失败：" + setting("status").textContent);
    }
    check((await IOUtils.readJSON(PL.Service.mineruConfigPath())).serverURL === mineruURL
      && !PL.pref(PL.Recognition.autoPref("mineru"), false), "MinerU settings save independently, with cloud auto-upload off");
    settings.close();
    await PL.Service.ensure();
    check(PL.Service.owned && (await PL.request("GET", "/api/zotero/status")).ocr_modes.join() === "layout,cloud,mineru,mineru-local",
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
    const modelSettings = PL.UI.settings(window);
    const field = id => modelSettings.document.getElementById(id);
    await waitFor(() => field("llm_concurrency")?.value === "3" && field("llm_api_key")?.placeholder.includes("无密钥"));
    for (const [id, value] of [["llm_base_url", unifiedFixtureURL + "/fixture/v1"], ["llm_model","fixture"], ["llm_temperature",""]]) {
      field(id).value = value;
      field(id).dispatchEvent(new modelSettings.Event("input",{bubbles:true}));
    }
    field("testModel").click();
    await waitFor(() => field("status").textContent.includes("测试通过"));
    field("tab-process").click();
    field("saveLocal").click();
    await waitFor(async () => (await PL.request("GET", "/api/settings")).llm_model === "fixture");
    check((await PL.request("GET","/api/settings")).llm_base_url === unifiedFixtureURL + "/fixture/v1",
      "Testing an edited configuration and clicking the main Save persists the same provider used by full-paper translation");
    modelSettings.close();
    const noPDF = new Zotero.Item("journalArticle");
    noPDF.setField("title", "Metadata-only smoke paper");
    await noPDF.saveTx();
    await window.ZoteroPane.selectItem(noPDF.id);
    const titleDialog = PL.BatchPicker.open(window, "translate-title");
    const titleRow = () => titleDialog.document.querySelector(`tr[data-source-id="item-${noPDF.id}"] input`);
    await waitFor(() => titleRow()?.checked && !titleRow().disabled);
    check(titleDialog.document.getElementById("translateTitleRow").hidden, "Title-only picker selects a Zotero item that has no PDF");
    titleDialog.document.getElementById("start").click();
    await waitFor(() => titleDialog.document.getElementById("notice").textContent.includes("成功 1 篇"));
    check(PL.TitleTranslations.column(noPDF) === "无 PDF 测试论文" &&
      !PL.Store.get(noPDF).ocr && !Object.keys(PL.Store.get(noPDF).translations).length,
      "Title-only action populates the title column without creating OCR or a full translation");
    const pickerField = id => titleDialog.document.getElementById(id);
    for (const [id, value] of [["sort", "recent"], ["statusFilter", "ready"], ["search", "无 PDF 测试论文"]]) {
      pickerField(id).value = value;
      pickerField(id).dispatchEvent(new titleDialog.Event(id === "search" ? "input" : "change", {bubbles:true}));
    }
    check(titleRow()?.checked && pickerField("papers").children.length === 1 &&
      pickerField("papers").textContent.includes("已翻译标题"),
      "Recent sorting and completed-title filtering preserve the checked metadata-only paper when searching its translated title");
    titleDialog.close();
    await window.ZoteroPane.selectItem(parent.id);
    const translateDialog = PL.BatchPicker.open(window, "translate");
    const fullRow = () => translateDialog.document.querySelector(`tr[data-source-id="${pdf.id}"] input`);
    await waitFor(() => fullRow()?.checked && !fullRow().disabled);
    const includeTitle = translateDialog.document.getElementById("translateTitle");
    check(!includeTitle.checked && !translateDialog.document.getElementById("translateTitleRow").hidden,
      "Batch translation offers an unchecked title option beside the action selector");
    includeTitle.click();
    translateDialog.document.getElementById("start").click();
    await waitFor(() => translateDialog.document.getElementById("notice").textContent.includes("成功 1 篇"));
    const translated = await PL.Store.saved(pdf, "translate");
    check(PL.TitleTranslations.column(parent) === "融合版测试论文" && parent.getField("title") === "Unified edition smoke fixture",
      "Combined batch saves the translated title separately from the Zotero source title");
    translateDialog.close();
    await waitFor(() => window.document.querySelector("#zotero-items-tree")?.textContent.includes("融合版测试论文"));
    const savedTitles = (await IOUtils.readJSON(PL.Store.path)).records;
    check(savedTitles[PL.Store.key(parent)].titles.zh.text === "融合版测试论文" &&
      savedTitles[PL.Store.key(noPDF)].titles.zh.text === "无 PDF 测试论文",
      "The item tree renders the new column and both titles are persisted outside the raw Zotero metadata");
    if (PL.config.semanticSearch) {
      window.document.getElementById("paperloom-batch-semantic").click();
      const dialog = () => PL.SemanticSearch.windows.get(window);
      const control = id => dialog()?.document.getElementById(id);
      await waitFor(() => control("build"));
      await waitFor(() => PL.Store.get(pdf).semanticIndexed === local.attachment.key, 90000);
      check(true, "OCR automatically indexes the current snapshot with local embeddings");
      control("query").value = "哪些文献用视觉语言模型识别科学公式与表格";
      control("search").click();
      await waitFor(() => control("results")?.querySelector("article"));
      const card = [...control("results").querySelectorAll("article")].find(node => node.textContent.includes("Unified edition smoke fixture"));
      const locate = card && [...card.querySelectorAll("button")].find(button => button.textContent === "定位原文");
      check(!!locate, "Real CPU embeddings find an English recognized passage with a Chinese query");
      await waitFor(() => card.textContent.includes("融合版测试论文") &&
        card.textContent.includes("文档阅读基准") && card.textContent.includes("benchmark for formula-aware document reading"));
      check(true, "Search displays the saved Chinese title and corresponding English and Chinese evidence");
      locate.click();
      await waitFor(() => [...PL.ReaderUI.states.values()].some(state =>
        state.reader.itemID === pdf.id && state.lastLocated?.page >= 1));
      check(true, "Search result opens the saved snapshot and locates its native PDF passage");
      dialog().close();
    }
    check(translated.ocrRevision === local.sourceRevision && translated.recognitionMode === "local" && translated.attachmentID,
      "Translation uses the saved local OCR snapshot via the core service");
    const children = await Zotero.Items.getAsync(parent.getAttachments());
    check([native.attachment, local.attachment, translated.attachment].every(item => item.parentItemID === parent.id) &&
      children.some(item => item.attachmentContentType === "text/markdown" && PL.tags(item).includes(`paperloom:pdf:${pdf.key}`)),
      "All new results on an existing paper, including translated Markdown, share its existing parent");
    const mineruPaper = await Zotero.Attachments.importFromFile({file:smokePDFPath, parentItemID:parent.id, contentType:"application/pdf"});
    const mineru = await PL.Tasks.enqueue(mineruPaper, "ocr", window, {mode:"mineru-local"});
    const mineruSource = await IOUtils.readJSON(PathUtils.join(PathUtils.parent(await mineru.attachment.getFilePathAsync()), "source.json"));
    const locatedMineruBlock = mineruSource.blocks.find(block => block.source.regions?.[0]?.bbox?.length === 4 &&
      (unifiedMineruURL ? /formula.aware/i.test(block.content.text) : block.content.text.includes("MinerU") && block.bbox[0] === .1));
    check(mineru.recognitionMode === "mineru-local" && mineru.documentID.startsWith("mineru-local-") && locatedMineruBlock,
      "Packaged MinerU adapter submits, downloads and saves an editable attachment with PDF coordinates");
    const mineruTranslation = await PL.Tasks.enqueue(mineruPaper, "translate", window);
    check(mineruTranslation.ocrRevision === mineru.sourceRevision && mineruTranslation.recognitionMode === "mineru-local",
      "MinerU attachment translates from its saved original snapshot");
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
    check(Array.from(state.recognitionSelect.options, option => option.value).join() === "layout,local,cloud,mineru,mineru-local" &&
      state.pane.querySelector('[data-action="sync-scroll"]') && state.pane.querySelector('[data-action="block-compare"]'),
      "Shared reader shows recognition, scroll sync and highlighting controls");
    check(state.saved.attachmentID === native.attachmentID, "Opening a historical attachment selects that exact recognition snapshot");
    const annotationActor = () => state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomAnnotationSmoke");
    async function annotate(selector, words, translated = false) {
      const actor = annotationActor();
      await actor.sendQuery("select", {selector, words});
      let rendered;
      await waitFor(async () => (rendered = await actor.sendQuery("palette")).point || rendered.status);
      if (!rendered.point) throw new Error(rendered.status);
      const entries = () => translated ? Object.values(PL.Store.get(pdf)?.translationMarks || {}) : pdf.getAnnotations();
      const existing = new Set(entries().map(item => item.key));
      const frame = state.frame.getBoundingClientRect(), {point} = rendered;
      window.windowUtils.sendMouseEvent("mousedown", frame.x+point.x+point.width/2, frame.y+point.y+point.height/2, 0, 1, 0);
      window.windowUtils.sendMouseEvent("mouseup", frame.x+point.x+point.width/2, frame.y+point.y+point.height/2, 0, 1, 0);
      await waitFor(async () => entries().some(item => !existing.has(item.key)) ||
        !!(rendered = await actor.sendQuery("palette")).status?.match(/失败|无法|can't|must|未保存|错误|not a function/i));
      const saved = entries().find(item => !existing.has(item.key));
      if (!saved) throw new Error(rendered.status);
      return saved;
    }
    await PL.ReaderUI.setMode(reader, "ocr");
    const exact = await annotate(".pl-target", "We present");
    check(exact.annotationText === "We present" && !exact.annotationComment &&
      JSON.parse(exact.annotationPosition).rects.length === 1 && state.mode === "ocr",
      "Recognition-only selection creates an exact native highlight without changing mode");
    await PL.ReaderUI.setMode(reader, "split");
    await PL.ReaderUI.showSaved(reader, translated, "translate");
    await waitFor(() => state.bridgeStatus === "ready");
    check(state.saved.attachmentID === translated.attachmentID && state.mode === "split",
      "The translated result opens in the same comparison reader");
    const blockIndex = await PL.ReaderUI.readerActor(state.frame.browsingContext.currentWindowGlobal).sendQuery("block-index");
    const regions = blockIndex.flatMap(b => (b.regions?.length ? b.regions : [{page:b.page,bbox:b.bbox}])
      .filter(r => r.bbox?.length === 4).map(r => ({...r, block:b.block})));
    const sample = regions.find(r => r.page === 1 && regions.filter(s => s.page === r.page &&
      (r.bbox[0]+r.bbox[2])/2 >= s.bbox[0] && (r.bbox[0]+r.bbox[2])/2 <= s.bbox[2] &&
      (r.bbox[1]+r.bbox[3])/2 >= s.bbox[1] && (r.bbox[1]+r.bbox[3])/2 <= s.bbox[3]).length === 1);
    const nativeContext = await PL.PDFContext.ready(state);
    const pageDiv = nativeContext.doc.querySelector('.page[data-page-number="1"]');
    const canvas = pageDiv.querySelector(".canvasWrapper canvas,.canvasWrapper") || pageDiv;
    const bounds = canvas.getBoundingClientRect();
    const point = {bubbles:true,button:0,clientX:bounds.left+(sample.bbox[0]+sample.bbox[2])*bounds.width/2,
      clientY:bounds.top+(sample.bbox[1]+sample.bbox[3])*bounds.height/2};
    state.lastLocated = null;
    pageDiv.dispatchEvent(new nativeContext.window.MouseEvent("click", Components.utils.cloneInto(point,nativeContext.window)));
    await waitFor(() => state.lastLocated?.block === sample.block);
    check(true, "A click on the real PDF.js page selects its corresponding translation block");
    await PL.ReaderUI.translationMode(reader,"translation");
    check(!PL.Store.get(legacy.source)?.translationView && PL.Store.get(pdf).translationView === "translation",
      "Only the current paper switches to translation-only");
    const count = pdf.getAnnotations().length;
    const note = await annotate(".pl-target", "文档阅读基准", true);
    check(note.target.quote === "文档阅读基准" && pdf.getAnnotations().length === count,
      "Chinese selection stays exclusively on the translation");
    await PL.ReaderUI.showSaved(reader, native, "ocr");
    check(!PL.ReaderAnnotations.list(state).some(entry => entry.key === note.key), "Chinese mark hidden on source");
    exact.annotationColor = "#ff6666";
    await exact.saveTx();
    await waitFor(() => annotationActor().sendQuery("has-highlight", "ff6666"));
    await exact.eraseTx();
    await waitFor(async () => !await annotationActor().sendQuery("has-highlight", "ff6666"));
    await PL.ReaderUI.showSaved(reader, translated, "translate");
    await waitFor(() => annotationActor().sendQuery("has-highlight","ffd400"));
    check(PL.ReaderAnnotations.list(state).some(entry => entry.key === note.key), "Translation highlight survives reopening");
    function clickContent(bounds) {
      const frame = state.frame.getBoundingClientRect(), x = frame.left + bounds.left + bounds.width / 2;
      const y = frame.top + bounds.top + bounds.height / 2;
      window.windowUtils.sendMouseEvent("mousedown", x, y, 0, 1, 0);
      window.windowUtils.sendMouseEvent("mouseup", x, y, 0, 1, 0);
    }
    const markPoint = await annotationActor().sendQuery("mark-point", {selector:".pl-target", words:"文档阅读基准"});
    clickContent(markPoint);
    let tools;
    await waitFor(async () => (tools = await annotationActor().sendQuery("palette")).remove);
    clickContent(tools.remove);
    await waitFor(() => !PL.ReaderAnnotations.list(state).some(entry => entry.key === note.key));
    check(!pdf.getAnnotations().some(item => item.key === note.key), "Clicking a saved translated mark reopens its tools and deletes it locally");
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
