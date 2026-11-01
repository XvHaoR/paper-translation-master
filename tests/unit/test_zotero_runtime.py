"""Exercise JS queue/lifetime/preferences without starting models or touching a library."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_js(modules, script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime required")
    setup = """
const assert=require('node:assert/strict');
const prefs={};
const PaperLoom={alive:true,windows:new Map(),pref:(key,fallback)=>prefs[key]??fallback,setPref:(key,value)=>prefs[key]=value,
  serviceURL:()=> 'http://127.0.0.1:8765',language:()=> 'zh',ReaderUI:{states:new Map(),applyOptions:async()=>{}},
  Editing:{jobs:new Map()},Store:{key:s=>s.id,set:()=>{}},UI:{refreshTasks:()=>{}}};
const Zotero={Promise:{delay:async()=>{}},getMainWindow:()=>null};
"""
    source = setup + "\n".join((ROOT / "zotero/modules" / (name+".js")).read_text(encoding="utf-8") for name in modules)
    process = subprocess.run([node,"-"],input=source+"\n(async()=>{\n"+script+"\n})().catch(e=>{console.error(e);process.exitCode=1;});",
                             text=True,encoding="utf-8",capture_output=True)
    assert process.returncode == 0, process.stderr


def test_owned_service_does_not_restart_on_probe_timeout_and_external_retries():
    run_js(["service"], """
const service=PaperLoom.Service;
service.state={phase:'ready'};service.owned=true;service.process={pid:123};service.instance='lease';service.readyURL=PaperLoom.serviceURL();
service.online=async()=>{throw new Error('No per-document probe allowed');};
service.start=async()=>{throw new Error('No model restart allowed');};
await service.ensure();await service.ensure();
service.owned=false;service.process=null;service.instance=null;service.lastReadyAt=0;
let probes=0;service.online=async()=>++probes>1;
await service.ensure();assert.equal(probes,2);await service.ensure();assert.equal(probes,2);
""")


def test_portable_plugin_discovers_installer_and_keeps_its_environment():
    run_js(["runtime"], """
PaperLoom.config={projectRoot:'',runtimeDirectory:'PaperTranslationMaster',defaultBackend:'local'};Zotero.isWin=true;
globalThis.Services={env:{get:()=>'/local'}};
globalThis.PathUtils={join:(...parts)=>parts.join('/'),isAbsolute:path=>path.startsWith('/')};
const data={schema:1,projectRoot:'/engine',pythonPath:'/python/python.exe',backend:'local',environment:{PAPERBRIDGE_WORKSPACE:'/data'}};
globalThis.IOUtils={exists:async()=>true,readJSON:async()=>data};
const discovered=await PaperLoom.Runtime.resolve();
assert.equal(discovered.projectRoot,'/engine');assert.equal(discovered.backend,'local');assert.ok(discovered.descriptor.endsWith('/runtime.json'));
PaperLoom.setPref('projectRoot','/engine');
const saved=await PaperLoom.Runtime.resolve();assert.equal(saved.pythonPath,data.pythonPath);assert.equal(saved.environment.PAPERBRIDGE_WORKSPACE,'/data');
PaperLoom.setPref('projectRoot','/manual');const manual=await PaperLoom.Runtime.resolve();
assert.equal(manual.projectRoot,'/manual');assert.equal(manual.descriptor,undefined);
data.schema=9;await assert.rejects(PaperLoom.Runtime.installed(),/安装信息/);
""")


def test_batch_deduplicates_and_continues_after_one_failure():
    run_js(["tasks"], """
const sources=[1,2,3].map(id=>({id,getField:()=>`paper ${id}`}));
const task=PaperLoom.Tasks;task.sourceFor=async item=>sources[item-1];
let running=0,max=0;const seen=[];
task.ocrCore=async task=>{running++;max=Math.max(max,running);seen.push(task.source.id);await Promise.resolve();running--;if(task.source.id===2)throw new Error('Broken PDF');return {attachment:{}};};
const window={ZoteroPane:{getSelectedItems:()=>[1,1,2,3]}};
await assert.rejects(task.selected(window,'ocr'),/Broken PDF/);
assert.deepEqual(seen,[1,2,3]);assert.equal(max,1);
assert.equal(task.lastBatch.done,3);assert.equal(task.lastBatch.success,2);assert.equal(task.lastBatch.failed,1);
assert.equal(task.lastBatch.finished,true);assert.equal(task.pending.size,0);
""")


def test_rapid_edition_uses_its_own_cpu_environment_even_with_old_gpu_preferences():
    run_js(["runtime"], """
PaperLoom.config={ocrMode:'rapidocr',runtimeDirectory:'PaperTranslationMasterRapidOCR'};
PaperLoom.setPref('projectRoot','/old-gpu');PaperLoom.setPref('backend','wsl');
PaperLoom.Runtime.installed=async()=>({projectRoot:'/rapid',pythonPath:'/rapid/python',backend:'local',descriptor:'/rapid/runtime.json'});
const result=await PaperLoom.Runtime.resolve();assert.equal(result.projectRoot,'/rapid');assert.equal(result.backend,'local');
""")


def test_single_paper_batch_never_opens_a_reader():
    run_js(["tasks"], """
const source={id:1,getField:()=> 'One paper'};
PaperLoom.Tasks.sourceFor=async item=>item;PaperLoom.Tasks.ocrCore=async()=>({attachment:{}});
PaperLoom.ReaderUI.open=()=>{throw new Error('No automatic reader tabs');};
const batch=await PaperLoom.Tasks.runItems({},'ocr',[source]);
assert.equal(batch.total,1);assert.equal(batch.success,1);assert.equal(batch.finished,true);
""")


def test_picker_lists_papers_without_processing_and_disables_missing_files():
    run_js(["tasks","batch-picker"], """
const IOUtils={exists:async path=>path!=='missing'};globalThis.IOUtils=IOUtils;
const paper=(id,title)=>({id,libraryID:1,deleted:false,isRegularItem:()=>true,isAttachment:()=>false,getField:key=>({title,firstCreator:'Author',date:'2025'})[key]||''});
const first=paper(1,'First'),missing=paper(2,'Missing'),noPDF=paper(3,'No PDF');
const pdf=(id,parent,path)=>({id,parentItemID:parent,libraryID:1,attachmentContentType:'application/pdf',isRegularItem:()=>false,isAttachment:()=>true,getFilePathAsync:async()=>path,getField:()=> 'Full text'});
const one=pdf(11,1,'local'),two=pdf(12,2,'missing');
Zotero.Items={get:id=>({1:first,2:missing})[id]};Zotero.Libraries={get:()=>({editable:true,filesEditable:true})};
PaperLoom.Tasks.sourceFor=async item=>{if(item===first||item===one)return one;if(item===missing)return two;throw new Error('No PDF');};
PaperLoom.Tasks.runItems=()=>{throw new Error('Opening selector cannot process anything');};
const owner={ZoteroPane:{getSelectedItems:()=>[first,one],itemsView:{getSortedItems:()=>[first,one,missing,noPDF]}}};
const rows=await PaperLoom.BatchPicker.rows(owner);
assert.equal(rows.length,3);assert.equal(rows[0].available,true);assert.equal(rows[0].preselected,true);
assert.equal(rows[1].available,false);assert.match(rows[1].reason,/缺失/);assert.equal(rows[2].reason,'无 PDF');
""")


def test_picker_submit_uses_checkmarks_instead_of_main_window_selection():
    run_js(["batch-picker"], """
const a={id:1},b={id:2},owner={ZoteroPane:{getSelectedItems:()=>[b]}};
PaperLoom.Tasks={runItems:async(w,action,items)=>{assert.equal(w,owner);assert.equal(action,'translate');assert.deepEqual(items,[a]);return {success:1};}};
const result=await PaperLoom.BatchPicker.submit(owner,'translate',[{id:1,source:a,available:true},{id:2,source:b,available:true}],[1]);
assert.equal(result.success,1);
await assert.rejects(PaperLoom.BatchPicker.submit(owner,'ocr',[],[]),/勾选/);
""")


def test_picker_registration_survives_initial_blank_document_unload():
    run_js(["batch-picker"], """
let unload,opened=0,focused=0;
const dialog={closed:false,addEventListener:(name,callback)=>{if(name==='unload')unload=callback;},focus:()=>focused++,close(){this.closed=true;}};
const owner={openDialog:()=>{opened++;return dialog;}};
PaperLoom.BatchPicker.open(owner,'ocr');
unload({target:{documentURI:'about:blank'}});
assert.equal(PaperLoom.BatchPicker.windows.get(owner),dialog);
PaperLoom.BatchPicker.open(owner,'translate');assert.equal(opened,1);assert.equal(focused,1);
unload({target:{documentURI:'chrome://paperloom/content/batch.xhtml'}});
assert.equal(PaperLoom.BatchPicker.windows.has(owner),false);
""")


def test_appearance_preferences_are_independent_and_reject_css_injection():
    run_js(["appearance"], """
let applied=0;PaperLoom.ReaderUI.states.set('a',{});PaperLoom.ReaderUI.applyOptions=async()=>applied++;
await PaperLoom.Appearance.save({ocrFontFamily:'Times New Roman',translationFontFamily:'Microsoft YaHei',ocrTextColor:'#203040',translationTextColor:'#502010'});
assert.equal(PaperLoom.Appearance.read().translationTextColor,'#502010');assert.equal(prefs.ocrFontFamily,'Times New Roman');assert.equal(applied,1);
assert.throws(()=>PaperLoom.Appearance.validate({ocrFontFamily:'Arial; color:red'}));
assert.throws(()=>PaperLoom.Appearance.validate({ocrTextColor:'url(http://example.com)'}));
await PaperLoom.Appearance.save(PaperLoom.Appearance.defaults);assert.equal(prefs.translationTextColor,'');
""")


def test_finished_batch_does_not_label_an_unrelated_task():
    run_js(["ui"], """
const node={};PaperLoom.windows.set({document:{getElementById:()=>node}},[]);
PaperLoom.ReaderUI.refresh=()=>{};
PaperLoom.Tasks={active:{kind:'translate',source:{getField:()=> 'Next paper'},message:'Working'},queue:[],batches:new Map(),lastBatch:{kind:'ocr',total:2,done:2,success:2,finished:true}};
PaperLoom.UI.refreshTasks();assert.ok(node.textContent.startsWith('翻译'));assert.ok(!node.textContent.includes('批量'));
PaperLoom.Tasks.active=null;PaperLoom.UI.refreshTasks();assert.ok(node.textContent.includes('2/2'));
""")
