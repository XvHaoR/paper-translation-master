"""PDF navigation contracts run without Zotero, model loading or real files."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const assert = require('node:assert/strict');
const errors=[], prefs={blockCompare:true};
const PaperLoom={alive:true,pref:(key,fallback)=>prefs[key]??fallback,
  ReaderUI:{ready:async()=>{},readerActor:()=>({sendQuery:async()=>null})},
  SourceRegions:{resolve:async(_state,data)=>[{page:data.page,bbox:data.bbox}],find:async()=>null}};
const Zotero={logError:error=>errors.push(error)};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
class Node {
  constructor(tag){this.tag=tag;this.style={opacity:''};this.attrs={};this.children=[];this.dataset={};
    const classes=new Set();this.classList={add:value=>classes.add(value),remove:value=>classes.delete(value),contains:value=>classes.has(value)};}
  setAttribute(name,value){this.attrs[name]=value;}
  append(child){this.children.push(child);child.parent=this;child.isConnected=true;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);this.isConnected=false;}
}
function fixture(){
  const pages=[1,2,3].map(number=>{const node=new Node('page');node.dataset.pageNumber=String(number);
    node.getBoundingClientRect=()=>({left:0,top:0,width:1000,height:1000});node.closest=()=>node;return node;});
  const handlers=new Map();
  const doc={defaultView:{getSelection:()=>({isCollapsed:true})},
    createElementNS:(_ns,tag)=>new Node(tag),
    querySelectorAll:selector=>selector==='.page'?pages:pages.filter(node=>node.classList.contains('paperloom-dimmed-page')),
    addEventListener:(name,callback)=>handlers.set(name,callback),
    removeEventListener:(name,callback)=>{if(handlers.get(name)===callback)handlers.delete(name);}};
  const viewport={width:1000,height:1000,convertToPdfPoint:(x,y)=>[x,1000-y]};
  const pdf={numPages:3};const viewer={getPageView:index=>({div:pages[index],viewport})};
  const app={pdfDocument:pdf,pdfViewer:viewer};const win={document:doc,PDFViewerApplication:app};
  const view={_iframeWindow:win},navigated=[];
  const state={reader:{_internalReader:{_primaryView:view},navigate:async value=>navigated.push(value)},
    mode:'split',saved:{},frame:{browsingContext:{currentWindowGlobal:{}}}};
  return{state,doc,pages,app,pdf,view,viewer,viewport,navigated,handlers};
}
"""


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime required")
    modules = ["pdf-context", "pdf-regions", "pdf-highlight", "comparison"]
    source = HARNESS + "\n".join(
        (ROOT / "zotero/modules" / f"{name}.js").read_text(encoding="utf-8")
        for name in modules
    )
    process = subprocess.run(
        [node, "-"], input=source + "\n(async()=>{\n" + script
        + "\n})().catch(error=>{console.error(error);process.exitCode=1;});",
        text=True, encoding="utf-8", capture_output=True,
    )
    assert process.returncode == 0, process.stderr


def test_navigation_works_both_directions_with_highlighting_disabled():
    run_js(r"""
const {state,doc,pages,navigated}=fixture();prefs.blockCompare=false;
const data={block:'ocr-only',page:1,bbox:[.1,.2,.3,.4]};
await PaperLoom.Comparison.locate(state,data);
assert.deepEqual(navigated,[{position:{pageIndex:0,rects:[[100,600,300,800]]}}]);
assert.equal(state.pdfHighlight,null);assert.equal(pages[1].style.opacity,'');
const reverse={block:'another-block',page:1,bbox:[.4,.5,.7,.8]};
PaperLoom.ReaderUI.readerActor=()=>({sendQuery:async(name,point)=>{
  assert.equal(name,'locate-point');assert.deepEqual(point,{page:1,x:.5,y:.6});return reverse;}});
await state.pdfClick({target:pages[0],clientX:500,clientY:600,button:0});
assert.equal(state.lastLocated.block,'another-block');assert.equal(navigated.length,1);
assert.equal(state.pdfHighlight,null);assert.equal(state.pdfDoc,doc);
""")


def test_known_cross_column_regions_survive_resolver_failure_and_draw_separately():
    run_js(r"""
const {state,pages,navigated}=fixture();pages[2].style.opacity='.8';
PaperLoom.SourceRegions.resolve=async()=>{throw Error('Native text extraction failed');};
const bbox=[.1,.2,.3,.4],right=[.6,.1,.9,.3];
await PaperLoom.Comparison.locate(state,{block:'cross-column',page:1,bbox,
  regions:[{page:1,bbox},{page:1,bbox:right},{page:2,bbox:[.1,.1,.3,.2]},
    {page:1,bbox:[0,0,NaN,.5]},{page:99,bbox}]});
assert.equal(errors.length,1);assert.equal(state.lastLocated.regions.length,3);
assert.deepEqual(navigated[0].position.rects,[[100,600,300,800],[600,700,900,900]]);
assert.equal(state.pdfHighlights.length,2);
assert.equal(state.pdfHighlights[0].children.filter(node=>node.tag==='rect').length,2);
assert.equal(state.pdfHighlights[1].children.filter(node=>node.tag==='rect').length,1);
assert.equal(pages[0].style.opacity,'');assert.equal(pages[2].style.opacity,'.3');
PaperLoom.Comparison.clear(state);
assert.equal(state.pdfHighlight,null);assert.equal(pages[2].style.opacity,'.8');
assert.equal(state.lastLocated.block,'cross-column');
""")


def test_invalid_bbox_and_failed_viewport_still_allow_page_navigation():
    run_js(r"""
const {state,navigated,viewer}=fixture();
assert.equal(PaperLoom.PDFRegions.box([.1,,.3,.4]),null);
await PaperLoom.Comparison.locate(state,{block:'invalid',page:2,bbox:[.3,.1,.1,.2]});
assert.deepEqual(navigated[0],{pageIndex:1});assert.deepEqual(state.lastLocated.regions,[]);
viewer.getPageView=()=>{throw Error('Page not rendered yet');};
await PaperLoom.Comparison.locate(state,{block:'valid',page:1,bbox:[.1,.1,.2,.2]});
assert.deepEqual(navigated[1],{pageIndex:0});assert.equal(state.lastLocated.block,'valid');
assert.equal(state.lastLocated.regions.length,1);
await assert.rejects(PaperLoom.Comparison.locate(state,{page:9}),/页码/);
""")


def test_newer_request_and_frame_replacement_cancel_old_resolutions():
    run_js(r"""
const {state,navigated}=fixture(),old=deferred(),replaced=deferred();
PaperLoom.SourceRegions.resolve=async(_state,data)=>{
  if(data.block==='old')await old.promise;
  if(data.block==='replaced')await replaced.promise;
  return[{page:data.page,bbox:data.bbox}];};
const first=PaperLoom.Comparison.locate(state,{block:'old',page:1,bbox:[.1,.1,.2,.2]});await flush();
await PaperLoom.Comparison.locate(state,{block:'new',page:2,bbox:[.2,.2,.3,.3]});
old.resolve();assert.equal(await first,false);
assert.equal(navigated.length,1);assert.equal(state.lastLocated.block,'new');
const third=PaperLoom.Comparison.locate(state,{block:'replaced',page:1,bbox:[.1,.1,.2,.2]});await flush();
state.frame={browsingContext:{currentWindowGlobal:{}}};replaced.resolve();
assert.equal(await third,false);assert.equal(navigated.length,1);assert.equal(state.lastLocated.block,'new');
""")


def test_native_navigation_in_flight_finishes_before_latest_navigation():
    run_js(r"""
const {state}=fixture(),first=deferred(),order=[];
state.reader.navigate=async value=>{const index=value.position.pageIndex;order.push(`start${index}`);if(!index)await first.promise;order.push(`end${index}`);};
const older=PaperLoom.Comparison.locate(state,{block:'old',page:1,bbox:[.1,.1,.2,.2]});await flush();
assert.deepEqual(order,['start0']);
const latest=PaperLoom.Comparison.locate(state,{block:'latest',page:2,bbox:[.2,.2,.3,.3]});await flush();
assert.deepEqual(order,['start0']);first.resolve();
assert.equal(await older,false);await latest;
assert.deepEqual(order,['start0','end0','start1','end1']);assert.equal(state.lastLocated.block,'latest');
""")


def test_pending_reverse_click_cannot_overwrite_newer_ocr_selection():
    run_js(r"""
const {state,pages,navigated}=fixture(),pending=deferred();
PaperLoom.Comparison.bind(state);
PaperLoom.ReaderUI.readerActor=()=>({sendQuery:async()=>pending.promise});
const reverse=state.pdfClick({target:pages[0],clientX:150,clientY:150,button:0});await flush();
await PaperLoom.Comparison.locate(state,{block:'newer-ocr-selection',page:2,bbox:[.1,.1,.2,.2]});
pending.resolve({block:'old-pdf-click',page:1,bbox:[.1,.1,.2,.2]});await reverse;
assert.equal(state.lastLocated.block,'newer-ocr-selection');assert.equal(navigated.length,1);
""")


def test_native_document_replacement_and_unbind_cancel_pending_work():
    run_js(r"""
const {state,view,handlers,navigated}=fixture(),pending=deferred();
PaperLoom.Comparison.bind(state);assert.equal(handlers.size,1);
PaperLoom.SourceRegions.resolve=async(_state,data)=>{await pending.promise;return[{page:data.page,bbox:data.bbox}];};
const old=PaperLoom.Comparison.locate(state,{block:'old-doc',page:1,bbox:[.1,.1,.2,.2]});await flush();
const replacement=fixture();view._iframeWindow=replacement.view._iframeWindow;
PaperLoom.Comparison.bind(state);assert.equal(handlers.size,0);assert.equal(replacement.handlers.size,1);
pending.resolve();assert.equal(await old,false);assert.equal(navigated.length,0);
const block=deferred();PaperLoom.SourceRegions.resolve=async(_state,data)=>{await block.promise;return[{page:data.page,bbox:data.bbox}];};
const closing=PaperLoom.Comparison.locate(state,{block:'closing',page:1,bbox:[.1,.1,.2,.2]});await flush();
PaperLoom.Comparison.unbind(state);block.resolve();assert.equal(await closing,false);
assert.equal(replacement.handlers.size,0);assert.equal(state.pdfDoc,null);
""")


def test_cross_realm_native_objects_and_failed_initialization_recover():
    run_js(r"""
const {state,app,view,navigated}=fixture(),unwrapped=[];
const wrap=value=>({value});
globalThis.Components={utils:{waiveXrays:object=>{unwrapped.push(object);return object?.value??object;}}};
state.reader._internalReader=wrap({_primaryView:wrap(view)});
view._iframeWindow.PDFViewerApplication=wrap(app);
const pdf=app.pdfDocument;app.pdfDocument=null;
await assert.rejects(PaperLoom.Comparison.locate(state,{page:1,bbox:[.1,.1,.2,.2]}),/尚未加载/);
app.pdfDocument=wrap(pdf);app.pdfViewer=wrap(app.pdfViewer);
await PaperLoom.Comparison.locate(state,{block:'recovered',page:1,bbox:[.1,.1,.2,.2]});
assert.equal(navigated.length,1);assert.equal(state.lastLocated.block,'recovered');
assert.ok(unwrapped.includes(view._iframeWindow.PDFViewerApplication));
""")


def test_highlight_and_actor_failures_do_not_erase_recovered_navigation():
    run_js(r"""
const {state,pages,navigated}=fixture();
const data={block:'source-cache',page:1,bbox:[.1,.1,.2,.2]};
PaperLoom.PDFHighlight.render=()=>{throw Error('Drawing unavailable');};
await PaperLoom.Comparison.locate(state,data);
assert.equal(navigated.length,1);assert.equal(state.lastLocated.block,data.block);
PaperLoom.ReaderUI.readerActor=()=>{throw Error('Actor temporarily unavailable');};
PaperLoom.SourceRegions.find=async()=>({...data,block:'fallback-cache'});
await state.pdfClick({target:pages[0],clientX:150,clientY:150,button:0});
assert.equal(state.lastLocated.block,'fallback-cache');assert.equal(navigated.length,1);
assert.ok(errors.some(error=>/Actor/.test(error.message)));
""")
