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
    process = subprocess.run([node,"-"],input=source+"\n(async()=>{\n"+script+"\nconsole.log('runtime-test-complete');})().catch(e=>{console.error(e);process.exitCode=1;});",
                             text=True,encoding="utf-8",capture_output=True,timeout=30)
    assert process.returncode == 0, process.stderr
    assert "runtime-test-complete" in process.stdout, "An awaited task never completed"


def test_completed_paper_translates_while_next_ocr_is_still_running():
    run_js(["tasks"], """
const tasks=PaperLoom.Tasks, a={id:1}, b={id:2};
let finishB;const blockedB=new Promise(resolve=>finishB=resolve);
let startedB=false, translated=false, submissions=0;
tasks.fingerprint=async source=>({hash:`pdf-${source.id}`});
tasks.ocrCore=async task=>{
  if(task.kind==='ocr') { submissions++;if(task.source===b){startedB=true;await blockedB;} }
  return {documentID:`doc-${task.source.id}`};
};
PaperLoom.Store.saved=async()=>null;PaperLoom.Store.get=()=>({});PaperLoom.Store.flush=async()=>{};
PaperLoom.Editing.stale=()=>false;PaperLoom.Service={ensure:async()=>{}};
PaperLoom.request=async(method,path)=>{
  if(path==='/api/settings')return {llm_base_url:'http://llm',llm_model:'test'};
  if(path.endsWith('/translations')){assert.ok(startedB);assert.equal(tasks.forSource(b).kind,'ocr');translated=true;return {id:'translated-a'};}
  return {};
};
PaperLoom.Artifacts={download:async(source,kind,hash,artifact)=>artifact};
const first=tasks.enqueue(a,'ocr'),second=tasks.enqueue(b,'ocr');
await first;assert.ok(startedB);
await tasks.enqueue(a,'translate');
assert.ok(translated);assert.equal(submissions,2);assert.equal(tasks.forSource(b).kind,'ocr');
finishB();await second;assert.equal(tasks.pending.size,0);assert.equal(tasks.running.length,0);
""")


def test_independent_lanes_serialize_each_kind_and_lock_only_the_same_pdf():
    run_js(["tasks"], """
const tasks=PaperLoom.Tasks,a={id:1},b={id:2},events=[];
const gates={};const counts={ocr:0,translate:0},max={ocr:0,translate:0};
const run=async task=>{
  const key=`${task.kind}${task.source.id}`;events.push(key);counts[task.kind]++;
  max[task.kind]=Math.max(max[task.kind],counts[task.kind]);
  await new Promise(resolve=>gates[key]=resolve);counts[task.kind]--;return {};
};tasks.ocrCore=run;tasks.translateCore=run;
const translateA=tasks.enqueue(a,'translate');
const ocrA=tasks.enqueue(a,'ocr',null,{force:true});
const ocrB=tasks.enqueue(b,'ocr');const translateB=tasks.enqueue(b,'translate');
assert.deepEqual(events,['translate1','ocr2']);
gates.ocr2();await ocrB;
assert.deepEqual(events,['translate1','ocr2']);
gates.translate1();await translateA;
assert.deepEqual(events,['translate1','ocr2','ocr1','translate2']);
gates.translate2();gates.ocr1();await Promise.all([ocrA,translateB]);
assert.deepEqual(max,{ocr:1,translate:1});assert.equal(tasks.pending.size,0);
""")


def test_translation_waits_for_its_own_ocr_but_not_an_unrelated_pdf():
    run_js(["tasks"], """
const tasks=PaperLoom.Tasks,a={id:1},b={id:2};let finishOCR,translatedA=false;
tasks.ocrCore=async()=>{await new Promise(resolve=>finishOCR=resolve);return {};};
tasks.translateCore=async task=>{if(task.source===a)translatedA=true;return {};};
const ocr=tasks.enqueue(a,'ocr');const waiting=tasks.enqueue(a,'translate');
await tasks.enqueue(b,'translate');assert.equal(translatedA,false);
finishOCR();await Promise.all([ocr,waiting]);assert.ok(translatedA);
assert.equal(tasks.pending.size,0);
""")


def test_translation_respects_a_queued_reocr_of_the_same_pdf():
    run_js(["tasks"], """
const tasks=PaperLoom.Tasks,a={id:1},b={id:2},events=[];const gates={};
tasks.ocrCore=async task=>{events.push(`ocr${task.source.id}`);await new Promise(resolve=>gates[task.source.id]=resolve);return {};};
tasks.translateCore=async task=>{events.push(`translate${task.source.id}`);return {};};
const ocrB=tasks.enqueue(b,'ocr'),ocrA=tasks.enqueue(a,'ocr',null,{force:true}),translation=tasks.enqueue(a,'translate');
assert.deepEqual(events,['ocr2']);
gates[2]();await ocrB;assert.deepEqual(events,['ocr2','ocr1']);
gates[1]();await Promise.all([ocrA,translation]);
assert.deepEqual(events,['ocr2','ocr1','translate1']);
""")


@pytest.mark.parametrize("cancel_kind", ["ocr", "translate"])
def test_cancel_one_lane_does_not_stop_shared_service_or_other_lane(cancel_kind):
    run_js(["tasks"], """
const tasks=PaperLoom.Tasks,a={id:1},b={id:2};const gates={};let cancelledJob;
const run=async task=>{
  if(task.kind==='ocr')task.jobID='ocr-b';
  await new Promise((resolve,reject)=>{gates[task.kind]=resolve;task.xhr={abort:()=>reject(new Error('Aborted'))};});
  tasks.check(task);return {};
};tasks.ocrCore=run;tasks.translateCore=run;
PaperLoom.Service={owned:true,stop:async()=>{throw new Error('Shared service must stay alive');}};
PaperLoom.request=async(method,path)=>{cancelledJob=path;};
const translating=tasks.enqueue(a,'translate').then(()=>true,()=>false);
const recognizing=tasks.enqueue(b,'ocr').then(()=>true,()=>false);
const kind=CANCEL_KIND,other=kind==='ocr'?'translate':'ocr';
await tasks.cancel(task=>task.kind===kind,'cleared');
assert.equal(tasks.running.length,1);assert.equal(tasks.active.kind,other);
assert.equal(await(kind==='ocr'?recognizing:translating),false);
if(kind==='ocr')assert.equal(cancelledJob,'/api/jobs/ocr-b/cancel');
gates[other]();assert.equal(await(kind==='ocr'?translating:recognizing),true);
assert.equal(tasks.pending.size,0);
""".replace("CANCEL_KIND", json.dumps(cancel_kind)))


def test_status_reports_both_active_lanes_and_shutdown_aborts_both():
    run_js(["tasks", "ui"], """
const tasks=PaperLoom.Tasks;
PaperLoom.ReaderUI.refresh=()=>{};
const node={};PaperLoom.windows.set({document:{getElementById:()=>node}},[]);
const run=async task=>new Promise((resolve,reject)=>{task.xhr={abort:()=>reject(new Error('Aborted'))};});
tasks.ocrCore=run;tasks.translateCore=run;
const ocr=tasks.enqueue({id:1,getField:()=> 'OCR paper'},'ocr').catch(()=>{});
const translation=tasks.enqueue({id:2,getField:()=> 'Translation paper'},'translate').catch(()=>{});
PaperLoom.UI.refreshTasks();assert.ok(node.textContent.includes('OCR'));assert.ok(node.textContent.includes('翻译'));
assert.ok(node.title.includes('OCR paper'));assert.ok(node.title.includes('Translation paper'));
PaperLoom.alive=false;tasks.shutdown();await Promise.all([ocr,translation]);
assert.equal(tasks.running.length,0);assert.equal(tasks.pending.size,0);
""")


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
