"""Reader component contracts: presentation cannot own navigation or sessions."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_js(modules, body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required")
    setup = """
const assert=require('node:assert/strict');const prefs={};
const PaperLoom={alive:true,pref:(key,fallback)=>prefs[key]??fallback,setPref:(key,value)=>prefs[key]=value,
  Appearance:{read:()=>({})},ReaderUI:{},readerStyle:'body{color:inherit}'};
const Zotero={logError:()=>{},Utilities:{Internal:{copyTextToClipboard:text=>{Zotero.clipboard=text;}}}};
function frame(name){const global={documentURI:{scheme:'file',spec:'file:///'+name}};
  return {currentURI:global.documentURI,browsingContext:{id:name,currentWindowGlobal:global}};}
"""
    source = setup + "\n".join((ROOT / f"zotero/modules/{module}.js").read_text(encoding="utf-8") for module in modules)
    result = subprocess.run([node, "-"], input=source + "\n(async()=>{\n" + body + "\n})().catch(error=>{console.error(error);process.exitCode=1;});",
                            encoding="utf-8", text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_font_changes_do_not_depend_on_actor_or_pdf_initialization():
    run_js(["reader-presentation"], """
const state={frame:frame('ocr'),fontSize:{},lastLocated:{block:'a'}};
PaperLoom.ReaderUI.readerActor=()=>{throw new Error('Actor not loaded');};
await PaperLoom.ReaderPresentation.setFont(24,new Map([[1,state]]));
assert.equal(state.frame.browsingContext.textZoom,24/17);assert.equal(state.fontSize.value,'24');
assert.equal(state.lastLocated.block,'a');
await assert.rejects(PaperLoom.ReaderPresentation.applyStyle(state),/Actor/);
assert.equal(state.lastLocated.block,'a');
""")


def test_presentation_changes_cannot_unregister_action_callbacks():
    run_js(["reader-presentation", "reader-session", "reader-actions"], """
const state={frame:frame('ocr'),uri:'file:///ocr',saved:{},mode:'ocr'};
PaperLoom.ReaderUI.action=(state,data)=>PaperLoom.ReaderActions.dispatch(state,data);
PaperLoom.ReaderUI.viewOptions=()=>PaperLoom.ReaderPresentation.viewOptions();
PaperLoom.ReaderUI.readerActor=()=>({sendQuery:async()=>({available:true})});
PaperLoom.ReaderSession.bind(state);const callback=state.frame._paperloomAction;
await PaperLoom.ReaderPresentation.applyStyle(state);await PaperLoom.ReaderPresentation.applyFont(state);
assert.equal(state.frame._paperloomAction,callback);
await callback({type:'copy-latex',text:'x^2'});assert.equal(Zotero.clipboard,'x^2');
state.frame=frame('translation');assert.throws(()=>callback({type:'copy-latex',text:'old'}),/更新/);
""")


def test_failed_connection_can_recover_and_old_connection_cannot_override_new_frame():
    run_js(["reader-session"], """
const state={frame:frame('ocr'),uri:'file:///ocr',saved:{}};
PaperLoom.ReaderUI.readerActor=()=>{throw new Error('Transient actor import');};
assert.equal(await PaperLoom.ReaderSession.connect(state),false);assert.equal(state.bridgePending,null);
let finish;PaperLoom.ReaderUI.readerActor=()=>({sendQuery:()=>new Promise(resolve=>finish=resolve)});
const old=PaperLoom.ReaderSession.connect(state);await Promise.resolve();
PaperLoom.ReaderSession.unbind(state);state.frame=frame('translation');state.uri='file:///translation';
PaperLoom.ReaderUI.readerActor=()=>({sendQuery:async()=>({available:true})});
assert.equal(await PaperLoom.ReaderSession.connect(state),true);
finish({available:true});assert.equal(await old,false);
assert.equal(state.bridgeGlobal,state.frame.browsingContext.currentWindowGlobal);assert.equal(state.bridgeStatus,'ready');
""")


def test_local_text_selection_and_explicit_compare_have_separate_actions():
    run_js(["reader-session", "reader-actions"], """
const state={frame:frame('ocr'),uri:'file:///ocr',saved:{},mode:'ocr'};let locations=0;
PaperLoom.ReaderUI.setMode=async(_reader,mode)=>state.mode=mode;
PaperLoom.ReaderUI.locate=async()=>locations++;
await PaperLoom.ReaderActions.dispatch(state,{type:'locate',revealPDF:false});
assert.equal(state.mode,'ocr');assert.equal(locations,0);
await PaperLoom.ReaderActions.dispatch(state,{type:'locate',revealPDF:true});
assert.equal(state.mode,'split');assert.equal(locations,1);
state.frame.currentURI={spec:'file:///ocr#footnote-1'};
await PaperLoom.ReaderActions.dispatch(state,{type:'copy-latex',text:'z_1'});assert.equal(Zotero.clipboard,'z_1');
state.frame.currentURI={spec:'file:///another'};
await assert.rejects(PaperLoom.ReaderActions.dispatch(state,{type:'copy-latex',text:'wrong page'}),/更新/);
""")


def test_component_dependencies_do_not_cross_presentation_and_pdf_boundaries():
    for module in ("reader-layout", "reader-presentation"):
        text = (ROOT / f"zotero/modules/{module}.js").read_text(encoding="utf-8")
        assert "SourceRegions" not in text and "Comparison" not in text
        assert "_paperloomAction" not in text
    highlight = (ROOT / "zotero/modules/pdf-highlight.js").read_text(encoding="utf-8")
    assert ".navigate(" not in highlight and "sendQuery" not in highlight
    protocol = (ROOT / "zotero/content/reader-protocol.mjs").read_text(encoding="utf-8")
    assert "textContent.includes" not in protocol
