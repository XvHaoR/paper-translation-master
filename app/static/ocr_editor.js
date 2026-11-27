/* A correction overlay, not a second Markdown parser. Rendering is done by the
   local worker on save; original block IDs/coordinates never enter edit fields. */
(() => {
  "use strict";
  const data = document.getElementById("pl-editor-data");
  if (!data) return;
  const payload = JSON.parse(data.textContent);
  const initial = { changes: payload.changes || {}, insertions: payload.insertions || [], order:payload.order || [], moved:payload.moved || [] };
  let draft = structuredClone(initial), history = [], editing = false, busy = false;
  const labels = {document_title:"论文标题",heading:"章节标题",paragraph:"正文",abstract:"摘要",
    figure_caption:"图注",table_caption:"表注",list:"列表",equation_block:"公式",equation_inline:"行内公式",
    table:"表格",code:"代码",reference:"参考文献",footnote:"脚注",authors:"作者",figure:"图片"};
  const sections = new Map(Array.from(document.querySelectorAll(".pl-block")).map(node => [node.dataset.blockId, node]));
  const blocks = new Map(payload.blocks.map(block => [block.id, block]));
  const main = document.querySelector("main"), positions = new Map();
  const stage = document.createElement("div");
  stage.className = "pl-edit-stage";
  stage.hidden = true;
  main.before(stage);
  let moving = "";
  const status = document.getElementById("pl-status");
  function element(tag, text, parent) {
    const node = document.createElement(tag); if (text != null) node.textContent = text;
    parent?.append(node); return node;
  }
  function control(parent, text, name, callback) {
    const node = element("button", text, parent); node.type = "button"; node.dataset.action = name;
    node.addEventListener("click", async event => {
      event.stopPropagation(); if (busy) return;
      try { await callback(); } catch (error) { status.textContent = error.message; }
    }); return node;
  }
  const bar = element("div", null, document.body); bar.className = "pl-editor-bar"; bar.hidden = true;
  element("strong", "编辑 OCR", bar);
  element("span", "拖动「移动」手柄可调整顺序。跨页移动也可以先点手柄，再点目标块的「移到此处」。", bar);
  const stateLabel = element("span", "修改保存在新版本中，PDF 原件不变", bar);
  control(bar, "保存修改", "editor-save", async () => {
    collect();
    busy = true; update();
    try {
      await window.PaperLoomOffline.action({type:"save-ocr-edits",...draft});
      stateLabel.textContent = "已保存";
    } finally { busy = false; update(); }
  });
  control(bar, "撤销", "editor-undo", () => { if (history.length) { draft = history.pop(); redraw(); } });
  control(bar, "恢复原始 OCR", "editor-reset", () => { remember(); draft={changes:{},insertions:[],order:[],moved:[]}; redraw(); });
  control(bar, "取消编辑", "editor-cancel", () => { draft=structuredClone(initial);history=[];close(); });
  function remember() { history.push(structuredClone(draft)); if (history.length > 100) history.shift(); }
  function baseRole(block) {
    if(block.type==='caption')return (block.layout_label||'').includes('table')||/^\s*(?:table\b|表\s*\d)/i.test(block.content.text||'')?'table_caption':'figure_caption';
    return block.type;
  }
  function currentRole(block) { return draft.changes[block.id]?.role || baseRole(block); }
  function markdown(block) {
    if(payload.markdown?.[block.id]!=null)return payload.markdown[block.id];
    if (block.content.latex) return block.type === "equation_inline" ? `$${block.content.latex}$` : `$$\n${block.content.latex}\n$$`;
    if (block.type === "heading") return `${"#".repeat(block.heading_level || 2)} ${block.content.text || ""}`;
    if (block.type === "document_title") return `# ${block.content.text || ""}`;
    if (block.type === "abstract") return `## Abstract\n\n${block.content.text || ""}`;
    if (block.type === "list") return (block.content.items || (block.content.text || "").split("\n")).map(t=>`- ${t}`).join("\n");
    if (block.type === "table") return block.content.html || block.content.text || "";
    return block.content.text || "";
  }
  function collect() {
    for (const [id, node] of sections) {
      const fields=node.querySelector('.pl-edit-fields'); if(!fields)continue;
      const block=blocks.get(id); if(!block)continue;
      const text=fields.querySelector('textarea')?.value;
      const role=fields.querySelector('select')?.value;
      const patch={...(draft.changes[id]||{})};
      if(text!=null && text!==markdown(block))patch.markdown=text;else delete patch.markdown;
      if(role!==baseRole(block))patch.role=role;else delete patch.role;
      if(Object.keys(patch).length)draft.changes[id]=patch;else delete draft.changes[id];
    }
    for(const row of document.querySelectorAll('.pl-inserted-heading')){
      const entry=draft.insertions.find(value=>value.id===row.dataset.id);
      if(entry)entry.markdown=row.querySelector('input').value;
    }
  }
  function update() {
    for(const node of bar.querySelectorAll('button'))node.disabled=busy||(node.dataset.action==='editor-undo'&&!history.length);
    if(busy)stateLabel.textContent="正在保存…";
    else stateLabel.textContent=moving ? "请选择目标位置" : `${Object.keys(draft.changes).length} 个内容块修改，${draft.moved.length} 个移动，${draft.insertions.length} 个新增标题`;
  }
  function move(from, to, after) {
    if (!from || from === to || !blocks.has(from) || !blocks.has(to)) return;
    collect(); remember();
    const order = (draft.order.length ? draft.order : [...blocks.keys()]).filter(id => id !== from);
    order.splice(order.indexOf(to) + Number(after), 0, from);
    draft.order = order;
    if (!draft.moved.includes(from)) draft.moved.push(from);
    moving = ""; redraw();
    sections.get(from)?.scrollIntoView({block:"nearest"});
  }
  function redraw() {
    document.querySelectorAll('.pl-edit-fields,.pl-inserted-heading,.pl-deletion-note').forEach(node=>node.remove());
    if (editing) for (const id of draft.order.length ? draft.order : blocks.keys()) {
      const node = sections.get(id);
      if (node) stage.append(node);
    }
    for(const [id,node] of sections){
      if(!blocks.has(id)){node.hidden=editing;continue;}
      node.classList.toggle('pl-deleted',!!draft.changes[id]?.deleted);
      if(!editing)continue;
      const block=blocks.get(id); if(!block)continue;
      const patch=draft.changes[id]||{};
      if(patch.deleted){
        const note=element('div','此内容已删除，保存后隐藏。',node);note.className='pl-deletion-note';
        control(note,'恢复','editor-restore',()=>{collect();remember();delete draft.changes[id].deleted;redraw();});
        continue;
      }
      const fields=element('div',null,node);fields.className='pl-edit-fields';
      const handle=control(fields,'移动','editor-move',()=>{moving=moving===id?"":id;redraw();});
      handle.draggable=true;
      handle.setAttribute("aria-pressed", String(moving===id));
      handle.addEventListener("dragstart",event=>{moving=id;event.dataTransfer.setData("text/plain",id);event.dataTransfer.effectAllowed="move";});
      if(moving && moving!==id){
        control(fields,'移到此处之前','move-before',()=>move(moving,id,false));
        control(fields,'移到此处之后','move-after',()=>move(moving,id,true));
      }
      const label=element('label','内容类型 ',fields),select=element('select',null,label);
      select.setAttribute('aria-label','内容类型');
      for(const [value,text] of Object.entries(labels)){
        if((block.type==='figure')!==(value==='figure'))continue;
        const option=element('option',text,select);option.value=value;
      }
      select.value=currentRole(block,node);
      if(block.type!=='figure'){
        const area=element('textarea',null,fields);area.value=patch.markdown??markdown(block);
        area.setAttribute('aria-label','Markdown 内容');area.spellcheck=false;
        area.rows=Math.min(16,Math.max(3,area.value.split('\n').length+Math.ceil(area.value.length/120)));
      }
      let beforeChange=null;
      fields.addEventListener('focusin',()=>{beforeChange=structuredClone(draft);});
      fields.addEventListener('change',()=>{if(beforeChange){history.push(beforeChange);beforeChange=null;}collect();update();});
      control(fields,'在此之前插入标题','editor-insert',()=>{
        collect();remember();
        const id='manual-'+Array.from(crypto.getRandomValues(new Uint8Array(12)),n=>n.toString(16).padStart(2,'0')).join('');
        draft.insertions.push({id,before:block.id,markdown:'## Main'});redraw();
        Array.from(document.querySelectorAll('.pl-inserted-heading')).find(row=>row.dataset.id===id)?.querySelector('input').focus();
      });
      control(fields,block.type==='figure'?'删除图片':'删除内容','editor-delete',()=>{collect();remember();(draft.changes[id]||={}).deleted=true;redraw();});
    }
    for(const entry of draft.insertions){
      const anchor=sections.get(entry.before);if(!anchor)continue;
      const row=element('div');row.className='pl-inserted-heading';row.dataset.id=entry.id;
      element('label','新增标题（无 PDF 原文区域） ',row);
      const input=element('input',null,row);input.value=entry.markdown;input.setAttribute('aria-label','新增 Markdown 标题');
      input.addEventListener('change',()=>{remember();collect();update();});
      control(row,'移除标题','editor-remove-heading',()=>{collect();remember();draft.insertions=draft.insertions.filter(item=>item.id!==entry.id);redraw();});
      anchor.before(row);
    }
    update();
  }
  stage.addEventListener("dragover",event=>{
    if (!moving || !event.target.closest(".pl-block")) return;
    event.preventDefault();event.dataTransfer.dropEffect="move";
  });
  stage.addEventListener("drop",event=>{
    const node=event.target.closest(".pl-block");
    if (!moving || !node) return;
    event.preventDefault();
    const rect=node.getBoundingClientRect();
    move(moving,node.dataset.blockId,event.clientY>rect.top+rect.height/2);
  });
  stage.addEventListener("dragend",()=>{moving="";update();});
  function close(){
    editing=false;moving="";bar.hidden=true;stage.hidden=true;main.hidden=false;
    for(const [id,marker] of positions) marker.replaceWith(sections.get(id));
    positions.clear();document.body.classList.remove('pl-editing');redraw();
  }
  function open(section, focusType=false) {
      if(editing)collect();
      else {
        for(const [id,node] of sections) {
          if(!blocks.has(id)) continue;
          const marker=document.createComment("editor position");node.before(marker);positions.set(id,marker);
        }
        main.hidden=true;stage.hidden=false;
      }
      editing=true;bar.hidden=false;document.body.classList.add('pl-editing');
      const first=section||document.querySelector('.pl-block.selected')||document.querySelector('.pl-block');
      if(first)window.PaperLoomOffline.select(first,false);
      redraw();
      if(focusType)first?.querySelector('.pl-edit-fields select')?.focus();
  }
  for(const [id,node] of sections){
    if(!blocks.has(id) || blocks.get(id).type==='figure')continue;
    const tools=node.querySelector('.pl-block-tools');
    if(tools)control(tools,'修改类型','editor-type',()=>open(node,true));
  }
  window.PaperLoomEditor={command(name){
    if(name==='open')open();
    if(name==='cancel')close();
  },get draft(){collect();return structuredClone(draft);}};
  document.addEventListener('keydown',event=>{
    if(!editing||busy||event.key!=='Delete'||event.target.closest('input,textarea,select'))return;
    const section=document.querySelector('.pl-block.selected');
    if(section?.dataset.blockType==='figure'){
      event.preventDefault();collect();remember();(draft.changes[section.dataset.blockId]||={}).deleted=true;redraw();
    }
  });
})();
