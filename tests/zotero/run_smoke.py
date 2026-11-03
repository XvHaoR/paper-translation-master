"""Exercise the actual Zotero runtime in an isolated, disposable profile.

Usage: python tests/zotero/run_smoke.py --zotero "C:/Program Files/Zotero/zotero.exe"
Uses fixture PDF + fake LLM. Never opens or modifies the user's Zotero library.
Results and diagnostic logs remain under workspace/zotero-smoke/<run>.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import threading
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zotero", default="C:/Program Files/Zotero/zotero.exe")
    parser.add_argument("--real-ocr-fixture", type=Path, help="Optional saved artifact/PDF metadata for real-paper rendering checks")
    parser.add_argument("--real-translation-fixture", type=Path, help="Optional cache-only translated artifact for real bilingual checks")
    parser.add_argument("--quit-backend", choices=["local", "wsl"], default="local")
    parser.add_argument("--ocr-mode", choices=["official", "rapidocr"], default="official")
    args = parser.parse_args()
    run = ROOT / "workspace" / "zotero-smoke" / time.strftime("%Y%m%d-%H%M%S")
    profile = run / "profile"
    extensions = profile / "extensions"
    extensions.mkdir(parents=True)
    data_dir = run / "data"
    data_dir.mkdir()
    result_file = run / "result.json"
    isolated_temp = run / "temp"
    isolated_temp.mkdir()
    from app.config import Settings
    from app.pipeline.orchestrator import ConvertOptions, Orchestrator
    from app.server import create_app
    from app.services import translator
    import uvicorn

    settings = Settings(workspace=run / "service")
    conversion = Orchestrator(settings.workspace).convert(
        ROOT / "tests" / "fixtures" / "sample_native.pdf", ConvertOptions(ocr_mode="native")
    )
    assert conversion.ok, conversion.errors
    # Native fixture text contains no TeX blocks. Add an explicit equation to
    # check actual typesetting, rather than only checking that MathJax loads.
    from app.models.ir import Block, BlockContent, BlockType
    conversion.ir.blocks.append(Block(
        id="smoke-equation", type=BlockType.EQUATION_BLOCK, page=1, order=999,
        content=BlockContent(latex=" $$ E=mc^2 $$ ")
    ))
    conversion.ir.blocks.append(Block(id="smoke-table", type=BlockType.TABLE, page=1, order=1000,
        bbox=[.1,.4,.7,.55], content=BlockContent(html="<table><tr><th>Method</th><th>Score</th></tr><tr><td>Ours</td><td>99.2</td></tr></table>")))
    from PIL import Image
    (conversion.doc_dir / "assets").mkdir(exist_ok=True)
    Image.new("RGB", (50, 25), "#527ca6").save(conversion.doc_dir / "assets" / "edit-figure.png")
    conversion.ir.blocks.append(Block(id="smoke-edit-figure", type=BlockType.FIGURE, page=1, order=1001,
        bbox=[.6,.6,.9,.8], content=BlockContent(image_path="assets/edit-figure.png")))
    conversion.ir.blocks.append(Block(id="smoke-figure-caption", type=BlockType.CAPTION, page=1, order=1002,
        layout_label="figure_title", content=BlockContent(text="Figure 1. An example figure caption with $x^2$.")))
    conversion.ir.blocks.append(Block(id="smoke-footnote", type=BlockType.FOOTNOTE, page=1, order=1003,
        content=BlockContent(text="1. A source footnote stays separate from the main body.")))
    conversion.ir.blocks.append(Block(id="smoke-table-caption", type=BlockType.CAPTION, page=1, order=999,
        layout_label="table_title", content=BlockContent(text="Table 1. Example recognition scores.")))
    next(block for block in conversion.ir.blocks if block.id=="smoke-equation").order=990
    (conversion.doc_dir / "document.json").write_text(conversion.ir.model_dump_json(), encoding="utf-8")
    real_artifact = json.loads(args.real_ocr_fixture.read_text(encoding="utf-8")) if args.real_ocr_fixture else None
    legacy_migration = None
    real_source_matches = False
    if real_artifact:
        # Old exports deliberately do not embed their source IR. A later OCR
        # can replace that mutable cache, so never borrow the developer's live
        # workspace during migration tests. Snapshot it into this test run.
        import hashlib
        import re
        from app.services.translation_export import create_ocr_export
        source_directory = Path(real_artifact["pdf"]).parent
        document_id = real_artifact["manifest"]["document_id"]
        isolated_document = settings.workspace / "documents" / document_id
        shutil.copytree(source_directory, isolated_document,
                        ignore=shutil.ignore_patterns("ocr", "translations"))
        source_bytes = (isolated_document / "document.json").read_bytes()
        real_source_matches = hashlib.sha256(source_bytes).hexdigest() == real_artifact["manifest"]["source_sha256"]
        fresh = create_ocr_export(isolated_document)
        fresh_directory = isolated_document / "ocr" / fresh["id"]
        legacy_directory = run / "legacy-migration-fixture"
        shutil.copytree(fresh_directory, legacy_directory,
                        ignore=shutil.ignore_patterns("editor.json", "source.json", "source.zip", "bundle.zip"))
        legacy_manifest = json.loads((legacy_directory / "manifest.json").read_text(encoding="utf-8"))
        for field in ("edit_revision", "source_revision", "edited"):
            legacy_manifest.pop(field, None)
        legacy_manifest["schema"] = 5
        (legacy_directory / "manifest.json").write_text(json.dumps(legacy_manifest, ensure_ascii=False), encoding="utf-8")
        page = (legacy_directory / "index.html").read_text(encoding="utf-8")
        page = re.sub(r'<script type="application/json" id="pl-editor-data">.*?</script>', "", page, flags=re.S)
        (legacy_directory / "index.html").write_text(page, encoding="utf-8")
        legacy_bundle = legacy_directory / "bundle.zip"
        with zipfile.ZipFile(legacy_bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in legacy_directory.rglob("*"):
                if path.is_file() and path != legacy_bundle:
                    archive.write(path, path.relative_to(legacy_directory).as_posix())
        legacy_migration = {"pdf": str(isolated_document / "original.pdf"),
                            "bundle": str(legacy_bundle), "manifest": legacy_manifest}
    app = create_app(settings)
    calls = {"ocr": 0, "llm": 0}
    block_next = threading.Event()
    hold_ocr = threading.Event()
    release_ocr = threading.Event()
    hold_after = None
    @app.get("/api/testing/calls")
    def testing_calls():
        return {**calls, "ocr_held": hold_ocr.is_set()}

    @app.post("/api/testing/hold-second-ocr")
    def testing_hold_second_ocr():
        nonlocal hold_after
        hold_after = calls["ocr"] + 2
        release_ocr.clear()
        return {"ok": True}

    @app.post("/api/testing/release-ocr")
    def testing_release_ocr():
        release_ocr.set()
        return {"ok": True}

    @app.post("/api/testing/block-next")
    def testing_block_next():
        block_next.set()
        return {"ok": True}

    @app.post("/api/testing/revise-ocr")
    def testing_revise_ocr():
        marker = "Latest OCR revision for translation regression."
        block = next(b for b in conversion.ir.blocks if b.type in (BlockType.PARAGRAPH, BlockType.HEADING) and b.content.text)
        block.content.text += " " + marker
        (conversion.doc_dir / "document.json").write_text(conversion.ir.model_dump_json(), encoding="utf-8")
        return {"marker": marker}

    def convert(*args, **kwargs):
        calls["ocr"] += 1
        if calls["ocr"] == hold_after:
            hold_ocr.set()
            try:
                deadline = time.monotonic() + 45
                while not release_ocr.is_set() and time.monotonic() < deadline:
                    kwargs["progress"]("recognizing", 1, 2, "Concurrent OCR fixture")
                    release_ocr.wait(.1)
            finally:
                hold_ocr.clear()
        if block_next.is_set():
            block_next.clear()
            for _ in range(100):
                kwargs["progress"]("recognizing", 1, 2, "Cancellation fixture")
                time.sleep(.1)
        return conversion

    app.state.jobs.orchestrator.convert = convert
    original = translator.translate_batch

    def fake_http(*args):
        calls["llm"] += 1
        prompt = args[3][0]["content"]
        inputs = translator.parse_batch_output(prompt.split("\n请把每条")[0], 10000)
        return "\n".join(f"[ID:{i}] 中文译文：{text}" for i, text in inputs.items())

    translator.translate_batch = lambda *a, **kw: original(*a, **({"http_post": fake_http} | kw))
    (settings.workspace / "settings.json").write_text(json.dumps({
        "llm_base_url": "http://127.0.0.1:1/v1", "llm_model": "smoke", "lang_to": "zh"
    }))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        marionette_port = sock.getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        managed_port = sock.getsockname()[1]
    class LegacyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404 if self.path == "/api/zotero/status" else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"legacy":true}')

        def log_message(self, *args):
            pass

    legacy_server = ThreadingHTTPServer(("127.0.0.1", managed_port), LegacyHandler)
    legacy_thread = threading.Thread(target=legacy_server.serve_forever, daemon=True)
    legacy_thread.start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started
    prefs = {
        "extensions.zotero.useDataDir": True,
        "extensions.zotero.dataDir": str(data_dir),
        "extensions.zotero.firstRun": False,
        "extensions.zotero.sync.autoSync": False,
        "extensions.zotero.automaticScraperUpdates": False,
        "extensions.autoDisableScopes": 0,
        "extensions.logging.enabled": True,
        "browser.dom.window.dump.enabled": True,
        "extensions.enabledScopes": 15,
        "xpinstall.signatures.required": False,
        "browser.shell.checkDefaultBrowser": False,
        "app.update.auto": False,
        "app.update.enabled": False,
        "app.update.disabledForTesting": True,
        "extensions.paperloom.serviceURL": f"http://127.0.0.1:{port}",
        "extensions.zoteroWinWordIntegration.skipInstallation": True,
        "extensions.zoteroOpenOfficeIntegration.skipInstallation": True,
        "marionette.port": marionette_port,
    }
    (profile / "user.js").write_text("\n".join(
        f"user_pref({json.dumps(key)}, {json.dumps(value)});" for key, value in prefs.items()
    ), encoding="utf-8")
    constants = "\n".join([
        f"var smokeResultPath = {json.dumps(str(result_file))};",
        f"var smokePDFPath = {json.dumps(str(ROOT / 'tests' / 'fixtures' / 'sample_native.pdf'))};",
        f"var smokePythonPath = {json.dumps(sys.executable)};",
        f"var smokeManagedURL = {json.dumps(f'http://127.0.0.1:{managed_port}')};",
        f"var smokeQuitBackend = {json.dumps(args.quit_backend)};",
        f"var smokeWorkspacePath = {json.dumps(str(settings.workspace))};",
        f"var smokeScreenshotPath = {json.dumps(str(run / 'integrated-reader.png'))};",
        f"var smokeLibraryScreenshotPath = {json.dumps(str(run / 'library-status.png'))};",
        f"var smokeRealArtifact = {json.dumps(real_artifact)};",
        f"var smokeRealSourceMatches = {json.dumps(real_source_matches)};",
        f"var smokeLegacyMigration = {json.dumps(legacy_migration)};",
        f"var smokeRealTranslation = {json.dumps(json.loads(args.real_translation_fixture.read_text(encoding='utf-8')) if args.real_translation_fixture else None)};",
        f"var smokeActualFigureScreenshotPath = {json.dumps(str(run / 'actual-figure.png'))};",
        f"var smokeActualFormulaScreenshotPath = {json.dumps(str(run / 'actual-formula.png'))};",
    ])
    harness = Path(__file__).with_name("smoke.js").read_text(encoding="utf-8")
    manifest = (ROOT / "zotero" / "manifest.json").read_bytes()
    test_plugin = run / "paperloom-smoke.xpi"
    with zipfile.ZipFile(test_plugin, "w") as bundle:
        bundle.writestr("manifest.json", manifest)
        bundle.writestr("bootstrap.js", (ROOT / "zotero" / "bootstrap.js").read_text(encoding="utf-8") + "\n" + constants + harness)
        for folder in ("modules", "content"):
            for path in (ROOT / "zotero" / folder).iterdir():
                if path.is_file():
                    bundle.write(path, f"{folder}/{path.name}")
        bundle.writestr("config.json", json.dumps({"projectRoot": str(ROOT), "pythonCandidates": [sys.executable], "defaultBackend": "local", "ocrMode": args.ocr_mode}))
        bundle.write(ROOT / "app" / "static" / "table_excel.js", "content/table-excel.js")
        bundle.writestr("content/reader.css", (ROOT / "app/static/offline_reader.css").read_bytes()
                        + b"\n" + (ROOT / "app/static/paper_reader.css").read_bytes())
        bundle.write(ROOT / "app/static/paper_reader.css", "content/paper-reader.css")
        runtime=(ROOT / "zotero/content/paper-layout.mjs").read_text(encoding="utf-8").replace(
            "export { enhance, exportPage };", "window.PaperLoomHTML = {enhance, exportPage}; enhance(document);")
        bundle.writestr("content/paper-reader.js", '(() => {"use strict";\n'+runtime+'\n})();')
        bundle.writestr("smoke-actor.mjs", '''export class PaperLoomSmokeChild extends JSWindowActorChild {
  async receiveMessage(message) {
    const view = this.contentWindow.wrappedJSObject;
    if(message.name==='paper-state'){
      const doc=this.document,style=node=>node?this.contentWindow.getComputedStyle(node):null;
      const paragraph=doc.querySelector('.pl-block[data-paper-role="paragraph"] .pl-target p');
      const caption=doc.querySelector('figcaption .pl-target p');
      const footnote=doc.querySelector('aside[role="doc-footnote"] .pl-target p,aside[role="doc-footnote"] .pl-target li');
      return {enhanced:doc.body.dataset.paperEnhanced,groups:doc.querySelectorAll('figure.paper-media').length,
        figureCaption:!!doc.querySelector('figure.paper-figure > figcaption [data-role="figure_caption"]'),
        tableCaption:!!doc.querySelector('figure.paper-table > figcaption [data-role="table_caption"]'),
        footnotes:doc.querySelectorAll('aside[role="doc-footnote"]').length,
        paragraphSize:style(paragraph)?.fontSize,captionSize:style(caption)?.fontSize,noteSize:style(footnote)?.fontSize,
        ids:Array.from(doc.querySelectorAll('.pl-block')).map(node=>node.dataset.blockId),
        dialog:doc.querySelector('dialog.paper-image-dialog')?.open||false};
    }
    if(message.name==='editor-state')return {editing:this.document.body.classList.contains('pl-editing'),
      fields:this.document.querySelectorAll('.pl-edit-fields').length,draft:JSON.parse(JSON.stringify(view.PaperLoomEditor?.draft||null)),
      status:this.document.getElementById('pl-status')?.textContent};
    if(message.name==='editor-change'){
      const section=Array.from(this.document.querySelectorAll('.pl-block[data-block-type="paragraph"]')).find(node=>JSON.parse(node.dataset.bbox||'[]').length===4);
      if(!section)throw new Error('Editable paragraph missing');
      view.PaperLoomOffline.select(section,false);
      const area=section.querySelector('textarea');area.value='Manually corrected **OCR** sentence with $x^2$.';
      area.dispatchEvent(new this.contentWindow.Event('change',{bubbles:true}));
      section.querySelector('[data-action="editor-insert"]').click();
      const figure=this.document.querySelector('[data-block-type="figure"]');
      view.PaperLoomOffline.select(figure,false);
      figure.querySelector('[data-action="editor-delete"]').click();
      return {block:section.dataset.blockId,page:Number(section.dataset.page),bbox:JSON.parse(section.dataset.bbox),figure:figure.dataset.blockId};
    }
    if(message.name==='editor-role'){
      const section=this.document.querySelector('.pl-block[data-role="figure_caption"]');
      view.PaperLoomOffline.select(section,false);
      const select=section.querySelector('select');select.value='table_caption';
      select.dispatchEvent(new this.contentWindow.Event('change',{bubbles:true}));
      return section.dataset.blockId;
    }
    if(message.name==='clear-selection'){
      this.document.querySelectorAll('.pl-block.selected').forEach(node=>node.classList.remove('selected'));
      this.document.body.classList.remove('has-selection');return true;
    }
    if(message.name==='brand-contract'){
      const nav=this.document.querySelector('nav');
      const oldActorRecognizes=nav?.textContent.includes('PaperLoom')||false;
      if(message.data?.rename){
        nav.querySelectorAll('[hidden]').forEach(node=>node.remove());
        for(const node of nav.childNodes)if(node.nodeType===3)node.textContent=' 用户自定义阅读器名称 ';
        this.document.body.dataset.zoteroBridge='false';
      }
      return {oldActorRecognizes,protocol:this.document.body.dataset.readerProtocol};
    }
    if(message.name==='click'){
      const node=this.document.querySelector(message.data.selector);
      if(!node)throw new Error('Missing test control: '+message.data.selector);
      node.scrollIntoView({block:'center'});node.click();return true;
    }
    if(message.name==='locate-study'){
      const section=Array.from(this.document.querySelectorAll('.pl-block')).find(node=>Number(node.dataset.page)===3&&(node.querySelector('.pl-source')||node.querySelector('.pl-target'))?.textContent.includes('In this study,'));
      if(!section)throw new Error('Actual study paragraph not found');
      section.scrollIntoView({block:'center'});section.querySelector('[data-action="locate"]').click();
      return {id:section.dataset.blockId,bbox:JSON.parse(section.dataset.bbox),page:3};
    }
    if(message.name==='cross-column'){
      const section=Array.from(this.document.querySelectorAll('.pl-block')).find(node=>Number(node.dataset.page)===2&&(node.querySelector('.pl-source')||node.querySelector('.pl-target'))?.textContent.startsWith('A key plant trait'));
      if(!section)throw new Error('Actual cross-column paragraph not found');
      const continuation=Array.from(this.document.querySelectorAll('.pl-block[data-page="2"]')).find(node=>
        node!==section&&JSON.parse(node.dataset.bbox||'[]')[0]>.5&&node.textContent.includes('The LAI of young and mature leaves increased'));
      return {id:section.dataset.blockId,bbox:JSON.parse(section.dataset.bbox),page:2,
        continuation:continuation?{id:continuation.dataset.blockId,bbox:JSON.parse(continuation.dataset.bbox)}:null};
    }
    if (view.MathJax?.startup?.promise) await view.MathJax.startup.promise;
    const texIdentity=value=>Array.from(value.replaceAll(String.fromCharCode(92)+'mathrm','')
      .replaceAll(String.fromCharCode(92)+'textrm','').replaceAll(String.fromCharCode(92)+'text',''))
      .filter(c=>c.charCodeAt(0)>32&&c!=='{'&&c!=='}').join('');
    const para=Array.from(this.document.querySelectorAll('.math-display')).find(node=>texIdentity(node.getAttribute('data-latex')||'').includes('Para='));
    const figure1=Array.from(this.document.querySelectorAll('main img')).find(node=>node.getAttribute('alt')==='图 4');
    const caption5=Array.from(this.document.querySelectorAll('main p')).find(node=>node.textContent.startsWith('Figure 5.'));
    if(message.name==='scroll'){
      const target=message.data.kind==='formula'?para:message.data.kind==='caption'?caption5:figure1;
      const section=target?.closest('.pl-block');
      if(section)view.PaperLoomOffline.select(section,false);
      target?.scrollIntoView({block:'center'});
      await new Promise(resolve=>this.contentWindow.setTimeout(resolve,300));
    }
    return {hasMain: !!this.document.querySelector("main"), text: this.document.body?.textContent?.slice(0, 3000),
      mathVersion: view.MathJax?.version, mathCount: this.document.querySelectorAll("mjx-container").length,
      mathErrors:this.document.querySelectorAll('mjx-merror,[data-mjx-error]').length,
      mathErrorDetails:Array.from(this.document.querySelectorAll('mjx-merror,[data-mjx-error]')).map(node=>({error:node.getAttribute('data-mjx-error'),latex:node.closest('[data-latex]')?.getAttribute('data-latex'),text:node.textContent})),
      paraFraction:!!para?.querySelector('mjx-mfrac'),figure1Ratio:figure1?figure1.naturalWidth/figure1.naturalHeight:0,
      caption5Math:caption5?.querySelectorAll('mjx-msub').length||0,
      translationMode:this.document.body.dataset.translationMode,bridge:this.document.body.dataset.zoteroBridge,
      actionReceived:this.document.body.dataset.actionReceived,status:this.document.getElementById('pl-status')?.textContent,
      bridgeState:this.document.body.dataset.readerBridgeState,bridgeError:this.document.body.dataset.readerBridgeError,
      mathControlsReady:this.document.body.dataset.mathControlsReady,mathCopyButtons:this.document.querySelectorAll('[data-action="copy-latex"]').length,
      firstMathHTML:this.document.querySelector('main .pl-target [data-latex]')?.outerHTML.slice(0,1200),
      firstLatex:this.document.querySelector('main .pl-target [data-latex]')?.dataset.latex,
      visibleSources:Array.from(this.document.querySelectorAll('.pl-source')).filter(node=>this.contentWindow.getComputedStyle(node).display!=='none').length,
      sourceCount:this.document.querySelectorAll('.pl-source').length,blockCount:this.document.querySelectorAll('.pl-block').length,
      selectedBlock:this.document.querySelector('.pl-block.selected')?.dataset.blockId,
      firstPosition:(()=>{const node=Array.from(this.document.querySelectorAll('.pl-block')).find(node=>Number(node.dataset.page)>0&&JSON.parse(node.dataset.bbox||'[]').length===4);return node?{block:node.dataset.blockId,page:Number(node.dataset.page),bbox:JSON.parse(node.dataset.bbox)}:null})(),
      bodyFont: this.contentWindow.getComputedStyle(this.document.body).fontSize,
      sourceFont:(()=>{const node=this.document.querySelector('.pl-block[data-block-type="paragraph"] .pl-source p');return node?this.contentWindow.getComputedStyle(node).fontSize:null})(),
      targetFont:(()=>{const node=this.document.querySelector('.pl-block[data-block-type="paragraph"] .pl-target p');return node?this.contentWindow.getComputedStyle(node).fontSize:null})(),
      appearance:(()=>{
        const source=this.document.querySelector('.pl-source p')||this.document.querySelector('.pl-target p');
        const target=this.document.querySelector('.pl-source+.pl-target p');
        const a=source&&this.contentWindow.getComputedStyle(source),b=target&&this.contentWindow.getComputedStyle(target);
        return {ocrColor:a?.color,translationColor:b?.color,ocrFamily:a?.fontFamily,translationFamily:b?.fontFamily};
      })(),
      layout:(()=>{
        const blocks=Array.from(this.document.querySelectorAll('.pl-block[data-block-type="paragraph"]'));
        const first=blocks.find(node=>node.nextElementSibling?.dataset.blockType==='paragraph');
        const lastText=first?.querySelector('.pl-target')?.lastElementChild;
        const nextSource=first?.nextElementSibling?.querySelector('.pl-source');
        const nextText=(nextSource&&this.contentWindow.getComputedStyle(nextSource).display!=='none'?nextSource:first?.nextElementSibling?.querySelector('.pl-target'))?.firstElementChild;
        const source=this.document.querySelector('.pl-source p')?.closest('.pl-source');
        const sourceLast=source?.lastElementChild;
        const targetFirst=source?.nextElementSibling?.firstElementChild;
        return {gap:lastText&&nextText?nextText.getBoundingClientRect().top-lastText.getBoundingClientRect().bottom:null,
          pairGap:sourceLast&&targetFirst?targetFirst.getBoundingClientRect().top-sourceLast.getBoundingClientRect().bottom:null,
          lineHeight:this.contentWindow.getComputedStyle(this.document.body).lineHeight,
          compactStyle:!!this.document.getElementById('paperloom-reader-layout')};
      })(),
      url: this.document.documentURI};
  }
}''')
    log_path = run / "zotero.log"
    print(f"Isolated Zotero smoke test: {run}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [args.zotero, "-no-remote", "-profile", str(profile), "-headless", "-ZoteroDebugText",
             "--marionette", "--remote-allow-system-access"],
            stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "MOZ_HEADLESS": "1", "TEMP": str(isolated_temp), "TMP": str(isolated_temp),
                 "MOZ_HEADLESS_WIDTH": "1800", "MOZ_HEADLESS_HEIGHT": "1100",
                 "PAPERBRIDGE_WORKSPACE": str(run / "managed-workspace"), "PAPERLOOM_LAUNCHER_LOCK": str(ROOT / "scripts" / "launcher.lock" if args.quit_backend == "wsl" else run / "launcher.lock")},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            # Install through the real AddonManager so manifest validation errors
            # are returned directly instead of silently dropping sideloaded XPIs.
            remote = None
            for _ in range(60):
                try:
                    remote = socket.create_connection(("127.0.0.1", marionette_port), timeout=1)
                    break
                except OSError:
                    time.sleep(1)
            if remote is None:
                raise RuntimeError(f"Marionette did not start; see {log_path}")
            remote.settimeout(30)

            def receive():
                length = b""
                while not length.endswith(b":"):
                    chunk = remote.recv(1)
                    if not chunk:
                        raise RuntimeError("Marionette disconnected")
                    length += chunk
                data = b""
                while len(data) < int(length[:-1]):
                    data += remote.recv(int(length[:-1]) - len(data))
                return json.loads(data)

            sequence = 0

            def command(name, payload):
                nonlocal sequence
                sequence += 1
                body = json.dumps([0, sequence, name, payload]).encode()
                remote.sendall(str(len(body)).encode() + b":" + body)
                response = receive()
                while response[1] != sequence:
                    response = receive()
                if response[2]:
                    raise RuntimeError(response[2])
                return response[3]

            receive()
            # Zotero has no Firefox sessionstore startup notification. Start the
            # session, then explicitly select its chrome window while NewSession
            # is awaiting that notification.
            sequence += 1
            body = json.dumps([0, sequence, "WebDriver:NewSession", {"capabilities": {}}]).encode()
            remote.sendall(str(len(body)).encode() + b":" + body)
            time.sleep(1)
            command("Marionette:SetContext", {"value": "chrome"})
            print("Marionette chrome context ready", flush=True)
            handles = []
            for _ in range(60):
                handles = command("WebDriver:GetWindowHandles", {})
                if handles:
                    break
                time.sleep(1)
            if not handles:
                raise RuntimeError("No Zotero test window appeared")
            command("WebDriver:SwitchToWindow", {"handle": handles[0]})
            install = command("Addon:Install", {"path": str(test_plugin), "temporary": True})
            print(f"AddonManager: {install}", flush=True)
            remote.close()
            for _ in range(600 if args.quit_backend == "wsl" else 180):
                if result_file.exists():
                    break
                time.sleep(1)
            if not result_file.exists():
                raise RuntimeError(f"Zotero test did not finish; see {log_path}")
            report = json.loads(result_file.read_text(encoding="utf-8"))
            if report.get("ok") and report.get("quitService"):
                # Assert natural shutdown BEFORE fallback cleanup: killing the
                # test process tree here used to hide orphaned API processes.
                import urllib.request
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(report["quitService"]["url"] + "/api/zotero/status", timeout=1):
                            pass
                    except OSError:
                        break
                    time.sleep(.25)
                else:
                    raise RuntimeError("API survived Zotero quit")
                from scripts.zotero_service import acquire_lock
                old_lock = os.environ.get("PAPERLOOM_LAUNCHER_LOCK")
                os.environ["PAPERLOOM_LAUNCHER_LOCK"] = str(ROOT / "scripts" / "launcher.lock" if args.quit_backend == "wsl" else run / "launcher.lock")
                try:
                    lock = acquire_lock(ROOT)
                    lock.close()
                finally:
                    if old_lock is None:
                        os.environ.pop("PAPERLOOM_LAUNCHER_LOCK", None)
                    else:
                        os.environ["PAPERLOOM_LAUNCHER_LOCK"] = old_lock
                report["checks"].append("real Zotero quit stops owned API and releases launcher lock without test cleanup")
                if args.quit_backend == "wsl":
                    active = subprocess.run(["wsl", "-u", "root", "-e", "systemctl", "is-active", "wsl-fastdeploy-vl16"],
                        capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                    assert active.returncode != 0, active.stdout
                    report["checks"].append("real Zotero quit stops the dedicated WSL model service")
            report["service_calls"] = calls
            result_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = {key: report[key] for key in ("version", "ok", "checks", "service_calls", "error") if key in report}
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            if not report.get("ok"):
                raise SystemExit(1)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            # On Windows Zotero's launcher can exit and leave its child running.
            # Only stop processes explicitly using this disposable profile.
            import psutil
            for candidate in psutil.process_iter(["name", "cmdline"]):
                try:
                    command = candidate.info["cmdline"] or []
                    if candidate.info["name"].lower() != "zotero.exe" or "-profile" not in command:
                        continue
                    if command[command.index("-profile") + 1] != str(profile):
                        continue
                    for child in candidate.children(recursive=True):
                        child.terminate()
                    candidate.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            server.should_exit = True
            thread.join(timeout=5)
            legacy_server.shutdown()
            legacy_server.server_close()
            legacy_thread.join(timeout=5)
            translator.translate_batch = original


if __name__ == "__main__":
    main()
