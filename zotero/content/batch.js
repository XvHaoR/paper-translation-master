"use strict";
window.addEventListener("DOMContentLoaded", () => {
  const args=window.arguments[0].wrappedJSObject,PL=args.controller,owner=args.owner;
  const $=id=>document.getElementById(id),NS="http://www.w3.org/1999/xhtml";
  let rows=[],rowsByID=new Map(),selection=new Set(),page=0,busy=false,loading=false,generation=0,initialized=false;
  const size=100;
  const element=(tag,text,parent)=>{const node=document.createElementNS(NS,tag);if(text!=null)node.textContent=text;parent?.append(node);return node;};
  const action=()=>$("action").value==="translate"?"translate":$("force").checked?"force-ocr":"ocr";
  const label=row=>({ocr:row.source?PL.Store.label(row.source,"ocr"):"—",translate:row.source?PL.Store.label(row.source,"translate"):"—"});
  const filtered=()=>{
    const query=$("search").value.trim().toLocaleLowerCase();
    return rows.filter(row=>!query||[row.title,row.creator,row.year].join(" ").toLocaleLowerCase().includes(query));
  };
  function counts(){
    const visible=filtered().filter(row=>row.available),chosen=rows.filter(row=>row.available&&selection.has(row.id));
    const shown=visible.filter(row=>selection.has(row.id)).length;
    $("selection").textContent=`已选 ${chosen.length} 篇${chosen.length!==shown?`（筛选内 ${shown} 篇）`:""}`;
    $("start").disabled=busy||loading||!chosen.length;
    $("start").textContent=busy?"正在处理…":`${action()==="translate"?"开始翻译":action()==="force-ocr"?"开始重新 OCR":"开始 OCR"}${chosen.length?`（${chosen.length} 篇）`:""}`;
    $("forceRow").hidden=$("action").value==="translate";
    $("actionHint").textContent=$("action").value==="translate"?"已有译文直接复用；没有 OCR 的论文会先识别再翻译。":$("force").checked?"勾选的论文将重新 OCR，不复用已有识别缓存。":"已有 OCR 直接复用；只处理勾选的论文。";
    $("language").textContent=`翻译目标语言：${PL.language()}`;
    for(const id of ["scope","action","force","selectFiltered","selectPending","clearSelection","selectPage"])$(id).disabled=busy||loading;
    $("close").textContent=busy?"关闭（后台继续）":"关闭";
    const onPage=filtered().slice(page*size,(page+1)*size).filter(row=>row.available);
    $("selectPage").checked=!!onPage.length&&onPage.every(row=>selection.has(row.id));
    $("selectPage").indeterminate=onPage.some(row=>selection.has(row.id))&&!$("selectPage").checked;
  }
  function render(){
    const all=filtered();page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(all.length/size)-1)));
    const current=all.slice(page*size,(page+1)*size),body=$("papers");body.replaceChildren();
    for(const row of current){
      const tr=element("tr",null,body);tr.dataset.sourceId=String(row.id);tr.classList.toggle("checked",selection.has(row.id));tr.classList.toggle("unavailable",!row.available);
      const check=element("input",null,element("td",null,tr));check.type="checkbox";check.checked=selection.has(row.id);check.disabled=busy||loading||!row.available;
      check.setAttribute("aria-label",`选择论文：${row.title}`);check.dataset.id=String(row.id);
      check.addEventListener("change",()=>{check.checked?selection.add(row.id):selection.delete(row.id);tr.classList.toggle("checked",check.checked);counts();});
      const title=element("td",null,tr),name=element("label",row.title,title);name.className="paper-title";name.title=row.title;
      name.addEventListener("click",()=>{if(!check.disabled)check.click();});
      element("div",row.reason||row.pdfTitle,title).className="paper-detail";
      element("td",[row.creator,row.year].filter(Boolean).join(" · "),tr);
      const statuses=label(row);
      element("td",statuses.ocr,tr).dataset.status="ocr";element("td",statuses.translate,tr).dataset.status="translate";
    }
    $("empty").hidden=!!all.length;$("empty").textContent=loading?"正在加载论文列表…":"没有符合条件的论文。可切换范围或清空搜索。";
    $("pageInfo").textContent=all.length?`${page*size+1}–${Math.min(all.length,(page+1)*size)} / ${all.length} 篇`:"0 篇";
    $("previous").disabled=page===0;$("next").disabled=(page+1)*size>=all.length;
    counts();
  }
  async function load(){
    const request=++generation;loading=true;$("notice").textContent="";render();
    try{
      const loaded=await PL.BatchPicker.rows(owner,$("scope").value);
      if(window.closed||request!==generation)return;
      rows=loaded;
      rowsByID=new Map(rows.map(row=>[String(row.id),row]));
      if(!initialized){selection=new Set(rows.filter(row=>row.available&&row.preselected).map(row=>row.id));initialized=true;}
      else selection=new Set(rows.filter(row=>row.available&&selection.has(row.id)).map(row=>row.id));
      page=0;
    }catch(error){if(!window.closed&&request===generation){rows=[];$("notice").textContent=error.message;}}
    finally{if(!window.closed&&request===generation){loading=false;render();}}
  }
  function setAction(value){if(busy)return;$("action").value=value==="translate"?"translate":"ocr";$("force").checked=value==="force-ocr";counts();}
  $("search").addEventListener("input",()=>{page=0;render();});$("scope").addEventListener("change",load);
  $("action").addEventListener("change",counts);$("force").addEventListener("change",counts);
  $("selectFiltered").addEventListener("click",()=>{for(const row of filtered())if(row.available)selection.add(row.id);render();});
  $("clearSelection").addEventListener("click",()=>{selection.clear();render();});
  $("selectPending").addEventListener("click",()=>{
    selection.clear();
    for(const row of filtered())if(row.available){
      const kind=$("action").value,state=PL.Store.get(row.source),status=kind==="translate"?state?.translations?.[PL.language()]:state?.ocr;
      if(!status?.attachmentID||status.state!=="ready"||(kind==="translate"&&PL.Editing.stale(state)))selection.add(row.id);
    }
    render();
  });
  $("selectPage").addEventListener("change",()=>{for(const row of filtered().slice(page*size,(page+1)*size))if(row.available)$("selectPage").checked?selection.add(row.id):selection.delete(row.id);render();});
  $("previous").addEventListener("click",()=>{page--;render();});$("next").addEventListener("click",()=>{page++;render();});
  $("close").addEventListener("click",()=>window.close());
  $("start").addEventListener("click",async()=>{
    if(busy||loading||!selection.size)return;
    const chosen=[...selection],kind=action();busy=true;render();$("notice").textContent=`已提交 ${chosen.length} 篇，正在后台处理…`;
    try{
      const batch=await PL.BatchPicker.submit(owner,kind,rows,chosen);
      if(!window.closed)$("notice").textContent=`已完成：成功 ${batch?.success||0} 篇${batch?.failed?`，失败 ${batch.failed} 篇`:""}。可关闭窗口后自行查看结果。`;
    }catch(error){if(!window.closed)$("notice").textContent=error.message;}
    finally{if(!window.closed){busy=false;render();}}
  });
  const timer=window.setInterval(()=>{
    if(window.closed)return;
    for(const tr of $("papers").children){const row=rowsByID.get(tr.dataset.sourceId);if(!row)continue;const statuses=label(row);for(const [kind,text]of Object.entries(statuses))tr.querySelector(`[data-status="${kind}"]`).textContent=text;}
    if(busy){const batch=PL.Tasks.lastBatch;$("notice").textContent=batch&&!batch.finished?`正在处理 ${batch.done}/${batch.total} 篇 · 成功 ${batch.success} · 失败 ${batch.failed}`:$("notice").textContent;}
  },1000);
  window.addEventListener("unload",()=>window.clearInterval(timer),{once:true});
  window.PaperLoomBatch={setAction,reload:load};setAction(args.action);load();
});
