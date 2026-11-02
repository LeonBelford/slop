/* Overview = the task graph on top, the same tasks as a tree list below (a task hangs under its first dependency).
   Finished graphs and cancelled tasks only on request; a finished task stays while its graph still has open work. */
const ttCollapsed=new Set();
function ttToggle(id){ttCollapsed.has(id)?ttCollapsed.delete(id):ttCollapsed.add(id);lastBody="";renderProject()}
function ttForest(items,ids){
  const deps=tgDeps(items.filter(i=>ids.has(i.id))),kids={},parent={};
  const reach=(from,target,seen=new Set())=>{if(from===target)return true;if(seen.has(from))return false;seen.add(from);return(kids[from]||[]).some(k=>reach(k,target,seen))};
  Object.keys(deps).sort().forEach(k=>{
    const p=deps[k].find(d=>!reach(k,d));
    if(p){parent[k]=p;(kids[p]=kids[p]||[]).push(k)}
  });
  const node=(id,depth)=>({id,depth,also:deps[id].filter(d=>d!==parent[id]),kids:(kids[id]||[]).sort().map(k=>node(k,depth+1))});
  return Object.keys(deps).sort().filter(k=>!parent[k]).map(k=>node(k,0));
}
function ttRows(n,by,ctx){
  const i=by[n.id],cur=i.id===ctx.liveId&&i.status!=="done"&&i.status!=="blocked",shut=ttCollapsed.has(n.id)&&n.kids.length;
  const prog=cur&&ctx.pct!=null?pctBar(ctx.pct):i.status==="done"?`<span class="meta">100%</span>`:`<span class="meta">-</span>`;
  const caret=n.kids.length?`<span class="tt-c" onclick="ttToggle('${esc(i.id)}')" title="${shut?"Expand":"Collapse"} ${n.kids.length} follow-up task(s)">${shut?"▸":"▾"}</span>`:`<span class="tt-c tt-dot">·</span>`;
  const also=n.also.length?` <span class="meta" title="This task also needs these tasks">+ needs ${n.also.map(esc).join(", ")}</span>`:"";
  const row=`<tr class="tt-r tt-${esc(i.status)}"><td>${esc(i.id)}</td><td style="padding-left:${8+n.depth*20}px">${caret}${i.layer!=null?`<span class="pill info" title="Build layer: lower layers are built first">L${i.layer}</span> `:""}${esc(i.title)}${i.source==="ideas"?' <span class="pill ok">from you</span>':i.source==="user"?' <span class="pill info">your task</span>':""}${also}</td><td>${esc(i.type)}</td><td>${Math.round(i.priority*10)/10}</td><td><span class="pill ${i.status==="done"?"ok":i.status==="blocked"?"bad":""}">${esc(i.status)}</span>${i.status==="blocked"&&i.reason?`<div class="meta reason" title="${esc(i.reason)}">Why: ${esc(i.reason)}</div>`:""}</td><td style="min-width:90px">${prog}</td><td style="white-space:nowrap">${taskBtns(i,ctx.running)}</td></tr>`;
  return row+(shut?"":n.kids.map(k=>ttRows(k,by,ctx)).join(""));
}
function treeListPanel(d){
  const P=pref(),all=d.backlog_items||[],live=tgLive(all),by=Object.fromEntries(live.map(i=>[i.id,i])),vis=tgVisible(live,P.showDone);
  const act=d.activity&&d.activity.now,ctx={liveId:act&&act.task?act.task.split(" ")[0]:null,pct:act?act.pct:null,running:d.running};
  const groups=tgComps(live).filter(c=>c.some(n=>vis.has(n))).map((c,n)=>{
    const done=c.filter(x=>by[x].status==="done").length,fin=done===c.length;
    return `<tr class="tt-g"><td colspan="7"><b>Graph ${n+1}</b> <span class="meta">${done}/${c.length} done${fin?" · finished":""}</span></td></tr>`+ttForest(live,new Set(c)).map(r=>ttRows(r,by,ctx)).join("");
  }).join("");
  const gone=all.filter(i=>i.status==="cancelled");
  const cancelled=P.showDone&&gone.length?`<details style="margin-top:10px"><summary class="meta">Cancelled tasks (${gone.length})</summary><table>${gone.map(i=>`<tr><td>${esc(i.id)}</td><td>${esc(i.title)}${i.reason?`<div class="meta reason">${esc(i.reason)}</div>`:""}</td><td style="white-space:nowrap">${taskBtns(i,d.running)}</td></tr>`).join("")}</table></details>`:"";
  return `<div class="panel"><h3>Tasks as a tree <span class="meta">every graph from its first task down</span></h3><table class="tt"><tr><th>ID</th><th>Task</th><th>Type</th><th>Prio</th><th>Status</th><th>Progress</th><th></th></tr>${groups||'<tr><td colspan=7 class="hint">No open tasks.</td></tr>'}</table>${cancelled}</div>`;
}
function overviewBody(d){
  const P=pref();
  const plan=(d.plan||[]).length?`<details class="panel" style="margin-top:16px"><summary><b>Build plan (core first)</b> <span class="meta">${d.plan.length} layers</span></summary>${d.plan.map(l=>`<div class="cap"><div><b>L${l.layer} ${esc(l.name)}</b> <span class="meta">${esc(l.goal)}</span></div><div style="min-width:140px">${pctBar(l.total?Math.round(100*l.done/l.total):0)} <span class="meta">${l.done}/${l.total}</span></div></div>`).join("")}</details>`:"";
  return tgPanel(d)+`<div style="margin-top:16px">${treeListPanel(d)}</div>`+plan+(P.showDone?`<div style="margin-top:16px">${finishedTasks(d)}</div>`:"");
}
