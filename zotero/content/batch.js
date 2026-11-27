"use strict";
window.addEventListener("DOMContentLoaded", () => {
  const args=window.arguments[0].wrappedJSObject,PL=args.controller,owner=args.owner;
  const $=id=>document.getElementById(id),NS="http://www.w3.org/1999/xhtml";
  let rows=[],rowsByID=new Map(),selection=new Set(),page=0,busy=false,loading=false,generation=0,initialized=false;
  if (PL.config.unified) PL.Recognition.populate($("scheme"));
  const size=100;
  let submission=0, displayedBatch=null;
  const element=(tag,text,parent)=>{const node=document.createElementNS(NS,tag);if(text!=null)node.textContent=text;parent?.append(node);return node;};
  const action=()=>$("action").value==="ocr" && $("force").checked ? "force-ocr" : $("action").value;
  const titleOnly=()=>action()==="translate-title";
  const withTitle=()=>action()==="translate" && $("translateTitle").checked;
  const titleText=row=>PL.TitleTranslations.text(row.titleItem);
  const available=row=>titleOnly() ? row.titleAvailable : row.available;
  const chosenRows=()=>rows.filter(row=>available(row)&&selection.has(row.id));
  const label=row=>({ocr:row.source?PL.Store.label(row.source,"ocr"):"—",
    translate:titleOnly() ? (row.titleAvailable ? (titleText(row) ? "已翻译标题" : "未翻译标题") : "—")
      : row.source?PL.Store.label(row.source,"translate"):"—"});
  const filtered=()=>{
    const query=$("search").value.trim().toLocaleLowerCase();
    return rows.filter(row=>!query||[row.title,row.creator,row.year,titleText(row)].join(" ").toLocaleLowerCase().includes(query));
  };
  function counts(){
    const visible=filtered().filter(available),chosen=chosenRows();
    const shown=visible.filter(row=>selection.has(row.id)).length;
    $("selection").textContent=`已选 ${chosen.length} 篇${chosen.length!==shown?`（筛选内 ${shown} 篇）`:""}`;
    $("start").disabled=busy||loading||!chosen.length;
    const translating = action() === "translate";
    const startLabel = titleOnly() ? "开始翻译标题" : translating ? "开始翻译" : PL.config.unified ? "开始识别"
      : action() === "force-ocr" ? "开始重新 OCR" : "开始 OCR";
    $("start").textContent=busy?"正在处理…":`${startLabel}${chosen.length?`（${chosen.length} 篇）`:""}`;
    $("forceRow").hidden=translating || titleOnly();
    $("translateTitleRow").hidden=!translating;
    $("translationColumn").textContent=titleOnly() ? "标题翻译状态" : "翻译状态";
    $("actionHint").textContent=titleOnly() ? "直接翻译文献条目的标题，支持没有 PDF 的文献。"
      : withTitle() ? "译后标题会显示在文献列表的「翻译标题」列。"
      : translating ? "翻译使用当前识别结果，尚未识别时先识别。"
      : $("force").checked ? "勾选的论文将重新识别。" : "已有识别结果会直接复用。";
    $("language").textContent=`翻译目标语言：${PL.language()}`;
    $("schemeRow").hidden=!PL.config.unified || translating || titleOnly();
    if (!titleOnly() && PL.config.unified) {
      const destination = PL.Recognition.destination(translating ? PL.Recognition.defaultMode() : $("scheme").value);
      if (destination) $("actionHint").textContent += ` 需要识别的 PDF 将提交到${destination}。`;
    }
    for(const id of ["scope","action","force","scheme","translateTitle","selectFiltered","selectPending","clearSelection","selectPage"])$(id).disabled=busy||loading;
    $("close").textContent=busy?"关闭（后台继续）":"关闭";
    const onPage=filtered().slice(page*size,(page+1)*size).filter(available);
    $("selectPage").checked=!!onPage.length&&onPage.every(row=>selection.has(row.id));
    $("selectPage").indeterminate=onPage.some(row=>selection.has(row.id))&&!$("selectPage").checked;
  }
  function render(){
    const all=filtered();page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(all.length/size)-1)));
    const current=all.slice(page*size,(page+1)*size),body=$("papers");body.replaceChildren();
    for(const row of current){
      const enabled=available(row);
      const tr=element("tr",null,body);tr.dataset.sourceId=String(row.id);tr.classList.toggle("checked",enabled&&selection.has(row.id));tr.classList.toggle("unavailable",!enabled);
      const check=element("input",null,element("td",null,tr));check.type="checkbox";check.checked=enabled&&selection.has(row.id);check.disabled=busy||loading||!enabled;
      check.setAttribute("aria-label",`选择论文：${row.title}`);check.dataset.id=String(row.id);
      check.addEventListener("change",()=>{check.checked?selection.add(row.id):selection.delete(row.id);tr.classList.toggle("checked",check.checked);counts();});
      const title=element("td",null,tr),name=element("label",row.title,title);name.className="paper-title";name.title=row.title;
      name.addEventListener("click",()=>{if(!check.disabled)check.click();});
      const detail=element("div",null,title);detail.className="paper-detail";detail.dataset.detail="true";
      showDetail(detail,row);
      element("td",[row.creator,row.year].filter(Boolean).join(" · "),tr);
      const statuses=label(row);
      element("td",statuses.ocr,tr).dataset.status="ocr";element("td",statuses.translate,tr).dataset.status="translate";
    }
    $("empty").hidden=!!all.length;$("empty").textContent=loading?"正在加载论文列表…":"没有符合条件的论文。可切换范围或清空搜索。";
    $("pageInfo").textContent=all.length?`${page*size+1}–${Math.min(all.length,(page+1)*size)} / ${all.length} 篇`:"0 篇";
    $("previous").disabled=page===0;$("next").disabled=(page+1)*size>=all.length;
    counts();
  }
  function showDetail(node,row) {
    const detail=titleText(row) || (titleOnly() ? (row.titleAvailable ? "" : "无标题") : row.reason||row.pdfTitle);
    node.textContent=detail;node.title=detail;
  }
  async function load(){
    const request=++generation;loading=true;$("notice").textContent="";render();
    try{
      const loaded=await PL.BatchPicker.rows(owner,$("scope").value);
      if(window.closed||request!==generation)return;
      rows=loaded;
      rowsByID=new Map(rows.map(row=>[String(row.id),row]));
      if(!initialized){selection=new Set(rows.filter(row=>row.preselected || (titleOnly() || !row.source) && row.titlePreselected).map(row=>row.id));initialized=true;}
      else selection=new Set(rows.filter(row=>selection.has(row.id)).map(row=>row.id));
      page=0;
    }catch(error){if(!window.closed&&request===generation){rows=[];$("notice").textContent=error.message;}}
    finally{if(!window.closed&&request===generation){loading=false;render();}}
  }
  function setAction(value){if(busy)return;$("action").value=["translate","translate-title"].includes(value)?value:"ocr";$("force").checked=value==="force-ocr";render();}
  $("search").addEventListener("input",()=>{page=0;render();});$("scope").addEventListener("change",load);
  $("action").addEventListener("change",render);$("force").addEventListener("change",counts);
  $("translateTitle").addEventListener("change",counts);
  $("scheme").addEventListener("change",counts);
  $("selectFiltered").addEventListener("click",()=>{for(const row of filtered())if(available(row))selection.add(row.id);render();});
  $("clearSelection").addEventListener("click",()=>{selection.clear();render();});
  $("selectPending").addEventListener("click",()=>{
    selection.clear();
    for(const row of filtered())if(available(row)){
      if(titleOnly()){if(!titleText(row))selection.add(row.id);continue;}
      const kind=$("action").value,state=PL.Store.get(row.source),status=kind==="translate"?state?.translations?.[PL.language()]:state?.ocr;
      if(!status?.attachmentID||status.state!=="ready"||(kind==="translate"&&PL.Editing.stale(state))
        || (withTitle() && row.titleAvailable && !titleText(row)))selection.add(row.id);
    }
    render();
  });
  $("selectPage").addEventListener("change",()=>{for(const row of filtered().slice(page*size,(page+1)*size))if(available(row))$("selectPage").checked?selection.add(row.id):selection.delete(row.id);render();});
  $("previous").addEventListener("click",()=>{page--;render();});$("next").addEventListener("click",()=>{page++;render();});
  $("close").addEventListener("click",()=>window.close());
  $("start").addEventListener("click",async()=>{
    const chosen=chosenRows().map(row=>row.id);
    if(busy||loading||!chosen.length)return;
    const kind=action(), ticket=++submission;busy=true;displayedBatch=null;render();$("notice").textContent=`已提交 ${chosen.length} 篇，正在后台处理…`;
    try{
      const batch=await PL.BatchPicker.submit(owner,kind,rows,chosen,$("schemeRow").hidden ? undefined : $("scheme").value,withTitle(), batch=>{
        if(!window.closed && ticket===submission){displayedBatch=batch;busy=false;render();}
      });
      if(!window.closed && ticket===submission)$("notice").textContent=`已完成：成功 ${batch?.success||0} 篇${batch?.failed?`，失败 ${batch.failed} 篇`:""}。`;
    }catch(error){if(!window.closed && ticket===submission)$("notice").textContent=error.message;}
    finally{if(!window.closed && ticket===submission){busy=false;render();}}
  });
  const timer=window.setInterval(()=>{
    if(window.closed)return;
    for(const tr of $("papers").children){const row=rowsByID.get(tr.dataset.sourceId);if(!row)continue;const statuses=label(row);for(const [kind,text]of Object.entries(statuses))tr.querySelector(`[data-status="${kind}"]`).textContent=text;showDetail(tr.querySelector("[data-detail]"),row);}
    if(displayedBatch && !displayedBatch.finished){const batch=displayedBatch;$("notice").textContent=`正在处理 ${batch.done}/${batch.total} 篇 · 成功 ${batch.success} · 失败 ${batch.failed}`;}
  },1000);
  window.addEventListener("unload",()=>window.clearInterval(timer),{once:true});
  $("schemeRow").hidden=!PL.config.unified;
  if(PL.config.unified)$("scheme").value=PL.Recognition.defaultMode();
  window.PaperLoomBatch={setAction,reload:load};setAction(args.action);load();
});
