const smokeStartup = startup;
startup = async function (...args) {
  try { await smokeStartup(...args); }
  catch (error) {
    await IOUtils.writeUTF8(smokeResultPath, JSON.stringify({ok:false,error:String(error),stack:error.stack}));
    Services.startup.quit(Ci.nsIAppStartup.eForceQuit);
    return;
  }
  const resources = Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler);
  resources.setSubstitution("paperloom-smoke", Services.io.newURI(args[0].rootURI));
  ChromeUtils.registerWindowActor("PaperLoomSmoke", {allFrames:true, matches:["file:///*"],
    child:{esModuleURI:"resource://paperloom-smoke/smoke-actor.mjs"}});
  setTimeout(async () => {
    const PL = PaperLoom;
    const report = {version:Zotero.version,checks:[]};
    const check = (ok, name) => { if (!ok) throw new Error(name); report.checks.push(name); Zotero.debug("PaperLoom smoke: " + name); };
    const waitFor = async (predicate, timeout=30000) => {
      const deadline=Date.now()+timeout;
      while(Date.now()<deadline) { const value=await predicate(); if(value) return value; await Zotero.Promise.delay(100); }
      throw new Error("Timed out waiting for test condition");
    };
    const screenshot = async (window, path) => {
      await Zotero.Promise.delay(250);
      const bitmap=await window.browsingContext.currentWindowGlobal.drawSnapshot(null,1,"white");
      const canvas=window.document.createElementNS("http://www.w3.org/1999/xhtml","canvas");
      canvas.width=bitmap.width;canvas.height=bitmap.height;
      canvas.getContext("2d").drawImage(bitmap,0,0);
      const bytes=Uint8Array.from(atob(canvas.toDataURL("image/png").split(",")[1]),c=>c.charCodeAt(0));
      await IOUtils.write(path,bytes);
    };
    const clipboardText=()=>{
      for(const flavor of ["text/plain","text/unicode"]){
        try{
          const trans=Cc["@mozilla.org/widget/transferable;1"].createInstance(Ci.nsITransferable);
          trans.init(null);trans.addDataFlavor(flavor);
          Services.clipboard.getData(trans,Services.clipboard.kGlobalClipboard);
          const value={};trans.getTransferData(flavor,value);
          return value.value.QueryInterface(Ci.nsISupportsString).data;
        }catch(_){}
      }
      return "";
    };
    try {
      await Zotero.uiReadyPromise;
      const window = Zotero.getMainWindow();
      window.resizeTo(1700,1000);
      const oldLocalAppData=Services.env.get("LOCALAPPDATA"),oldRuntimeDirectory=PL.config.runtimeDirectory,oldProjectRoot=PL.config.projectRoot;
      const runtimeBase=PathUtils.parent(smokeResultPath),runtimeDirectory="test-installed-runtime";
      const runtimeFile=PathUtils.join(runtimeBase,runtimeDirectory,"runtime.json");
      await IOUtils.makeDirectory(PathUtils.parent(runtimeFile),{ignoreExisting:true});
      await IOUtils.writeJSON(runtimeFile,{schema:1,projectRoot:oldProjectRoot,pythonPath:smokePythonPath,backend:"local",
        environment:{PAPERBRIDGE_WORKSPACE:PathUtils.join(runtimeBase,"installed-workspace")}});
      try {
        Services.env.set("LOCALAPPDATA",runtimeBase);PL.config.runtimeDirectory=runtimeDirectory;PL.config.projectRoot="";
        const installed=await PL.Runtime.resolve();
        check(installed.projectRoot===oldProjectRoot&&installed.pythonPath===smokePythonPath&&installed.descriptor===runtimeFile,
          "portable plugin discovers installed engine and Python without manual paths");
      } finally {
        Services.env.set("LOCALAPPDATA",oldLocalAppData);PL.config.runtimeDirectory=oldRuntimeDirectory;PL.config.projectRoot=oldProjectRoot;
      }
      // Editor workers and the fake API must use the same disposable cache.
      // Runtime resolution otherwise falls back to the developer workspace.
      const resolveRuntime=PL.Runtime.resolve.bind(PL.Runtime);
      PL.Runtime.resolve=async()=>{
        const runtime=await resolveRuntime();
        return {...runtime,environment:{...runtime?.environment,PAPERBRIDGE_WORKSPACE:smokeWorkspacePath}};
      };
      PL.UI.addWindow(window);
      check(!!window.document.getElementById("paperloom-item-menu"),"item OCR and translation commands");
      const translationMenu=Array.from(window.document.getElementById("paperloom-item-menu").querySelectorAll("menuitem")).map(node=>node.getAttribute("label"));
      check(translationMenu.some(label=>label.startsWith("删除当前语言译文"))&&translationMenu.some(label=>label.startsWith("翻译选中文献"))&&!translationMenu.some(label=>label.includes("升级")),"translation menu keeps delete and translate without an upgrade action");
      check(!!window.document.getElementById("paperloom-service-toolbar"),"main toolbar service control");
      check(!!window.document.getElementById("paperloom-batch-ocr")&&!!window.document.getElementById("paperloom-batch-translate"),"batch OCR and translation controls are directly visible in the library toolbar");
      check(PL.UI.columns.filter(Boolean).length===2,"native OCR and translation status columns");
      const settings=PL.UI.settings(window);
      await waitFor(()=>settings.document.getElementById("saveModel"));
      check(!!settings.document.getElementById("autoOCR"),"native automatic OCR and translation settings");
      settings.close();

      // Start and stop a real Python API process from Zotero. This test uses the
      // local backend without loading models, in its own workspace and port.
      const configuredURL=PL.pref("serviceURL","");
      PL.setPref("serviceURL",smokeManagedURL);
      PL.setPref("backend","local");
      PL.setPref("pythonPath",smokePythonPath);
      const project=PL.Service.project;
      PL.Service.project=async()=>{throw new Error("Startup failure fixture");};
      window.document.getElementById("paperloom-start-service").click();
      await waitFor(()=>PL.Service.state.phase==="failed"&&!PL.Service.starting);
      check(window.document.getElementById("paperloom-startup-progress").hidden&&!window.document.getElementById("paperloom-start-service").disabled,"startup failure hides progress and enables retry without a modal");
      check(window.document.getElementById("paperloom-startup-label").textContent.includes("Startup failure fixture"),"startup error appears inline beside retry button");
      PL.Service.project=project;
      const starting=PL.Service.ensure();
      check(!window.document.getElementById("paperloom-startup-progress").hidden,"startup progress appears inside Zotero");
      await starting;
      check(PL.Service.state.phase==="ready","hidden Python service starts from Zotero");
      const ownedPID=PL.Service.process.pid,ownedLease=PL.Service.instance,online=PL.Service.online;
      PL.Service.online=async()=>{throw new Error("Busy API probe must not restart an owned model");};
      await PL.Service.ensure();await PL.Service.ensure();
      PL.Service.online=online;
      check(PL.Service.process.pid===ownedPID&&PL.Service.instance===ownedLease,"successive jobs reuse the owned API and lease without probing or restarting its model");
      check(PL.serviceURL()!==smokeManagedURL,"occupied old-service port switches automatically to a free port");
      check((await Zotero.HTTP.request("GET",smokeManagedURL,{responseType:"json"})).response.legacy===true,"existing listener remains untouched after port fallback");
      check(window.document.getElementById("paperloom-startup-progress").hidden,"startup progress disappears when ready");
      await PL.Service.stop();
      check(PL.Service.state.phase==="stopped","owned service stops inside Zotero");
      PL.setPref("serviceURL",configuredURL);
      await PL.Service.ensure();

      const parent=new Zotero.Item("journalArticle");
      parent.setField("title","PaperLoom integrated reader fixture");
      await parent.saveTx();
      const pdf=await Zotero.Attachments.importFromFile({file:smokePDFPath,parentItemID:parent.id,contentType:"application/pdf",title:"Fixture PDF"});
      check(PL.Store.label(parent,"ocr")==="未 OCR","unprocessed paper status");
      const ocr=await PL.ocr(window,pdf);
      check(!!ocr.attachment,"OCR saves independently of translation");
      const calls=await PL.request("GET","/api/testing/calls");
      check(calls.llm===0,"OCR never calls a translation model");
      check(PL.Store.label(parent,"ocr")==="已 OCR" && PL.Store.label(parent,"translate")==="未翻译","OCR and translation statuses stay independent");
      PL.setPref("blockCompare",false);
      const ocrOnlyReader=await PL.ReaderUI.open(pdf);
      await PL.ReaderUI.setMode(ocrOnlyReader,"split");
      const ocrOnlyState=PL.ReaderUI.states.get(ocrOnlyReader);
      await waitFor(()=>ocrOnlyState.exportSchema>=5);
      check(!ocrOnlyState.pane.querySelector('[data-action="repair"]')
        &&!translationMenu.some(label=>label.includes("修复"))
        &&!ocrOnlyState.heading.textContent.includes("启用"),"fresh OCR supports comparison without repair or enable controls");
      const ocrOnlyActor=ocrOnlyState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      await waitFor(async()=>(await ocrOnlyActor.sendQuery("state")).bridge==="true");
      const focusButton=ocrOnlyState.pane.querySelector('[data-action="block-compare"]');
      const clickFocus=()=>{
        const rect=focusButton.getBoundingClientRect();
        window.windowUtils.sendMouseEvent("mousedown",rect.x+rect.width/2,rect.y+rect.height/2,0,1,0);
        window.windowUtils.sendMouseEvent("mouseup",rect.x+rect.width/2,rect.y+rect.height/2,0,1,0);
      };
      check(focusButton.textContent==="开启段落高亮","previously saved off preference is clearly labelled rather than appearing broken");
      clickFocus();
      await waitFor(()=>PL.pref("blockCompare",false)&&focusButton.textContent==="关闭段落高亮");
      const ocrLayout=(await ocrOnlyActor.sendQuery("state")).layout;
      check(ocrLayout.compactStyle&&ocrLayout.gap>=0&&ocrLayout.gap<20,"new OCR paragraphs use compact spacing while retaining separate clickable blocks");
      const compatibility=await ocrOnlyActor.sendQuery("brand-contract");
      check(compatibility.oldActorRecognizes&&compatibility.protocol==="paperloom.saved-reader/1",
        "new exports retain the old-client handshake marker alongside an independent protocol");
      await ocrOnlyActor.sendQuery("brand-contract",{rename:true});
      await PL.ReaderUI.readerActor(ocrOnlyState.frame.browsingContext.currentWindowGlobal).sendQuery("reader-connect");
      await waitFor(async()=>{report.readerRuntime=await ocrOnlyActor.sendQuery("state");return report.readerRuntime.bridge==="true"&&report.readerRuntime.mathCopyButtons>0;});
      check(!(await ocrOnlyActor.sendQuery("brand-contract")).oldActorRecognizes,"native reader reconnects after visible brand and legacy label are removed");
      await ocrOnlyActor.sendQuery("click",{selector:'main .pl-target [data-latex] [data-action="copy-latex"]'});
      await waitFor(()=>clipboardText().includes("E=mc^2"));
      check(!await PL.Store.saved(pdf,"translate"),"OCR-only formula copies raw LaTeX before a translation has ever existed");
      const ocrPosition=(await ocrOnlyActor.sendQuery("state")).firstPosition;
      await ocrOnlyActor.sendQuery("click",{selector:`[data-block-id="${ocrPosition.block}"] [data-action="locate"]`});
      await waitFor(()=>ocrOnlyState.lastLocated?.block===ocrPosition.block&&ocrOnlyState.pdfHighlight?.isConnected);
      check(true,"OCR-only paragraph highlights its source PDF before a translation has ever existed");
      clickFocus();
      await waitFor(()=>!PL.pref("blockCompare",true)&&!ocrOnlyState.pdfHighlight&&focusButton.textContent==="开启段落高亮");
      ocrOnlyState.lastLocated=null;
      await ocrOnlyActor.sendQuery("click",{selector:`[data-block-id="${ocrPosition.block}"] [data-action="locate"]`});
      await waitFor(()=>ocrOnlyState.lastLocated?.block===ocrPosition.block&&!ocrOnlyState.pdfHighlight);
      check(true,"OCR-to-PDF navigation remains available with focus highlighting disabled");
      const ocrPage=ocrOnlyReader._internalReader._primaryView._iframeWindow.document.querySelector(`.page[data-page-number="${ocrPosition.page}"]`);
      await ocrOnlyActor.sendQuery("clear-selection");
      // A synthetic click has no preceding mousedown to collapse the native
      // text selection created by reader.navigate(). Match a normal click.
      ocrPage.ownerDocument.defaultView.getSelection().removeAllRanges();
      const ocrBounds=ocrPage.getBoundingClientRect();
      report.ocrReverse={mode:ocrOnlyState.mode,compare:PL.pref("blockCompare",true),collapsed:ocrPage.ownerDocument.defaultView.getSelection().isCollapsed,position:ocrPosition,bounds:ocrBounds.toJSON()};
      ocrPage.ownerDocument.addEventListener("click",event=>{report.ocrReverse.seen={target:event.target.className,collapsed:ocrPage.ownerDocument.defaultView.getSelection().isCollapsed};},{capture:true,once:true});
      const reverseActor=PL.ReaderUI.readerActor;
      PL.ReaderUI.readerActor=global=>{
        const actor=reverseActor.call(PL.ReaderUI,global);
        return {sendQuery:async(name,data)=>{if(name==="locate-point")report.ocrReverse.query=data;const result=await actor.sendQuery(name,data);if(name==="locate-point")report.ocrReverse.result=result;return result;}};
      };
      const pdfMouse=window.windowUtils;
      const pdfFrame=ocrOnlyReader._internalReader._primaryView._iframe.getBoundingClientRect();
      const outerFrame=ocrOnlyReader._iframe.getBoundingClientRect();
      const ocrX=outerFrame.left+pdfFrame.left+ocrBounds.left+(ocrPosition.bbox[0]+ocrPosition.bbox[2])/2*ocrBounds.width;
      const ocrY=outerFrame.top+pdfFrame.top+ocrBounds.top+(ocrPosition.bbox[1]+ocrPosition.bbox[3])/2*ocrBounds.height;
      pdfMouse.sendMouseEvent("mousedown",ocrX,ocrY,0,1,0);
      pdfMouse.sendMouseEvent("mouseup",ocrX,ocrY,0,1,0);
      await waitFor(async()=>{const content=await ocrOnlyActor.sendQuery("state");report.ocrReverse.selected=content.selectedBlock;return content.selectedBlock===ocrPosition.block;},5000);
      PL.ReaderUI.readerActor=reverseActor;
      check(true,"source PDF locates its OCR-only block without a saved translation");
      check(!PL.pref("blockCompare",true)&&!ocrOnlyState.pdfHighlight,"PDF-to-OCR navigation remains available with focus highlighting disabled");
      clickFocus();
      await waitFor(()=>PL.pref("blockCompare",false)&&ocrOnlyState.pdfHighlight?.isConnected);
      check(focusButton.textContent==="关闭段落高亮","enabling focus restores the current region without needing another paragraph click");
      await PL.ReaderUI.setMode(ocrOnlyReader,"original");
      let translated=await PL.translate(window,pdf);
      check(!!translated.attachment,"manual translation saves durable result");
      check(PL.Store.label(parent,"translate")==="已翻译","parent item translation status");
      const translationPath=await translated.attachment.getFilePathAsync();
      check((await IOUtils.readUTF8(PathUtils.join(PathUtils.parent(translationPath),"translation.md"))).includes("中文译文"),"saved translated Markdown");
      PL.setPref("language","en");
      const english=await PL.translate(window,pdf);
      PL.setPref("language","zh");
      const revision=await PL.request("POST","/api/testing/revise-ocr");
      const currentOCR=await PL.Tasks.enqueue(pdf,"ocr",window,{force:true});
      const currentOCRPath=await currentOCR.attachment.getFilePathAsync();
      const currentOCRManifest=await IOUtils.readJSON(PathUtils.join(PathUtils.parent(currentOCRPath),"manifest.json"));
      await PL.Tasks.clearTranslation(pdf);
      await PL.Store.reconcile();
      check(!await PL.Store.saved(pdf,"translate")&&!!await PL.Store.saved(pdf,"ocr")&&!!await PL.Store.saved(pdf,"translate","en")&&!!await pdf.getFilePathAsync(),"clear current-language translation preserves OCR, PDF and other languages");
      check(translated.attachment.deleted,"cleared saved translation moves to Zotero trash");
      const beforeTranslate=await PL.request("GET","/api/testing/calls");
      translated=await PL.translate(window,pdf);
      const afterTranslate=await PL.request("GET","/api/testing/calls");
      check(afterTranslate.llm>beforeTranslate.llm,"translation after clearing ignores the old artifact and unit caches");
      const currentTranslationPath=await translated.attachment.getFilePathAsync();
      const currentTranslationManifest=await IOUtils.readJSON(PathUtils.join(PathUtils.parent(currentTranslationPath),"manifest.json"));
      check(currentTranslationManifest.source_sha256===currentOCRManifest.source_sha256
        &&(await IOUtils.readUTF8(PathUtils.join(PathUtils.parent(currentTranslationPath),"translation.md"))).includes(revision.marker)
        &&afterTranslate.ocr===beforeTranslate.ocr,"delete then translate uses the newly recognized OCR text without recognizing again");

      // New attachment events must trigger only OCR and avoid recursive helper imports.
      PL.setPref("autoOCR",true);
      const automatic=await Zotero.Attachments.importFromFile({file:smokePDFPath,parentItemID:parent.id,contentType:"application/pdf",title:"Automatic fixture"});
      await waitFor(()=>PL.Store.get(automatic)?.ocr?.state==="ready");
      check(!Object.keys(PL.Store.get(automatic).translations).length,"new PDF auto OCR does not auto translate");
      PL.setPref("autoOCR",false);

      await window.ZoteroPane.selectItem(parent.id);
      report.libraryToolbar=Array.from(window.document.querySelectorAll("#zotero-title-bar,#tab-bar-container,#zotero-tabs-toolbar,#paperloom-service-toolbar,#paperloom-start-service")).map(node=>({id:node.id,rect:node.getBoundingClientRect().toJSON(),display:window.getComputedStyle(node).display,hidden:node.hidden}));
      check(window.document.getElementById("paperloom-start-service").getBoundingClientRect().width>20,"service control remains visible after selecting a library item");
      const columns=window.ZoteroPane.itemsView._getColumns();
      check(PL.UI.columns.every(key=>columns.some(column=>column.dataKey===key&&!column.hidden)),"status columns visible by default in the native library");
      const picker=window.document.createXULElement("menupopup");
      window.ZoteroPane.itemsView.buildColumnPickerMenu(picker);
      check(["OCR 状态","翻译状态"].every(label=>Array.from(picker.querySelectorAll("menuitem")).some(item=>item.getAttribute("label")===label)),"status options in Zotero native column chooser");
      await waitFor(()=>window.document.querySelector("#zotero-items-tree")?.textContent.includes("已 OCR")&&window.document.querySelector("#zotero-items-tree")?.textContent.includes("已翻译"));
      check(true,"saved statuses rendered in library item row");
      try {await screenshot(window,smokeLibraryScreenshotPath);report.libraryScreenshot=smokeLibraryScreenshotPath;}
      catch(error){report.libraryScreenshotError=String(error);}

      const reader=await Zotero.Reader.open(pdf.id);
      await reader._initPromise;
      await waitFor(()=>reader._iframeWindow?.document.querySelector(".paperloom-reader-controls"));
      check(true,"OCR view controls in the original PDF toolbar");
      const state=PL.ReaderUI.states.get(reader);
      const pdfReady=async()=>{
        const view=reader._internalReader?._primaryView;
        const app=view?._iframeWindow?.PDFViewerApplication;
        report.nativePDF={pages:app?.pdfDocument?.numPages,uri:view?._iframe?.contentDocument?.documentURI,
          renderingStates:app?.pdfViewer?._pages?.map(page=>page.renderingState),
          iframeCount:state.doc.querySelectorAll('#primary-view iframe').length,
          canvas:!!view?._iframeWindow?.document?.querySelector('.page canvas'),
          primary:state.doc.getElementById('primary-view')?.innerHTML?.slice(0,700)};
        return app?.pdfDocument?.numPages>0 && !!view?._iframeWindow?.document?.querySelector('.page canvas');
      };
      await waitFor(pdfReady);
      check(true,"native source PDF really loads and renders a page canvas before OCR view");
      const chooseMode=async mode=>{
        const button=state.doc.querySelector(`.paperloom-mode-button[data-mode="${mode}"]`);
        const rect=button.getBoundingClientRect();
        const frame=reader._iframe.getBoundingClientRect();
        const utils=reader._window.windowUtils;
        report.modeClicks ||= [];
        const seen=[];
        for(const type of ["mousedown","mouseup","click"]) button.addEventListener(type,event=>seen.push({type,detail:event.detail,button:event.button}),true);
        report.modeClicks.push({mode,rect:rect.toJSON(),frame:frame.toJSON(),seen});
        utils.sendMouseEvent("mousedown",frame.left+rect.left+rect.width/2,frame.top+rect.top+rect.height/2,0,1,0);
        utils.sendMouseEvent("mouseup",frame.left+rect.left+rect.width/2,frame.top+rect.top+rect.height/2,0,1,0);
        await waitFor(()=>state.mode===mode);
      };
      await chooseMode("split");
      await waitFor(pdfReady);
      check(true,"native PDF remains rendered after entering comparison view");
      check(true,"real mouse click on the direct view button selects split view");
      const native=state.doc.getElementById("primary-view");
      await Zotero.Promise.delay(200);
      check(native.getBoundingClientRect().width>100 && state.pane.getBoundingClientRect().width>100,"PDF and OCR shown side by side in one tab");
      check(native.getBoundingClientRect().left<state.pane.getBoundingClientRect().left,"default original left and OCR right");
      const frameState=await waitFor(async()=>{
        const global=state.frame?.browsingContext?.currentWindowGlobal;
        report.frameURI=global?.documentURI?.spec;
        report.browserURI=state.frame?.currentURI?.spec;
        if(global?.documentURI?.scheme!=="file") return false;
        const data=await global.getActor("PaperLoomSmoke").sendQuery("state");
        report.frameState=data;
        return data.hasMain&&data.mathCount ? data : false;
      });
      check(frameState.mathCount>0,"offline OCR formula typesetting in embedded panel");
      const clickPane=async action=>{
        const button=state.pane.querySelector(`[data-action="${action}"]`);
        const rect=button.getBoundingClientRect();
        reader._window.windowUtils.sendMouseEvent("mousedown",rect.x+rect.width/2,rect.y+rect.height/2,0,1,0);
        reader._window.windowUtils.sendMouseEvent("mouseup",rect.x+rect.width/2,rect.y+rect.height/2,0,1,0);
      };
      await clickPane("font-larger");
      await waitFor(async()=>(await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state")).bodyFont==="18px");
      check(PL.pref("ocrFontSize",0)===18,"actual OCR font larger button changes rendered text");
      await clickPane("font-smaller");
      await waitFor(async()=>(await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state")).bodyFont==="17px");
      check(true,"actual OCR font smaller button changes rendered text");
      state.fontSize.value="23";
      state.fontSize.dispatchEvent(new reader._window.Event("input",{bubbles:true}));
      await waitFor(async()=>(await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state")).bodyFont==="23px",3000);
      check(PL.pref("ocrFontSize",0)===23,"font number input changes text immediately without losing focus");
      await clickPane("font-reset");
      await waitFor(async()=>(await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state")).bodyFont==="17px");
      check(state.fontSize.value==="17","font reset button restores the default size and input");
      await PL.ReaderUI.setFont(22);
      const fontState=await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state");
      check(fontState.bodyFont==="22px" && PL.pref("ocrFontSize",0)===22,"OCR font size changes and preference persists");
      await PL.ReaderUI.setFont(17);
      PL.ReaderUI.swap(reader);
      check(native.getBoundingClientRect().left>state.pane.getBoundingClientRect().left,"swap original and OCR panel positions");
      await chooseMode("ocr");
      check(state.doc.defaultView.getComputedStyle(native).display==="none","OCR-only reading mode");
      const onlyActor=state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      await onlyActor.sendQuery("click",{selector:'.pl-block[data-block-type="paragraph"] .pl-target p'});
      await Zotero.Promise.delay(150);
      check(state.mode==="ocr"&&state.doc.defaultView.getComputedStyle(native).display==="none","clicking OCR text in OCR-only mode keeps the PDF hidden");
      await onlyActor.sendQuery("click",{selector:'.pl-block[data-block-type="paragraph"] [data-action="locate"]'});
      await waitFor(()=>state.mode==="split");
      check(true,"explicit Compare PDF action still opens split view from OCR-only mode");
      await chooseMode("split");
      await PL.ReaderUI.showSaved(reader,translated,"translate");
      await PL.ReaderUI.setFont(19);
      await waitFor(async()=>{
        const global=state.frame?.browsingContext?.currentWindowGlobal;
        return global?.documentURI?.scheme==="file" && (await global.getActor("PaperLoomSmoke").sendQuery("state")).bodyFont==="19px";
      });
      check(true,"font size applies to translated view as well");
      await PL.ReaderUI.setFont(17);
      const appearanceSettings=PL.UI.settings(window);
      await waitFor(()=>appearanceSettings.document.getElementById("saveAppearance"));
      const appearanceDoc=appearanceSettings.document;
      appearanceDoc.getElementById("ocrFontFamily").value="Times New Roman";
      appearanceDoc.getElementById("translationFontFamily").value="Georgia";
      for(const [kind,color]of [["ocr","#243648"],["translation","#57412f"]]){
        appearanceDoc.getElementById(kind+"ColorAuto").checked=false;
        appearanceDoc.getElementById(kind+"ColorAuto").dispatchEvent(new appearanceSettings.Event("change",{bubbles:true}));
        appearanceDoc.getElementById(kind+"TextColor").value=color;
      }
      appearanceDoc.getElementById("saveAppearance").click();
      await waitFor(()=>PL.pref("translationTextColor","")==="#57412f"&&appearanceDoc.getElementById("status").textContent.includes("已保存"));
      appearanceSettings.close();
      const appearanceActor=state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      const palette=await waitFor(async()=>{
        const a=(await appearanceActor.sendQuery("state")).appearance;
        return a.ocrColor==="rgb(36, 54, 72)"&&a.translationColor==="rgb(87, 65, 47)"?a:false;
      });
      check(palette.ocrFamily.includes("Times New Roman")&&palette.translationFamily.includes("Georgia"),"native appearance settings independently update actual OCR and translation fonts and colors");
      await PL.ReaderUI.setMode(reader,"ocr");
      await appearanceActor.sendQuery("click",{selector:'.pl-source+.pl-target p'});
      await Zotero.Promise.delay(150);
      check(state.mode==="ocr","clicking a translation in single-pane mode also preserves the selected view");
      await PL.ReaderUI.load(reader,"ocr");
      const ocrPalette=await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state");
      check(ocrPalette.appearance.ocrColor==="rgb(36, 54, 72)"&&ocrPalette.appearance.ocrFamily.includes("Times New Roman"),"OCR appearance persists when replacing the translation browser");
      await PL.ReaderUI.showSaved(reader,translated,"translate");
      await chooseMode("split");
      const resetSettings=PL.UI.settings(window);
      await waitFor(()=>resetSettings.document.getElementById("resetAppearance"));
      resetSettings.document.getElementById("resetAppearance").click();
      await waitFor(()=>PL.pref("translationTextColor","missing")===""&&resetSettings.document.getElementById("status").textContent.includes("恢复"));
      resetSettings.close();
      const automaticColors=(await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state")).appearance;
      check(automaticColors.ocrColor===automaticColors.translationColor,"default translation text follows theme color instead of fixed blue");
      check(state.kind==="translate" && !!state.pane.querySelector('[data-action="translate"]'),"translation controls and saved translation in OCR panel");
      check(state.pane.querySelector('[data-action="clear-translation"]').textContent==="删除译文"
        &&!state.pane.querySelector('[data-action="repair-translation"]'),"reader keeps delete and translate without an upgrade button");
      await clickPane("translation-only");
      await waitFor(async()=>{
        const data=await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state");
        return data.translationMode==="translation"&&data.sourceCount>0&&data.visibleSources===0;
      });
      check(true,"translation-only button hides every English source paragraph");
      await clickPane("font-larger");
      await waitFor(async()=>{
        const data=await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state");
        return data.targetFont==="18px"&&data.visibleSources===0;
      });
      check(true,"font button resizes actual translated paragraphs in translation-only mode");
      await clickPane("bilingual");
      await waitFor(async()=>{
        const data=await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state");
        return data.translationMode==="bilingual"&&data.visibleSources===data.sourceCount&&data.sourceCount>0;
      });
      check(true,"bilingual button shows the English source above each translated block");
      state.fontSize.value="25";
      state.fontSize.dispatchEvent(new reader._window.Event("input",{bubbles:true}));
      await waitFor(async()=>{
        const data=await state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("state");
        return data.sourceFont==="25px"&&data.targetFont==="25px";
      });
      check(true,"font input resizes both source and translated paragraphs in bilingual mode");
      const viewErrors=[];
      const alert=PL.alert;
      PL.alert=(_window,error)=>viewErrors.push(String(error));
      for(const kind of ["ocr","translate","translate","ocr"]){
        await clickPane(kind==="ocr"?"show-ocr":"show-translation");
        await waitFor(async()=>{
          if(state.kind!==kind)return false;
          const global=state.frame?.browsingContext?.currentWindowGlobal;
          if(global?.documentURI?.scheme!=="file")return false;
          const content=await global.getActor("PaperLoomSmoke").sendQuery("state");
          return content.text.includes(kind==="ocr"?"论文翻译大师 OCR":"论文翻译大师 译文")&&content.bodyFont==="25px";
        });
      }
      await Zotero.Promise.delay(300);
      PL.alert=alert;
      check(!viewErrors.length,"repeated translation button clicks do not report an error");
      check(true,"real pane button clicks switch translation to OCR and back repeatedly");
      check(true,"non-default font size persists across OCR and translation browser replacements");
      const applyStyle=PL.ReaderUI.applyStyle;
      PL.ReaderUI.applyStyle=async()=>{throw new Error("Stylesheet update failure fixture");};
      await PL.ReaderUI.load(reader,"translate");
      await waitFor(async()=>{
        const global=state.frame?.browsingContext?.currentWindowGlobal;
        return global?.documentURI?.scheme==="file"&&(await global.getActor("PaperLoomSmoke").sendQuery("state")).bridge==="true";
      });
      const styleFailureActor=state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      const styleFailurePosition=(await styleFailureActor.sendQuery("state")).firstPosition;
      await styleFailureActor.sendQuery("click",{selector:`[data-block-id="${styleFailurePosition.block}"] [data-action="locate"]`});
      await waitFor(()=>state.lastLocated?.block===styleFailurePosition.block&&state.pdfHighlight?.isConnected);
      await PL.ReaderUI.translationMode("translation");
      check((await styleFailureActor.sendQuery("state")).visibleSources===0,"stylesheet update failure cannot block PDF mapping or translation mode changes");
      PL.ReaderUI.applyStyle=applyStyle;
      await PL.ReaderUI.applyStyle(state);
      await PL.ReaderUI.translationMode("bilingual");
      const fontActor=PL.ReaderUI.readerActor;
      PL.ReaderUI.readerActor=()=>{throw new Error("Failed to load reader-parent.mjs fixture");};
      await PL.ReaderUI.load(reader,"translate");
      await PL.ReaderUI.load(reader,"translate");
      check(state.kind==="translate"&&!!state.saved,"font actor loading failure cannot block repeated saved translation viewing");
      await PL.ReaderUI.setFont(26);
      await waitFor(async()=>{
        const global=state.frame?.browsingContext?.currentWindowGlobal;
        return global?.documentURI?.scheme==="file"&&(await global.getActor("PaperLoomSmoke").sendQuery("state")).targetFont==="26px";
      });
      check(true,"rendered font still updates when reader actor loading fails");
      PL.ReaderUI.readerActor=fontActor;
      await clickPane("font-reset");
      await waitFor(()=>PL.pref("ocrFontSize",0)===17);
      await PL.ReaderUI.load(reader,"ocr");
      await waitFor(async()=>{
        const global=state.frame?.browsingContext?.currentWindowGlobal;
        return global?.documentURI?.scheme==="file"&&(await global.getActor("PaperLoomSmoke").sendQuery("state")).bridge==="true";
      });
      const actor=state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      await actor.sendQuery("click",{selector:'main .pl-target [data-latex] [data-action="copy-latex"]'});
      await waitFor(async()=>{
        report.actionDebug={...(await actor.sendQuery("state")),parentAction:state.lastBridgeAction,bridgeID:state.bridgeID};
        return clipboardText().includes("E=mc^2");
      });
      check(true,"saved formula button copies raw LaTeX to the native clipboard offline");
      const excelPath=PathUtils.join(PathUtils.parent(smokeResultPath),"saved-table.xls");
      const pickSave=PL.Artifacts.pickSaveFile;
      PL.Artifacts.pickSaveFile=async()=>excelPath;
      await actor.sendQuery("click",{selector:'[data-action="export-table"]'});
      await waitFor(()=>IOUtils.exists(excelPath));
      PL.Artifacts.pickSaveFile=pickSave;
      const excelXML=await IOUtils.readUTF8(excelPath);
      check(excelXML.includes('urn:schemas-microsoft-com:office:spreadsheet')&&excelXML.includes("Method"),"saved table exports an Excel workbook through Zotero without the service");
      const baseStyle=PL.readerStyle;
      for(const [i,kind] of ["ocr","translate","translate","ocr"].entries()){
        PL.readerStyle=baseStyle+`\nbody { line-height: ${i%2?1.55:1.6}; }`;
        await PL.ReaderUI.load(reader,kind);
        await PL.ReaderUI.setFont(18+i);
        await PL.ReaderUI.applyStyle(state);
        await PL.ReaderUI.translationMode(i%2?"translation":"bilingual");
        const currentActor=state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
        await waitFor(async()=>(await currentActor.sendQuery("state")).bridge==="true");
        const currentPosition=(await currentActor.sendQuery("state")).firstPosition;
        await currentActor.sendQuery("click",{selector:`[data-block-id="${currentPosition.block}"] [data-action="locate"]`});
        await waitFor(()=>state.lastLocated?.block===currentPosition.block&&state.pdfHighlight?.isConnected);
        check(JSON.stringify(state.lastLocated.bbox)===JSON.stringify(currentPosition.bbox),`mixed sequence ${i+1}: ${kind}, font and stylesheet changes retain exact PDF coordinates`);
      }
      PL.readerStyle=baseStyle;
      await PL.ReaderUI.applyStyle(state);
      await PL.ReaderUI.setFont(17);
      await PL.ReaderUI.translationMode("bilingual");
      await PL.ReaderUI.load(reader,"ocr");
      await waitFor(async()=>{
        const global=state.frame?.browsingContext?.currentWindowGlobal;
        return global?.documentURI?.scheme==="file"&&(await global.getActor("PaperLoomSmoke").sendQuery("state")).bridge==="true";
      });
      const mappedActor=state.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      const position=(await mappedActor.sendQuery("state")).firstPosition;
      await mappedActor.sendQuery("click",{selector:`[data-block-id="${position.block}"] [data-action="locate"]`});
      await waitFor(()=>state.lastLocated?.block===position.block&&state.pdfHighlight?.isConnected);
      check(true,"saved paragraph locates and highlights its matching source PDF rectangle");
      const pdfPage=reader._internalReader._primaryView._iframeWindow.document.querySelector(`.page[data-page-number="${position.page}"]`);
      const bounds=pdfPage.getBoundingClientRect();
      pdfPage.dispatchEvent(new reader._internalReader._primaryView._iframeWindow.MouseEvent("click",{
        bubbles:true,clientX:bounds.left+(position.bbox[0]+position.bbox[2])/2*bounds.width,
        clientY:bounds.top+(position.bbox[1]+position.bbox[3])/2*bounds.height,
      }));
      await waitFor(async()=>(await mappedActor.sendQuery("state")).selectedBlock===position.block);
      check(true,"clicking a source PDF region selects its matching OCR block");
      await chooseMode("original");
      check(state.pane.hidden && !state.doc.body.classList.contains("paperloom-reading"),"restore original native PDF view");

      const unloadable=await PL.ReaderUI.open(automatic);
      const oldTab=unloadable.tabID;
      window.Zotero_Tabs.select("zotero-pane");
      window.Zotero_Tabs.unload(oldTab);
      await waitFor(()=>!Zotero.Reader.getByTabID(oldTab));
      const open=Zotero.Reader.open;
      let undefinedOpen=false;
      Zotero.Reader.open=async function(...args){const result=await open.apply(this,args);if(!result)undefinedOpen=true;return result;};
      const restored=await PL.ReaderUI.open(automatic);
      Zotero.Reader.open=open;
      check(undefinedOpen&&restored.tabID===oldTab,"restored unloaded tab returns undefined natively and plugin waits for its reader without duplicating tabs");
      await PL.ReaderUI.setMode(restored,"split");
      await PL.ReaderUI.showSaved(restored,await PL.Store.saved(automatic,"ocr"),"ocr");
      check(restored._internalReader._primaryView._iframeWindow.PDFViewerApplication.pdfDocument.numPages>0,"restored tab keeps original PDF loaded alongside saved OCR");
      window.Zotero_Tabs.select(reader.tabID);

      const forceBefore=(await PL.request("GET","/api/testing/calls")).ocr;
      await PL.Tasks.enqueue(pdf,"ocr",window,{force:true});
      await PL.Tasks.enqueue(pdf,"ocr",window,{force:true});
      check((await PL.request("GET","/api/testing/calls")).ocr===forceBefore+2,"each force OCR bypasses both saved attachments and completed server jobs");
      await PL.ReaderUI.load(reader,"ocr");
      await PL.Tasks.clearOCR(pdf);
      await PL.Store.reconcile();
      check(!await PL.Store.saved(pdf,"ocr") && !state.frame,"clear OCR releases the reader and removes every old OCR attachment");
      check(PL.Store.label(pdf,"ocr")==="未 OCR" && !!await PL.Store.saved(pdf,"translate") && !!await pdf.getFilePathAsync(),"clear OCR preserves source PDF and saved translation");
      await PL.ocr(window,pdf);
      check((await PL.request("GET","/api/testing/calls")).ocr===forceBefore+3,"OCR after clearing actually recognizes again instead of returning server cache");
      await PL.request("POST","/api/testing/block-next");
      const cancelled=PL.Tasks.enqueue(pdf,"ocr",window,{force:true}).then(()=>false,()=>true);
      await waitFor(()=>PL.Tasks.active?.jobID);
      const cancelledJob=PL.Tasks.active.jobID;
      await PL.Tasks.clearOCR(pdf);
      check(await cancelled && !PL.Tasks.active && (await PL.request("GET",`/api/jobs/${cancelledJob}`)).state==="cancelled","clear an active OCR cancels its job and releases the occupied task");
      await PL.ocr(window,pdf);
      check(!!await PL.Store.saved(pdf,"ocr"),"OCR can restart immediately after cancelling and clearing");

      const count=parent.getAttachments().length;
      const request=PL.request;
      PL.request=async()=>{throw new Error("offline: service must not be called");};
      await PL.ocr(window,pdf);
      await PL.translate(window,pdf);
      await PL.ReaderUI.setMode(reader,"split");
      await PL.ReaderUI.load(reader,"ocr");
      check(parent.getAttachments().length===count,"offline cached OCR and translation create no duplicates");
      PL.Store.records={};
      await PL.Store.reconcile();
      check(PL.Store.label(parent,"ocr")==="已 OCR" && PL.Store.label(parent,"translate")==="已翻译","statuses rebuilt from native attachments after cache removal");
      await IOUtils.remove(await pdf.getFilePathAsync());
      await PL.ReaderUI.load(reader,"ocr");
      check(!!state.saved,"saved OCR opens after original PDF removal");
      PL.request=request;

      // Use the separate automatic fixture: the main test deliberately removed
      // its PDF above. Exercise actual saved-page controls and the local worker.
      window.Zotero_Tabs.select(restored.tabID);
      const editState=PL.ReaderUI.states.get(restored);
      const oldEditTranslation=await PL.translate(window,automatic);
      await PL.ReaderUI.load(restored,"ocr");
      const beforeEdit=await PL.request("GET","/api/testing/calls");
      const originalEditPDF=await Zotero.Utilities.Internal.md5Async(Zotero.File.pathToFile(await automatic.getFilePathAsync()));
      const oldEditOCR=await PL.Store.saved(automatic,"ocr");
      const oldEditPath=await oldEditOCR.attachment.getFilePathAsync();
      const originalEditPage=await IOUtils.readUTF8(oldEditPath);
      PL.request=async()=>{throw new Error("Editing must remain offline");};
      await PL.Editing.open(restored);
      const editActor=editState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      check((await editActor.sendQuery("editor-state")).fields>0,"OCR editor opens locally with per-block Markdown fields");
      const editedPosition=await editActor.sendQuery("editor-change");
      let draft=await editActor.sendQuery("editor-state");
      check(draft.draft.insertions[0].markdown==="## Main"&&Object.values(draft.draft.changes).some(p=>p.deleted),"actual editor controls insert a Main heading and delete a duplicate figure");
      await editActor.sendQuery("click",{selector:'[data-action="editor-undo"]'});
      draft=await editActor.sendQuery("editor-state");
      check(!Object.values(draft.draft.changes).some(p=>p.deleted),"editor undo restores a deleted figure before saving");
      await editActor.sendQuery("click",{selector:'[data-block-type="figure"] [data-action="editor-delete"]'});
      await screenshot(window,smokeScreenshotPath.replace("integrated-reader.png","ocr-editor.png"));
      await editActor.sendQuery("click",{selector:'[data-action="editor-save"]'});
      await waitFor(()=>!!PL.Store.get(automatic)?.ocr?.editRevision&&!PL.Editing.jobs.size,15000);
      PL.request=request;
      const editedOCR=await PL.Store.saved(automatic,"ocr");
      const editedPath=await editedOCR.attachment.getFilePathAsync();
      const corrected=await IOUtils.readJSON(PathUtils.join(PathUtils.parent(editedPath),"source.json"));
      const correctedParagraph=corrected.blocks.find(b=>b.id===editedPosition.block);
      check(correctedParagraph.content.text.includes("Manually corrected")&&JSON.stringify(correctedParagraph.bbox)===JSON.stringify(editedPosition.bbox)
        &&correctedParagraph.source.text,"saved edit retains the exact original block ID, source text and PDF coordinates");
      check(corrected.blocks.some(b=>b.content.text==="Main")&&!corrected.blocks.some(b=>b.id===editedPosition.figure),"saved OCR and Markdown omit the deleted figure and include the manual heading");
      check(await IOUtils.readUTF8(oldEditPath)===originalEditPage&&oldEditTranslation.attachment.deleted===false
        &&await Zotero.Utilities.Internal.md5Async(Zotero.File.pathToFile(await automatic.getFilePathAsync()))===originalEditPDF,"editing preserves the prior OCR, prior translation and original PDF bytes");
      await PL.Store.reconcile();
      check(PL.Editing.stale(PL.Store.get(automatic))&&PL.Store.label(automatic,"translate")==="原文已修改","translation staleness survives state reconstruction from saved attachments");
      const editedContentActor=await waitFor(async()=>{
        const global=editState.frame?.browsingContext?.currentWindowGlobal;
        if(global?.documentURI?.scheme!=="file")return false;
        const actor=global.getActor("PaperLoomSmoke"),content=await actor.sendQuery("state");
        return content.bridge==="true"&&content.mathCount>0?actor:false;
      });
      await editedContentActor.sendQuery("click",{selector:`[data-block-id="${editedPosition.block}"] [data-action="locate"]`});
      await waitFor(()=>editState.lastLocated?.block===editedPosition.block&&editState.pdfHighlight?.isConnected);
      check(true,"manually corrected paragraph still locates its original PDF region");
      PL.request=async()=>{throw new Error("Editing must remain offline");};
      await PL.Editing.open(restored);
      const reopenedActor=editState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
      const correctedCaption=await reopenedActor.sendQuery("editor-role");
      report.roleDraft=await reopenedActor.sendQuery("editor-state");
      check(report.roleDraft.draft.changes[correctedCaption]?.role==="table_caption","role selection is retained in the edit draft");
      await reopenedActor.sendQuery("click",{selector:'[data-action="editor-save"]'});
      await waitFor(()=>!!PL.Store.get(automatic)?.ocr?.editRevision&&PL.Store.get(automatic).ocr.editRevision!==editedOCR.editRevision&&!PL.Editing.jobs.size,15000);
      PL.request=request;
      const secondEdit=await PL.Store.saved(automatic,"ocr");
      const secondEditPath=await secondEdit.attachment.getFilePathAsync();
      const secondSnapshot=await IOUtils.readJSON(PathUtils.join(PathUtils.parent(secondEditPath),"source.json"));
      check(secondSnapshot.blocks.find(b=>b.id===correctedCaption).layout_label==="table_title"
        &&secondSnapshot.blocks.some(b=>b.content.text==="Main")&&!secondSnapshot.blocks.some(b=>b.id===editedPosition.figure),"reopening and saving type corrections preserves previous text, manual headings and deleted figures");
      await PL.translate(window,automatic);
      const afterEdit=await PL.request("GET","/api/testing/calls");
      const editedTranslation=await PL.Store.saved(automatic,"translate");
      const editedTranslationPath=await editedTranslation.attachment.getFilePathAsync();
      const editedTranslationMD=await IOUtils.readUTF8(PathUtils.join(PathUtils.parent(editedTranslationPath),"translation.md"));
      check(afterEdit.ocr===beforeEdit.ocr&&afterEdit.llm>beforeEdit.llm&&editedTranslationMD.includes("Manually corrected")&&editedTranslationMD.includes("Main"),"manual translation uses the corrected snapshot and new title without OCR");
      check(!PL.Editing.stale(PL.Store.get(automatic)),"new translation is linked to the current correction revision");
      window.Zotero_Tabs.select(reader.tabID);

      // Selection takes place in the dialog. Main-window selection is not the
      // submitted batch, and opening or cancelling must never start work.
      const batchParents=[],batchPDFs=[];
      for(let index=0;index<2;index++){
        const parent=new Zotero.Item("journalArticle");parent.setField("title",`Batch paper ${index+1}`);await parent.saveTx();
        const path=PathUtils.join(PathUtils.parent(smokeResultPath),`batch-${index}.pdf`);
        const bytes=await IOUtils.read(smokePDFPath),suffix=new TextEncoder().encode(`\n% Batch fixture ${index}\n`);
        const copy=new Uint8Array(bytes.length+suffix.length);copy.set(bytes);copy.set(suffix,bytes.length);await IOUtils.write(path,copy);
        const attachment=await Zotero.Attachments.importFromFile({file:path,parentItemID:parent.id,contentType:"application/pdf",title:"Batch fixture"});
        batchParents.push(parent);batchPDFs.push(attachment);
      }
      await window.ZoteroPane.selectItems(batchParents.map(parent=>parent.id));
      const openBatch=async kind=>{
        window.document.getElementById(`paperloom-batch-${kind}`).click();
        const dialog=await waitFor(()=>{const value=PL.BatchPicker.windows.get(window);
          report.pickerOpenDebug={count:PL.BatchPicker.windows.size,found:!!value,closed:value?.closed,
            documents:[...Services.wm.getEnumerator(null)].map(w=>w.document.documentURI)};
          return value&&!value.closed?value:false;});
        await waitFor(()=>{
          report.pickerDebug={uri:dialog.document.documentURI,ready:dialog.document.readyState,api:!!dialog.PaperLoomBatch,
            rows:dialog.document.getElementById("papers")?.children.length,notice:dialog.document.getElementById("notice")?.textContent,
            empty:dialog.document.getElementById("empty")?.textContent,loading:dialog.document.getElementById("scope")?.disabled,
            text:dialog.document.documentElement.textContent.slice(-800)};
          return dialog.PaperLoomBatch&&dialog.document.getElementById("papers")?.children.length&&!dialog.document.getElementById("scope").disabled;
        });
        return dialog;
      };
      const choosePapers=(dialog,pdfs)=>{
        const doc=dialog.document;doc.getElementById("clearSelection").click();
        check(doc.getElementById("start").disabled,"batch start is disabled until at least one paper is checked");
        for(const pdf of pdfs){const input=doc.querySelector(`tr[data-source-id="${pdf.id}"] input[type="checkbox"]`);if(!input)throw new Error("Missing batch paper "+pdf.id);input.click();}
      };
      const beforeBatchCalls=await PL.request("GET","/api/testing/calls"),previousBatch=PL.Tasks.lastBatch;
      const cancelledPicker=await openBatch("ocr");
      check(JSON.stringify(await PL.request("GET","/api/testing/calls"))===JSON.stringify(beforeBatchCalls)&&PL.Tasks.lastBatch===previousBatch,"opening the paper picker does not start OCR or translation");
      cancelledPicker.document.getElementById("close").click();
      await waitFor(()=>cancelledPicker.closed);
      check(PL.Tasks.lastBatch===previousBatch,"cancelling the paper picker leaves the task queue untouched");
      await window.ZoteroPane.selectItem(batchParents[0].id);
      const ocrPicker=await openBatch("ocr");
      ocrPicker.document.getElementById("scope").value="library";
      ocrPicker.document.getElementById("scope").dispatchEvent(new ocrPicker.Event("change",{bubbles:true}));
      await waitFor(()=>!ocrPicker.document.getElementById("scope").disabled&&ocrPicker.document.querySelector(`tr[data-source-id="${batchPDFs[1].id}"]`));
      check(ocrPicker.document.querySelectorAll('[data-status="ocr"]').length>=2,"paper picker lists titles and independent OCR/translation status columns across the library");
      ocrPicker.document.getElementById("clearSelection").click();
      ocrPicker.document.getElementById("search").value="Batch paper 2";
      ocrPicker.document.getElementById("search").dispatchEvent(new ocrPicker.Event("input",{bubbles:true}));
      check(ocrPicker.document.getElementById("papers").children.length===1,"paper picker filters by paper title");
      ocrPicker.document.getElementById("selectFiltered").click();
      ocrPicker.document.getElementById("search").value="";
      ocrPicker.document.getElementById("search").dispatchEvent(new ocrPicker.Event("input",{bubbles:true}));
      check(ocrPicker.document.querySelector(`tr[data-source-id="${batchPDFs[1].id}"] input`).checked,"checkbox selection survives searching and clearing the filter");
      choosePapers(ocrPicker,batchPDFs);
      const readersBeforeBatch=Zotero.Reader._readers.length;
      await screenshot(ocrPicker,smokeScreenshotPath.replace("integrated-reader.png","batch-picker.png"));
      await PL.request("POST","/api/testing/hold-second-ocr");
      ocrPicker.document.getElementById("start").click();
      await waitFor(async()=>PL.Store.get(batchPDFs[0])?.ocr?.state==="ready"
        &&(await PL.request("GET","/api/testing/calls")).ocr_held);
      // The fake converter deliberately shares one document cache between
      // fixture PDFs. Bypass the earlier translation cache to exercise the LLM.
      await PL.Tasks.clearTranslation(batchPDFs[0]);
      let translatedDuringOCR=false,parallelError;
      const parallelTranslation=PL.translate(window,batchPDFs[0]).then(result=>{
        translatedDuringOCR=true;return result;
      },error=>{parallelError=error;});
      try {
        await waitFor(()=>translatedDuringOCR||parallelError,15000);
        if(parallelError)throw parallelError;
        const parallelCalls=await PL.request("GET","/api/testing/calls");
        report.parallelProcessing={...parallelCalls,activeOCR:PL.Tasks.forSource(batchPDFs[1])?.kind};
        check(parallelCalls.ocr_held&&parallelCalls.llm>beforeBatchCalls.llm
          &&PL.Tasks.forSource(batchPDFs[1])?.kind==="ocr",
          "first paper completes real translation API and saves its result while the second OCR job is still held");
        check(Zotero.Reader._readers.length===readersBeforeBatch,"concurrent OCR and translation remain in the background without opening tabs");
      } finally { await PL.request("POST","/api/testing/release-ocr"); }
      await parallelTranslation;
      await waitFor(()=>PL.Tasks.lastBatch!==previousBatch&&PL.Tasks.lastBatch?.finished);
      check(PL.Tasks.lastBatch.total===2&&PL.Tasks.lastBatch.success===2&&batchPDFs.every(pdf=>PL.Store.get(pdf)?.ocr?.state==="ready"),"dialog checkmarks process both papers independently of main-window selection");
      check((await PL.request("GET","/api/testing/calls")).ocr===beforeBatchCalls.ocr+2,"batch OCR actually processes both distinct PDFs");
      check(Zotero.Reader._readers.length===readersBeforeBatch,"batch OCR never opens reading tabs on completion");
      await waitFor(()=>!ocrPicker.document.getElementById("action").disabled);
      ocrPicker.document.getElementById("close").click();
      const previousOCRBatch=PL.Tasks.lastBatch;
      const translationPicker=await openBatch("translate");
      check(translationPicker.document.getElementById("action").value==="translate","translation toolbar button opens the picker in translation mode");
      choosePapers(translationPicker,batchPDFs);
      translationPicker.document.getElementById("start").click();
      await waitFor(()=>PL.Tasks.lastBatch!==previousOCRBatch&&PL.Tasks.lastBatch?.finished);
      check(PL.Tasks.lastBatch.total===2&&PL.Tasks.lastBatch.success===2&&batchPDFs.every(pdf=>PL.Store.get(pdf)?.translations?.zh?.state==="ready"),"translation picker submits exactly its checked papers");
      check((await PL.request("GET","/api/testing/calls")).ocr===beforeBatchCalls.ocr+2,"batch translation reuses OCR instead of recognizing the papers again");
      check(Zotero.Reader._readers.length===readersBeforeBatch,"batch translation also finishes without opening reading tabs");
      check(window.document.getElementById("paperloom-task-status").textContent.includes("2/2"),"completed batch summary remains visible with success counts");
      await waitFor(()=>!translationPicker.document.getElementById("action").disabled);
      translationPicker.document.getElementById("close").click();
      const untranslatedParent=new Zotero.Item("journalArticle");untranslatedParent.setField("title","Batch translation without prior OCR");await untranslatedParent.saveTx();
      const freshBatchPath=PathUtils.join(PathUtils.parent(smokeResultPath),"batch-fresh.pdf");
      const freshBytes=await IOUtils.read(smokePDFPath),freshSuffix=new TextEncoder().encode("\n% Fresh batch translation fixture\n");
      const freshCopy=new Uint8Array(freshBytes.length+freshSuffix.length);freshCopy.set(freshBytes);freshCopy.set(freshSuffix,freshBytes.length);await IOUtils.write(freshBatchPath,freshCopy);
      const freshBatchPDF=await Zotero.Attachments.importFromFile({file:freshBatchPath,parentItemID:untranslatedParent.id,contentType:"application/pdf",title:"Fresh batch fixture"});
      const lastTranslatedBatch=PL.Tasks.lastBatch;
      const mixedPicker=await openBatch("translate");
      choosePapers(mixedPicker,[batchPDFs[0],freshBatchPDF]);
      mixedPicker.document.getElementById("start").click();
      await waitFor(()=>PL.Tasks.lastBatch!==lastTranslatedBatch);
      mixedPicker.document.getElementById("close").click();
      await waitFor(()=>mixedPicker.closed);
      await waitFor(()=>PL.Tasks.lastBatch!==lastTranslatedBatch&&PL.Tasks.lastBatch?.finished);
      check(PL.Tasks.lastBatch.success===2&&PL.Store.get(freshBatchPDF)?.ocr?.state==="ready"&&PL.Store.get(freshBatchPDF)?.translations?.zh?.state==="ready",
        "batch translation fills missing OCR automatically alongside a paper that already has results");
      check((await PL.request("GET","/api/testing/calls")).ocr===beforeBatchCalls.ocr+3,"mixed batch recognizes only the one paper that lacks OCR");
      check(Zotero.Reader._readers.length===readersBeforeBatch,"closing the picker keeps its queued jobs running in the background without opening tabs");
      const onePicker=await openBatch("ocr"),beforeSingleBatch=PL.Tasks.lastBatch;
      choosePapers(onePicker,[freshBatchPDF]);onePicker.document.getElementById("start").click();
      await waitFor(()=>PL.Tasks.lastBatch!==beforeSingleBatch&&PL.Tasks.lastBatch?.finished);
      check(PL.Tasks.lastBatch.total===1&&PL.Tasks.lastBatch.success===1&&Zotero.Reader._readers.length===readersBeforeBatch,"even a one-paper batch completes without opening a reader");
      onePicker.document.getElementById("close").click();
      window.Zotero_Tabs.select(reader.tabID);

      if(smokeRealArtifact){
        const fixture=smokeRealArtifact;
        const actualParent=new Zotero.Item("journalArticle");
        actualParent.setField("title",fixture.manifest.title);
        await actualParent.saveTx();
        const actualPDF=await Zotero.Attachments.importFromFile({file:fixture.pdf,parentItemID:actualParent.id,contentType:"application/pdf",title:"Actual 22-page PDF"});
        const temp=Zotero.getTempDirectory();temp.append("paperloom-real-artifact");temp.createUnique(Ci.nsIFile.DIRECTORY_TYPE,0o700);
        let actual;
        try{
          await PL.Artifacts.extract(fixture.bundle,temp.path,"ocr");
          actual=await PL.Artifacts.save(actualPDF,"ocr",await Zotero.Utilities.Internal.md5Async(Zotero.File.pathToFile(await actualPDF.getFilePathAsync())),fixture.manifest,temp.path,"zh");
        }finally{await IOUtils.remove(temp.path,{recursive:true,ignoreAbsent:true});}
        const actualReader=await Zotero.Reader.open(actualPDF.id);await actualReader._initPromise;
        await PL.ReaderUI.setMode(actualReader,"split");await PL.ReaderUI.showSaved(actualReader,actual,"ocr");
        const actualState=PL.ReaderUI.states.get(actualReader);
        await waitFor(()=>actualReader._internalReader?._primaryView?._iframeWindow?.document?.querySelector('.page canvas'));
        check(actualReader._internalReader._primaryView._iframeWindow.PDFViewerApplication.pdfDocument.numPages===22,"actual 22-page source PDF renders alongside saved OCR");
        const actualContent=await waitFor(async()=>{
          const global=actualState.frame?.browsingContext?.currentWindowGlobal;
          if(global?.documentURI?.scheme!=="file")return false;
          const result=await global.getActor("PaperLoomSmoke").sendQuery("state");
          return result.mathCount>20&&result.paraFraction&&result.layout.compactStyle ? result:false;
        });
        report.actualContent=actualContent;
        check(actualContent.mathErrors===0,"actual paper formulas including the Para fraction typeset without MathJax errors");
        check(actualContent.caption5Math===6,"all six missing-delimiter subscripts in actual Figure 5 caption typeset correctly");
        check(actualContent.figure1Ratio>3,"actual Figure 1 retains all three panels together in one wide image");
        check(actualContent.layout.compactStyle&&actualContent.layout.gap>=0&&actualContent.layout.gap<20,"previously saved actual OCR gets compact paragraph spacing without regenerating it");
        await PL.ReaderUI.setMode(actualReader,"ocr");
        const oldActor=actualState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
        await oldActor.sendQuery("click",{selector:'.pl-block[data-block-type="paragraph"] .pl-target p'});
        await Zotero.Promise.delay(150);
        check(actualState.mode==="ocr","older saved OCR pages also keep OCR-only mode when text is clicked");
        await oldActor.sendQuery("click",{selector:'.pl-block[data-block-type="paragraph"] [data-action="locate"]'});
        await waitFor(()=>actualState.mode==="split");
        check(true,"older saved pages retain the explicit Compare PDF button without regenerating them");
        if(smokeRealSourceMatches){
          await PL.Editing.open(actualReader);
          const legacyEditor=await actualState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("editor-state");
          check(legacyEditor.fields>200,"previously saved real paper gains block editing without another OCR pass");
          await PL.ReaderUI.readerActor(actualState.frame.browsingContext.currentWindowGlobal).sendQuery("editor-command",{command:"cancel"});
        }else{
          let mismatch;
          try{await PL.Editing.open(actualReader);}catch(error){mismatch=error;}
          check(mismatch?.message.includes("OCR 源版本已不在本机")
            &&(await PL.Store.saved(actualPDF,"ocr")).attachmentID===actual.attachmentID,
            "legacy real OCR refuses a mismatched newer source cache without replacing its saved attachment");
        }
        // A frozen, matching real-paper cache still exercises all legacy
        // migration controls, independently of the historical render fixture.
        const migration=smokeLegacyMigration;
        const migrationParent=new Zotero.Item("journalArticle");
        migrationParent.setField("title","Isolated real paper legacy editing fixture");await migrationParent.saveTx();
        const migrationPDF=await Zotero.Attachments.importFromFile({file:migration.pdf,parentItemID:migrationParent.id,contentType:"application/pdf",title:"Real paper legacy editing PDF"});
        const migrationTemp=Zotero.getTempDirectory();migrationTemp.append("paperloom-legacy-migration");migrationTemp.createUnique(Ci.nsIFile.DIRECTORY_TYPE,0o700);
        try{
          await PL.Artifacts.extract(migration.bundle,migrationTemp.path,"ocr");
          await PL.Artifacts.save(migrationPDF,"ocr",await Zotero.Utilities.Internal.md5Async(Zotero.File.pathToFile(await migrationPDF.getFilePathAsync())),migration.manifest,migrationTemp.path,"zh");
        }finally{await IOUtils.remove(migrationTemp.path,{recursive:true,ignoreAbsent:true});}
        const migrationReader=await PL.ReaderUI.open(migrationPDF);
        await PL.ReaderUI.setMode(migrationReader,"split");
        const migrationBefore=await PL.request("GET","/api/testing/calls");
        await PL.Editing.open(migrationReader);
        const migrationState=PL.ReaderUI.states.get(migrationReader);
        const legacyEditor=await migrationState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke").sendQuery("editor-state");
        const migrationAfter=await PL.request("GET","/api/testing/calls");
        check(legacyEditor.fields>200&&migrationBefore.ocr===migrationAfter.ocr&&migrationBefore.llm===migrationAfter.llm,
          "matching isolated real-paper legacy source gains over 200 editing fields without OCR or translation");
        await PL.ReaderUI.readerActor(migrationState.frame.browsingContext.currentWindowGlobal).sendQuery("editor-command",{command:"cancel"});
        window.Zotero_Tabs.select(actualReader.tabID);
        const actor=actualState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
        await actor.sendQuery("scroll",{kind:"figure"});
        await screenshot(window,smokeActualFigureScreenshotPath);
        await actor.sendQuery("scroll",{kind:"formula"});
        await screenshot(window,smokeActualFormulaScreenshotPath);
        await actor.sendQuery("scroll",{kind:"caption"});
        await screenshot(window,smokeActualFigureScreenshotPath.replace("actual-figure.png","actual-caption.png"));
        const located=await actor.sendQuery("locate-study");
        await waitFor(()=>actualState.lastLocated?.block===located.id&&actualState.pdfHighlight?.isConnected);
        await waitFor(()=>actualReader._internalReader._primaryView._iframeWindow.PDFViewerApplication.pdfViewer.currentPageNumber===3);
        check(actualReader._internalReader._primaryView._iframeWindow.document.querySelector('.page[data-page-number="3"]').style.opacity!=="0.3","selected PDF page remains fully opaque while surrounding regions are dimmed");
        check(true,"actual paper study paragraph links page 3 and highlights its exact source region");
        await screenshot(window,smokeActualFigureScreenshotPath.replace("actual-figure.png","actual-linked-paragraph.png"));
        const crossColumn=await actor.sendQuery("cross-column");
        await actor.sendQuery("clear-selection");
        await actualReader.navigate({pageIndex:1});
        const actualView=actualReader._internalReader._primaryView;
        await waitFor(()=>actualView._iframeWindow.document.querySelector('.page[data-page-number="2"] canvas'));
        const crossPage=actualView._iframeWindow.document.querySelector('.page[data-page-number="2"]');
        crossPage.ownerDocument.defaultView.getSelection().removeAllRanges();
        const crossBounds=crossPage.getBoundingClientRect(),crossFrame=actualView._iframe.getBoundingClientRect(),crossOuter=actualReader._iframe.getBoundingClientRect();
        const crossX=crossOuter.left+crossFrame.left+crossBounds.left+crossBounds.width*.72;
        const crossY=crossOuter.top+crossFrame.top+crossBounds.top+crossBounds.height*.38;
        window.windowUtils.sendMouseEvent("mousedown",crossX,crossY,0,1,0);
        window.windowUtils.sendMouseEvent("mouseup",crossX,crossY,0,1,0);
        await waitFor(async()=>(await actor.sendQuery("state")).selectedBlock===crossColumn.id);
        check(true,"clicking the previously unmapped right-column continuation selects the whole OCR paragraph");
        await actor.sendQuery("click",{selector:`[data-block-id="${crossColumn.id}"] [data-action="locate"]`});
        await waitFor(()=>actualState.lastLocated?.block===crossColumn.id&&actualState.lastLocated.regions?.length>=2&&actualState.pdfHighlight?.isConnected);
        report.crossColumn=actualState.lastLocated;
        check(actualState.lastLocated.regions.some(region=>region.page===2&&region.bbox[0]>.5&&region.bbox[1]>.28&&region.bbox[3]<.49)
          &&actualState.pdfHighlight.querySelectorAll('rect').length>=2,"actual cross-column paragraph highlights both columns without enclosing the unrelated introduction text");
        await screenshot(window,smokeActualFigureScreenshotPath.replace("actual-figure.png","actual-cross-column.png"));
        if(smokeRealTranslation){
          const fixture=smokeRealTranslation;
          const temp=Zotero.getTempDirectory();temp.append("paperloom-real-translation");temp.createUnique(Ci.nsIFile.DIRECTORY_TYPE,0o700);
          let saved;
          try{
            await PL.Artifacts.extract(fixture.bundle,temp.path,"translate");
            saved=await PL.Artifacts.save(actualPDF,"translate",actual.hash,fixture.manifest,temp.path,"zh");
          }finally{await IOUtils.remove(temp.path,{recursive:true,ignoreAbsent:true});}
          await PL.ReaderUI.showSaved(actualReader,saved,"translate");
          const bilingual=await waitFor(async()=>{
            const global=actualState.frame?.browsingContext?.currentWindowGlobal;
            if(global?.documentURI?.scheme!=="file")return false;
            const content=await global.getActor("PaperLoomSmoke").sendQuery("state");
            return content.sourceCount>200&&content.bridge==="true" ? content:false;
          });
          check(bilingual.mathErrors===0&&bilingual.visibleSources===bilingual.sourceCount,"actual saved translation shows over 200 English/Chinese block pairs with no formula errors");
          check(bilingual.layout.compactStyle&&bilingual.layout.gap>=0&&bilingual.layout.gap<20
            &&bilingual.layout.pairGap>=0&&bilingual.layout.pairGap<16,"saved bilingual page has compact paragraph gaps and compact English-to-translation spacing");
          const actor=actualState.frame.browsingContext.currentWindowGlobal.getActor("PaperLoomSmoke");
          const bilingualCross=await actor.sendQuery("cross-column");
          await actor.sendQuery("click",{selector:`[data-block-id="${bilingualCross.id}"] [data-action="locate"]`});
          await waitFor(()=>actualState.lastLocated?.block===bilingualCross.id&&actualState.lastLocated.regions?.length>=2);
          check(true,"saved bilingual translation maps both regions using its original English paragraph");
          const paragraph=await actor.sendQuery("locate-study");
          await waitFor(()=>actualState.lastLocated?.block===paragraph.id&&actualState.pdfHighlight?.isConnected);
          await screenshot(window,smokeActualFigureScreenshotPath.replace("actual-figure.png","actual-bilingual.png"));
          await PL.ReaderUI.translationMode("translation");
          const translationOnly=await actor.sendQuery("state");
          check(translationOnly.visibleSources===0,"actual saved translation switches to Chinese-only without translation requests");
          check(translationOnly.layout.compactStyle&&translationOnly.layout.gap>=0&&translationOnly.layout.gap<20,"saved translation-only page also uses compact paragraph spacing");
          await screenshot(window,smokeActualFigureScreenshotPath.replace("actual-figure.png","actual-translation-only.png"));
          await actor.sendQuery("click",{selector:`[data-block-id="${bilingualCross.id}"] [data-action="locate"]`});
          await waitFor(()=>actualState.lastLocated?.block===bilingualCross.id&&actualState.lastLocated.regions?.length>=2);
          check(true,"translation-only mode still maps both columns while English source paragraphs are hidden");
          await PL.ReaderUI.translationMode("bilingual");
        }
      }

      // Best-effort UI evidence; structural/behavior assertions above are mandatory.
      try {
        await PL.ReaderUI.setMode(reader,"split");
        state.swapped=false;
        PL.ReaderUI.layout(state);
        await screenshot(window,smokeScreenshotPath);
        report.screenshot=smokeScreenshotPath;
      }
      catch(error){report.screenshotError=String(error);}
      await PL.Store.flush();
      PL.setPref("serviceURL",smokeManagedURL);
      PL.setPref("backend",smokeQuitBackend);
      await PL.Service.ensure();
      report.quitService={url:PL.serviceURL(), lease:PL.Service.leasePath, state:PL.Service.statePath, runnerPID:PL.Service.process.pid};
      check(PL.Service.owned,"leave an owned service running to verify real Zotero quit cleanup");
      report.ok=true;
    }
    catch(error){report.ok=false;report.error=String(error);report.stack=error.stack;}
    await IOUtils.writeUTF8(smokeResultPath,JSON.stringify(report,null,2));
    Services.startup.quit(Ci.nsIAppStartup.eForceQuit);
  },1500);
};
