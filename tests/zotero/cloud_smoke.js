const cloudStartup = startup;
startup = async function (...args) {
  const report = {version:Zotero.version,checks:[]};
  const check = (pass, label) => { if (!pass) throw new Error(label); report.checks.push(label); };
  try {
    await cloudStartup(...args);
    await Zotero.uiReadyPromise;
    const PL = PaperLoom, window = Zotero.getMainWindow();
    const runtime = await PL.Runtime.resolve();
    check(runtime.pinned && runtime.backend === "cloud", "Portable test package finds its embedded cloud runtime");
    PL.setPref("autoOCR", true);
    check(PL.Tasks.autoPref() === "cloudAutoOCR" && !PL.pref("cloudAutoOCR", false),
      "Existing local auto OCR does not upload documents");
    PL.setPref("serviceURL", smokeManagedURL);
    await PL.Service.ensure();
    check(PL.Service.owned && (await PL.request("GET","/api/zotero/status")).cloud_ocr,
      "Zotero starts the model-free cloud service");
    const parent = new Zotero.Item("journalArticle");
    parent.setField("title","Synthetic cloud test");
    await parent.saveTx();
    const pdf = await Zotero.Attachments.importFromFile({file:cloudPDFPath,parentItemID:parent.id,contentType:"application/pdf"});
    const saved = await PL.ocr(window,pdf);
    check(saved?.attachmentID && (await PL.Store.saved(pdf,"ocr"))?.attachmentID === saved.attachmentID,
      "Real AI Studio OCR is saved as a Zotero attachment");
    const file = await saved.attachment.getFilePathAsync();
    const page = await IOUtils.readUTF8(file);
    check(page.includes('data-block-id=') && page.includes('data-page="1"') && page.includes('data-bbox='),
      "Cloud output keeps the HTML reader and PDF comparison coordinates");
    const payload = await IOUtils.readJSON(PathUtils.join(PathUtils.parent(file),"editor.json"));
    check(payload.blocks.length > 0 && payload.document.parser.name === "paddleocr-vl-cloud",
      "Saved cloud result uses the normal editable block format");
    report.quitService={url:PL.serviceURL(),lease:PL.Service.leasePath,state:PL.Service.statePath,runnerPID:PL.Service.process.pid};
    report.ok=true;
  } catch (error) {
    report.ok=false; report.error=String(error); report.stack=error.stack;
  }
  await IOUtils.writeJSON(smokeResultPath,report);
  Services.startup.quit(Ci.nsIAppStartup.eForceQuit);
};
