"""Brand-independent saved-reader protocol, actor lifetime and upgrade contracts."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime required")
    modules = (ROOT / "zotero/content").as_uri()
    prelude = f"""
import assert from 'node:assert/strict';
globalThis.JSWindowActorChild = class {{}};
globalThis.JSWindowActorParent = class {{}};
const protocol = await import({json.dumps(modules + '/reader-protocol.mjs')});
const {{PaperLoomReaderChild: Child}} = await import({json.dumps(modules + '/reader-child.mjs')});
const {{PaperLoomReaderParent: Parent}} = await import({json.dumps(modules + '/reader-parent.mjs')});
function documentFor(dataset={{readerProtocol:protocol.READER_PROTOCOL}}, nodes={{}}) {{
  const events=[];
  return {{body:{{dataset,hasAttribute:name=>name==='data-export-schema'&&dataset.exportSchema!==undefined}},
    querySelector:selector=>nodes[selector]||null, dispatchEvent:event=>events.push(event),events}};
}}
function actor(document=documentFor()) {{
  const value = new Child(); value.document=document;
  const timers=new Map(); let sequence=0;
  value.contentWindow={{setTimeout:fn=>{{timers.set(++sequence,fn);return sequence;}},
    clearTimeout:id=>timers.delete(id),CustomEvent:class {{constructor(type,options){{this.type=type;Object.assign(this,options);}}}}}};
  value.applyView=()=>{{}};value.applyStyle=()=>{{}};value.report=()=>{{}};
  value.timers=timers;return value;
}}
"""
    result = subprocess.run([node, "--input-type=module", "-"], input=prelude + script,
                            text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr


def test_new_exports_keep_old_actor_compatibility_without_visible_legacy_brand():
    from app.services.translation_export import reader_page

    for original in (None, "Source paragraph"):
        page = reader_page("Sample", "en", "A paragraph", original)
        assert 'data-reader-protocol="paperloom.saved-reader/1"' in page
        nav = re.search(r"<nav>(.*?)</nav>", page, re.S)[1]
        assert 'hidden aria-hidden="true" data-reader-legacy-marker="PaperLoom"' in nav
        # v0.2.15 used nav.textContent.includes('PaperLoom'). Keep those already
        # installed actors working with new server exports, without changing UI.
        assert "PaperLoom" in re.sub(r"<[^>]+>", "", nav)
        assert "论文翻译大师" in nav


def test_protocol_accepts_old_new_and_edited_titles_but_never_uses_brand_labels():
    run_js("""
assert.equal(protocol.isSavedReader(documentFor()),true);
assert.equal(protocol.isSavedReader(documentFor({exportSchema:'7'})),true);
assert.equal(protocol.isSavedReader(documentFor({}, {'.pl-block[data-block-id]':{}})),true);
const oldPage=documentFor({}, {'main':{},'script[src="assets/offline_reader.js"]':{},
  'nav a[href="ocr.md"],nav a[href="translation.md"]':{}});
assert.equal(protocol.isSavedReader(oldPage),true);
assert.equal(protocol.isSavedReader(documentFor({}, {'nav':{textContent:'PaperLoom'}})),false);
assert.equal(protocol.isSavedReader(documentFor({}, {'nav':{textContent:'论文翻译大师'}})),false);
assert.equal(protocol.isSavedReader(documentFor({readerProtocol:'unknown/9'})),false);
""")


def test_parent_preserves_frame_callbacks_observer_topic_and_action_contract():
    run_js("""
const notifications=[]; const actions=[];
globalThis.Services={obs:{notifyObservers:(...args)=>notifications.push(args)}};
const frame={isConnected:true,_paperloomOptions:()=>({view:{focus:true},css:'custom'}),
  _paperloomAction:async data=>{actions.push(data);return 'done';}};
const parent=new Parent();parent.browsingContext={id:12,embedderElement:frame};
const config=parent.receiveMessage({name:'ready'});
assert.equal(config.protocol,protocol.READER_PROTOCOL);assert.equal(config.available,true);
assert.deepEqual(config.view,{focus:true});assert.equal(config.css,'custom');
assert.deepEqual(notifications,[[null,'paperloom-reader-ready','12']]);
for(const type of protocol.ACTION_TYPES){
  assert.equal(await parent.receiveMessage({name:'action',data:{id:7,type}}),'done');
}
assert.equal(actions.length,5);
assert.throws(()=>parent.receiveMessage({name:'action',data:{type:'execute'}}),/不支持/);
frame.isConnected=false;
assert.equal(parent.receiveMessage({name:'ready'}).available,false);
assert.throws(()=>parent.receiveMessage({name:'action',data:{type:'locate'}}),/窗口已经关闭/);
""")


def test_child_recovers_when_ready_precedes_parent_binding_and_style_errors_are_independent():
    run_js("""
const child=actor();let bound=false;let queries=0;
child.sendQuery=async name=>{assert.equal(name,'ready');queries++;return {available:bound,view:{focus:true}};};
await child.connect();
assert.equal(child.document.body.dataset.zoteroBridge,'false');
assert.equal(child.document.body.dataset.readerBridgeState,'waiting');
assert.equal(child.timers.size,1);
bound=true;child.applyStyle=()=>{throw Error('Bad optional CSS');};
await child.receiveMessage({name:'reader-connect'});
assert.equal(child.document.body.dataset.zoteroBridge,'true');
assert.equal(child.document.body.dataset.readerBridgeState,'ready');
assert.equal(child.document.body.dataset.readerBridgeError,undefined);
assert.equal(child.timers.size,0);assert.equal(queries,2);
""")


def test_proactive_handshake_waits_for_deferred_runtime_and_shares_dom_ready_promise():
    run_js("""
const document=documentFor();document.readyState='loading';
const events=new Map();
document.addEventListener=(name,callback)=>events.set(name,callback);
document.removeEventListener=(name,callback)=>{if(events.get(name)===callback)events.delete(name);};
const child=actor(document);let queries=0;
child.sendQuery=async()=>{queries++;return {available:true};};
const first=child.receiveMessage({name:'reader-connect'});
await Promise.resolve();await Promise.resolve();
assert.equal(queries,0);assert.notEqual(document.body.dataset.zoteroBridge,'true');
assert.equal(document.body.dataset.readerBridgeState,'connecting');
assert.equal(child.timers.size,1);
document.readyState='interactive';
child.handleEvent({type:'DOMContentLoaded'}); // Must not replace or deadlock the pending handshake.
events.get('DOMContentLoaded')();
assert.equal((await first).available,true);
assert.equal(queries,1);assert.equal(document.body.dataset.zoteroBridge,'true');
assert.equal(child.timers.size,0);assert.equal(events.size,0);
""")


def test_loading_document_can_be_destroyed_or_time_out_without_hanging_handshake():
    run_js("""
{
 const document=documentFor();document.readyState='loading';
 document.addEventListener=()=>{throw Error('A destroyed actor must not start waiting');};
 const child=actor(document);const pending=child.connect();child.didDestroy();
 assert.equal((await pending).available,false);assert.equal(child.timers.size,0);
}
for(const close of [true,false]){
 const document=documentFor();document.readyState='loading';const events=new Map();
 document.addEventListener=(name,callback)=>events.set(name,callback);
 document.removeEventListener=name=>events.delete(name);
 const child=actor(document);let queries=0;
 child.sendQuery=async()=>{queries++;return {available:true};};
 const pending=child.connect();await Promise.resolve();
 if(close)child.didDestroy();else Array.from(child.timers.values())[0]();
 assert.equal((await pending).available,false);assert.equal(queries,0);
 assert.notEqual(document.body.dataset.zoteroBridge,'true');assert.equal(events.size,0);
 if(!close){assert.match(document.body.dataset.readerBridgeError,/加载超时/);child.cancelRetry();}
}
""")


def test_interactive_document_with_pending_defer_runtime_waits_for_dom_content_loaded():
    run_js("""
const document=documentFor(undefined,{'script[src="assets/offline_reader.js"]':{}});
document.readyState='interactive';const events=new Map();
document.addEventListener=(name,callback)=>events.set(name,callback);
document.removeEventListener=name=>events.delete(name);
const child=actor(document);let queries=0;
child.sendQuery=async()=>{queries++;return {available:true};};
const pending=child.connect();await Promise.resolve();await Promise.resolve();
assert.equal(queries,0);assert.notEqual(document.body.dataset.zoteroBridge,'true');
assert.equal(child.timers.size,1);
child.contentWindow.wrappedJSObject={PaperLoomOffline:{action:()=>{}}};
events.get('DOMContentLoaded')();
assert.equal((await pending).available,true);assert.equal(queries,1);assert.equal(child.timers.size,0);
""")


def test_late_actor_accepts_existing_runtime_or_complete_or_legacy_static_document():
    run_js("""
for(const kind of ['runtime','complete','static']){
 const nodes=kind==='static'?{}:{'script[src="assets/offline_reader.js"]':{}};
 const document=documentFor(undefined,nodes);document.readyState=kind==='complete'?'complete':'interactive';
 document.addEventListener=()=>{throw Error('Must not wait for an already completed page');};
 const child=actor(document);
 if(kind==='runtime')child.contentWindow.wrappedJSObject={PaperLoomOffline:{action:()=>{}}};
 child.sendQuery=async()=>({available:true});
 assert.equal((await child.connect()).available,true);
 assert.equal(document.body.dataset.zoteroBridge,'true');assert.equal(child.timers.size,0);
}
""")


def test_parent_options_failures_keep_native_actions_connected_and_observable():
    run_js("""
globalThis.Services={obs:{notifyObservers:()=>{}}};
const frame={isConnected:true,_paperloomAction:async data=>data.type};
const parent=new Parent();parent.browsingContext={id:23,embedderElement:frame};
for(const provider of [
 ()=>{throw Error('Appearance preferences unavailable');},
 ()=>({get css(){throw Error('Appearance preferences unavailable');}}),
]){
 frame._paperloomOptions=provider;
 const config=parent.receiveMessage({name:'ready'});
 assert.equal(config.available,true);assert.equal(config.protocol,protocol.READER_PROTOCOL);
 assert.deepEqual(config.view,{});assert.equal(config.css,'');
 assert.equal(config.optionError,'Appearance preferences unavailable');
 assert.equal(await parent.receiveMessage({name:'action',data:{type:'locate'}}),'locate');
 const child=actor();child.sendQuery=async()=>config;
 await child.connect();assert.equal(child.document.body.dataset.zoteroBridge,'true');
 assert.equal(child.document.body.dataset.readerBridgeState,'ready');
}
frame.isConnected=false;assert.equal(protocol.frameConfig(frame).available,false);
""")


def test_failed_initialization_is_observable_bounded_and_explicitly_retryable():
    run_js("""
const child=actor();child.sendQuery=()=>{throw Error('Actor module unavailable');};
for(let i=0;i<6;i++){await child.connect();child.cancelRetry();}
assert.equal(child.connecting,null);
assert.equal(child.document.body.dataset.readerBridgeState,'error');
assert.match(child.document.body.dataset.readerBridgeError,/连接未完成/);
child.sendQuery=async()=>({available:true});
await child.receiveMessage({name:'reader-connect'});
assert.equal(child.document.body.dataset.zoteroBridge,'true');
assert.equal(child.document.body.dataset.readerBridgeState,'ready');
""")


def test_old_actions_keep_explicit_locate_copy_excel_edit_and_translation_semantics():
    run_js("""
const child=actor();const actions=[];
child.sendQuery=async(name,data)=>{actions.push(data);return true;};
child.handleEvent({type:'click',target:{closest:()=>true}});
child.handleEvent({type:'PaperLoomAction',detail:JSON.stringify({id:1,type:'locate',block:'p1'})});
child.handleEvent({type:'PaperLoomAction',detail:JSON.stringify({id:2,type:'locate',block:'p2'})});
for(const type of ['copy-latex','excel','save-ocr-edits','translation-mode']){
 child.handleEvent({type:'PaperLoomAction',detail:JSON.stringify({id:type,type,text:'x^2',items:[],changes:[]})});
}
await new Promise(resolve=>setImmediate(resolve));
assert.equal(actions[0].revealPDF,true);assert.equal(actions[1].revealPDF,false);
assert.equal(child.document.events.length,6);
assert.equal(JSON.parse(child.document.events[0].detail).id,1);
assert.equal(actions[2].text,'x^2');assert.deepEqual(actions[4].changes,[]);
""")


def test_responses_from_replaced_or_destroyed_pages_are_discarded():
    run_js("""
const first=documentFor();const child=actor(first);let finish;
child.sendQuery=()=>new Promise(resolve=>finish=resolve);
child.handleEvent({type:'PaperLoomAction',detail:'{"id":1,"type":"save-ocr-edits"}'});
await Promise.resolve();
const replacement=documentFor();child.document=replacement;finish(true);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(first.events.length,0);assert.equal(replacement.events.length,0);
child.result({id:2,value:true},first);assert.equal(replacement.events.length,0);
child.didDestroy();child.result({id:3,value:true},replacement);
assert.equal(replacement.events.length,0);
""")


def test_handshake_response_cannot_apply_to_a_replaced_page():
    run_js("""
const child=actor();let finish;
child.sendQuery=()=>new Promise(resolve=>finish=resolve);
const pending=child.connect();await new Promise(resolve=>setImmediate(resolve));
const next=documentFor();child.document=next;finish({available:true,view:{focus:true}});
assert.equal((await pending).available,false);
assert.equal(next.body.dataset.zoteroBridge,undefined);
""")


def run_offline_js(script, before=""):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime required")
    prelude = """
const assert=require('node:assert/strict');
const vm=require('node:vm');
const listeners=new Map(),windowListeners=new Map(),sent=[];
const body={dataset:{},append:()=>{}};
const document={body,querySelectorAll:()=>[],querySelector:()=>null,
 addEventListener:(name,callback)=>listeners.set(name,callback),
 dispatchEvent:event=>{sent.push(event);listeners.get(event.type)?.(event);},
 createElement:()=>({nodeType:1,dataset:{},classList:{add:()=>{}},addEventListener:()=>{},
   matches:selector=>selector==='button[data-action="copy-latex"]',select:()=>{},remove:()=>{}}),execCommand:()=>true};
const window={addEventListener:(name,callback)=>windowListeners.set(name,callback)};
globalThis.document=document;globalThis.window=window;
globalThis.CustomEvent=class{constructor(type,options){this.type=type;Object.assign(this,options);}};
"""
    source = (ROOT / "app/static/offline_reader.js").read_text(encoding="utf-8")
    result = subprocess.run([node, "-"], input=prelude + before + "\nvm.runInThisContext(" + json.dumps(source) + ");\n(async()=>{\n" + script + "\n})().catch(error=>{console.error(error);process.exitCode=1;});",
                            text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr


def test_offline_actions_reconnect_before_locating_and_return_native_errors():
    run_offline_js("""
body.dataset.readerBridgeState='waiting';body.dataset.zoteroBridge='false';
listeners.set('PaperLoomReaderConnect',()=>{setTimeout(()=>{body.dataset.zoteroBridge='true';body.dataset.readerBridgeState='ready';},10);});
listeners.set('PaperLoomAction',event=>{
 const data=JSON.parse(event.detail);
 document.dispatchEvent(new CustomEvent('PaperLoomActionResult',{detail:JSON.stringify({id:data.id,value:data.type})}));
});
assert.equal(await window.PaperLoomOffline.action({type:'locate',block:'p1'}),'locate');
assert.equal(sent.filter(event=>event.type==='PaperLoomAction').length,1);
listeners.set('PaperLoomAction',event=>{
 const data=JSON.parse(event.detail);
 document.dispatchEvent(new CustomEvent('PaperLoomActionResult',{detail:JSON.stringify({id:data.id,error:'Reader closed'})}));
});
await assert.rejects(window.PaperLoomOffline.action({type:'copy-latex',text:'x^2'}),/Reader closed/);
""")


def test_offline_page_closure_releases_pending_editor_request():
    run_offline_js("""
body.dataset.zoteroBridge='true';
const pending=window.PaperLoomOffline.action({type:'save-ocr-edits',changes:[]});
await Promise.resolve();
windowListeners.get('pagehide')();
await assert.rejects(pending,/已经关闭/);
""")


def test_offline_page_reports_missing_native_navigation_instead_of_silent_noop():
    run_offline_js("""
await assert.rejects(window.PaperLoomOffline.action({type:'locate',block:'p1'}),/Zotero/);
await window.PaperLoomOffline.action({type:'copy-latex',text:'x^2'});
body.dataset.readerBridgeState='error';body.dataset.readerBridgeError='Module failed';
await assert.rejects(window.PaperLoomOffline.action({type:'locate'}),/Module failed/);
""")


def test_formula_copy_controls_survive_async_mathjax_startup_and_repeated_installation():
    run_offline_js("""
assert.equal(math.children.length,1);
assert.equal(body.dataset.mathControlsReady,'false');
math.children=[]; // MathJax's initial typeset replaced the original children.
resolveMath();await window.PaperLoomOffline.mathControlsReady;
assert.equal(math.children.length,1);assert.equal(body.dataset.mathControlsReady,'true');
window.PaperLoomOffline.refreshMathControls();window.PaperLoomOffline.refreshMathControls();
assert.equal(math.children.length,1);
assert.equal(math.children[0].dataset.action,'copy-latex');
""", before="""
let resolveMath;
window.MathJax={startup:{promise:new Promise(resolve=>resolveMath=resolve)}};
const math={dataset:{latex:'E=mc^2'},children:[],append(node){this.children.push(node);},
 querySelector(){return this.children.find(node=>node.dataset.action==='copy-latex');}};
document.querySelectorAll=selector=>selector==='[data-latex]'?[math]:[];
""")


def test_later_typeset_replacement_repairs_formula_controls_without_duplicates():
    run_offline_js("""
await window.PaperLoomOffline.mathControlsReady;
const removed=math.children[0];math.children=[];
observer.callback([{addedNodes:[],removedNodes:[removed]}]);
await Promise.resolve();assert.equal(math.children.length,1);
observer.callback([{addedNodes:[math.children[0]],removedNodes:[]}]);
await Promise.resolve();assert.equal(math.children.length,1);
windowListeners.get('pagehide')();assert.equal(observer.disconnected,true);
math.children=[];window.PaperLoomOffline.refreshMathControls();assert.equal(math.children.length,0);
""", before="""
let observer;
globalThis.MutationObserver=class{
 constructor(callback){this.callback=callback;observer=this;}
 observe(){}disconnect(){this.disconnected=true;}
};
const math={dataset:{latex:'x^2'},children:[],append(node){this.children.push(node);},
 querySelector(){return this.children.find(node=>node.dataset.action==='copy-latex');}};
document.querySelectorAll=selector=>selector==='[data-latex]'?[math]:[];
""")


def test_mathjax_failure_still_leaves_copyable_latex_controls():
    run_offline_js("""
await window.PaperLoomOffline.mathControlsReady;
assert.equal(math.children.length,1);assert.equal(body.dataset.mathControlsReady,'true');
assert.equal(body.dataset.mathControlsError,'Font failed');
""", before="""
window.MathJax={startup:{promise:Promise.reject(Error('Font failed'))}};
const math={dataset:{latex:'x^2'},children:[],append(node){this.children.push(node);},
 querySelector(){return this.children.find(node=>node.dataset.action==='copy-latex');}};
document.querySelectorAll=selector=>selector==='[data-latex]'?[math]:[];
""")
