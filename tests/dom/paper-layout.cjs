const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../..');
const cases=new Map();
const test=(name,run)=>cases.set(name,run);
const html=(id,role,text='',page=1)=>`<section class="pl-block" data-block-id="${id}" data-role="${role}" data-block-type="${role.endsWith('_caption')?'caption':role}" data-page="${page}" data-bbox="[0.1,0.2,0.4,0.6]" data-regions='[{"page":${page},"bbox":[0.1,0.2,0.4,0.6]}]'><span class="pl-role">${role}</span><div class="pl-source">${text}</div><div class="pl-target">${text}</div></section>`;
const dom=body=>new JSDOM(`<body><nav></nav><div id="pl-status"></div><main>${body}</main></body>`,{runScripts:'outside-only'});
const ids=node=>Array.from(node.querySelectorAll('.pl-block'),b=>b.dataset.blockId);
const block=(doc,id)=>Array.from(doc.querySelectorAll('.pl-block')).find(b=>b.dataset.blockId===id);
let enhance,exportPage;

test('abstracts',()=>{
  const view=dom(html('h','heading','<h2>Summary</h2>')+[1,2,3,4].map(i=>html('a'+i,'abstract',(i===1?'<h2>Abstract</h2>':'')+`<p>Bullet ${i}</p>`)).join('')+html('intro','heading','<h2>Introduction</h2>')+html('p','paragraph','Main body'));
  const doc=view.window.document;
  const original=Array.from(doc.querySelectorAll('.pl-block'));const attributes=original.map(b=>b.dataset.regions);
  enhance(doc);enhance(doc);
  assert.equal(doc.querySelectorAll('.paper-abstract').length,1);
  assert.deepEqual(ids(doc.querySelector('.paper-abstract')),['h','a1','a2','a3','a4']);
  assert.equal(doc.querySelectorAll('.paper-abstract h2:not([hidden])').length,2); // source and translation Summary
  assert.deepEqual(Array.from(doc.querySelectorAll('.paper-outline a'),a=>a.textContent),['Summary','Introduction']);
  assert.equal(block(doc,'p').closest('.paper-abstract'),null);
  original.forEach((b,i)=>{assert.equal(block(doc,b.dataset.blockId),b);assert.equal(b.dataset.regions,attributes[i]);});
  view.window.close();
});

test('captions',()=>{
  const view=dom(html('image','figure','<img src="image.png">')+html('c1','figure_caption','Fig. 2 | Main caption')+html('c2','figure_caption','lines depict the median value')+html('p','paragraph','Body text')+html('tc1','table_caption','Table 1. Values')+html('tc2','table_caption','Units and explanations')+html('table','table','<table><tr><td>1</td></tr></table>'));
  const doc=view.window.document;enhance(doc);
  assert.deepEqual(ids(doc.querySelector('.paper-figure .paper-media-caption')),['c1','c2']);
  assert.deepEqual(ids(doc.querySelector('.paper-table .paper-media-caption')),['tc1','tc2']);
  assert.equal(block(doc,'p').closest('.paper-media'),null);
  assert.equal(doc.querySelectorAll('.paper-caption-group').length,0);
  assert.equal(block(doc,'c2').querySelectorAll('.pl-source,.pl-target').length,2);
  view.window.close();
});

test('boundaries',()=>{
  const view=dom(html('image','figure','<img>')+html('c1','figure_caption','Fig. 2. Caption')+html('c2','figure_caption','Fig. 3. A different numbered figure')+html('body','paragraph','Body barrier')+html('c3','figure_caption','Continuation without an image')+html('tc','table_caption','Table 1. Different type')+html('c4','figure_caption','Next page',2));
  const doc=view.window.document;enhance(doc);
  assert.deepEqual(ids(doc.querySelector('.paper-figure .paper-media-caption')),['c1']);
  for(const id of ['c2','c3','tc','c4'])assert.equal(block(doc,id).closest('.paper-media'),null);
  assert.equal(doc.querySelectorAll('.paper-caption-group').length,4);
  view.window.close();
});

test('orphan-captions',()=>{
  const view=dom(html('a','figure_caption','Figure 1. Caption')+html('b','figure_caption','continuation')+html('h','heading','<h2>Results</h2>')+html('p','paragraph','Body'));
  const doc=view.window.document;enhance(doc);
  assert.deepEqual(ids(doc.querySelector('.paper-caption-group')),['a','b']);
  assert.equal(block(doc,'p').closest('.paper-caption-group'),null);view.window.close();
});

test('footnotes',()=>{
  const view=dom(html('fig','figure','<img>')+html('note1','footnote','Extended Data Fig. 3 | Caption')+html('note2','footnote','Abbreviations'));
  const doc=view.window.document;enhance(doc);
  assert.equal(doc.querySelectorAll('.paper-note').length,2);
  for(const id of ['note1','note2']){block(doc,id).dataset.role='figure_caption';block(doc,id).dataset.blockType='caption';}
  delete doc.body.dataset.paperLayoutVersion;enhance(doc);
  assert.equal(doc.querySelectorAll('.paper-note').length,0);
  assert.deepEqual(ids(doc.querySelector('.paper-figure .paper-media-caption')),['note1','note2']);
  view.window.close();
});

test('mapping-and-export',async()=>{
  const view=dom(html('image','figure','<img src="assets/media/a.png">')+html('a','figure_caption','Fig. 2. Caption')+html('b','figure_caption','Continuation')+html('abstract','abstract','<h2>Abstract</h2><p>Summary</p>'));
  const doc=view.window.document;doc.body.dataset.zoteroBridge='true';
  view.window.eval(fs.readFileSync(path.join(root,'app/static/offline_reader.js'),'utf8'));
  const actions=[];
  doc.addEventListener('PaperLoomAction',event=>{const data=JSON.parse(event.detail);actions.push(data);doc.dispatchEvent(new view.window.CustomEvent('PaperLoomActionResult',{detail:JSON.stringify({id:data.id,value:true})}));});
  const continuation=block(doc,'b'),regions=continuation.dataset.regions;
  enhance(doc);continuation.querySelector('[data-action="locate"]').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(actions.length,1);assert.equal(actions[0].block,'b');assert.equal(actions[0].revealPDF,true);
  assert.equal(JSON.stringify(actions[0].regions),regions);assert.equal(block(doc,'b'),continuation);
  assert.equal(doc.querySelectorAll('[data-block-id="b"]').length,1);
  const exported=exportPage(doc,'paper-assets');
  assert.ok(!exported.includes('data-action="locate"'));assert.ok(exported.includes('paper-assets/media/a.png'));
  const saved=new JSDOM(exported);enhance(saved.window.document);
  assert.equal(saved.window.document.querySelectorAll('.paper-media').length,1);
  assert.deepEqual(ids(saved.window.document.querySelector('.paper-media-caption')),['a','b']);
  assert.equal(saved.window.document.querySelectorAll('.paper-abstract').length,1);
  assert.equal(block(saved.window.document,'b').dataset.regions,regions);
  saved.window.close();view.window.close();
});

test('editor-types',async()=>{
  const view=dom(html('p','paragraph','First abstract paragraph')+html('note','footnote','Extended Data Fig. 3. Caption')+html('abstract','abstract','<h2>Abstract</h2><p>Last abstract paragraph</p>'));
  const doc=view.window.document;
  const payload={blocks:[['p','paragraph','First abstract paragraph'],['note','footnote','Extended Data Fig. 3. Caption'],['abstract','abstract','Last abstract paragraph']].map(([id,type,text])=>({id,type,content:{text}})),changes:{},insertions:[]};
  const data=doc.createElement('script');data.id='pl-editor-data';data.type='application/json';data.textContent=JSON.stringify(payload);doc.body.append(data);
  view.window.structuredClone=structuredClone;
  view.window.eval(fs.readFileSync(path.join(root,'app/static/offline_reader.js'),'utf8'));
  view.window.eval(fs.readFileSync(path.join(root,'app/static/ocr_editor.js'),'utf8'));
  enhance(doc);
  assert.equal(doc.querySelectorAll('[data-action="editor-type"]').length,3);
  for(const [id,role] of [['p','abstract'],['note','figure_caption']]){
    block(doc,id).querySelector('[data-action="editor-type"]').click();
    const select=block(doc,id).querySelector('select[aria-label="内容类型"]');
    assert.equal(doc.activeElement,select);select.value=role;
    select.dispatchEvent(new view.window.Event('change',{bubbles:true}));
  }
  assert.equal(view.window.PaperLoomEditor.draft.changes.p.role,'abstract');
  assert.equal(view.window.PaperLoomEditor.draft.changes.note.role,'figure_caption');
  assert.ok(!('markdown' in view.window.PaperLoomEditor.draft.changes.p));
  assert.ok(!('markdown' in view.window.PaperLoomEditor.draft.changes.note));
  let saved;view.window.PaperLoomOffline.action=async data=>{saved=data;};
  doc.querySelector('[data-action="editor-save"]').click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(saved.type,'save-ocr-edits');assert.equal(saved.changes.note.role,'figure_caption');
  doc.querySelector('[data-action="editor-cancel"]').click();assert.equal(doc.body.classList.contains('pl-editing'),false);
  assert.equal(Object.keys(view.window.PaperLoomEditor.draft.changes).length,0);
  view.window.close();
});

(async()=>{
  ({enhance,exportPage}=await import(pathToFileURL(path.join(root,'zotero/content/paper-layout.mjs'))));
  const name=process.argv[2];
  if(name==='--file'){
    const view=new JSDOM(fs.readFileSync(process.argv[3],'utf8'));enhance(view.window.document);
    const doc=view.window.document;
    for(const [wrapper,expected] of JSON.parse(process.argv[4]||'[]'))assert.deepEqual(ids(doc.querySelector(wrapper)),expected);
    console.log(JSON.stringify({blocks:doc.querySelectorAll('.pl-block').length,abstracts:doc.querySelectorAll('.paper-abstract').length,figures:doc.querySelectorAll('.paper-figure').length,captions:doc.querySelectorAll('.paper-media-caption:not(:empty)').length}));
    view.window.close();return;
  }
  assert.ok(!name||cases.has(name),'Unknown DOM test');
  for(const [label,run] of cases)if(!name||name===label){await run();console.log('PASS '+label);}
})().catch(error=>{console.error(error);process.exitCode=1;});
