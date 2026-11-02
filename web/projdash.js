/* Per-project dashboard built from widgets (see widgets.js): reorder, hide, resize, share the layout. Click a task for its detail. */
const PD_LEVEL={bad:"bad",warn:"warn",info:"info"};
const PD={paused:false,q:"",task:null};
function pdStat(v,l){return `<div class="stat"><b>${v}</b><span>${l}</span></div>`}
function pdRepaint(){lastBody="";renderProject()}
const pdMatch=i=>!PD.q||`${i.id} ${i.title} ${i.cls||""}`.toLowerCase().includes(PD.q.toLowerCase());
const pdTask=(id,label)=>`<a class="pd-link" onclick="pdOpen('${esc(id)}')">${label??esc(id)}</a>`;
function pdStepRow(x){
  const cur=x.current.step,idx=x.steps.indexOf(cur);
  return `<div class="pd-steps">${x.steps.map((s,i)=>`<span class="pill ${s===cur?"ok":i<idx?"info":""}">${esc(s)}</span>`).join("")}</div>`;
}
async function pdOpen(id){
  try{PD.task=await api(`/api/project/${encodeURIComponent(sel)}/task?id=${encodeURIComponent(id)}`);PD.task.id=id}catch(e){toast(e.message,true);return}
  pdRepaint();
}
async function pdReplay(id){
  try{await api(`/api/project/${encodeURIComponent(sel)}/replay`,{task:id,model:$("#pdrm").value,hint:$("#pdrh").value});toast("Replay started in a sandbox copy; the original project is untouched")}catch(e){toast(e.message,true)}
}
function pdClose(){PD.task=null;pdRepaint()}
function pdFilter(v){PD.q=v;pdRepaint();const e=$("#pdq");if(e){e.focus();e.setSelectionRange(v.length,v.length)}}
function pdPause(){PD.paused=!PD.paused;pdRepaint();if(!PD.paused)refresh()}
function pdDrawer(){
  const t=PD.task;if(!t)return "";
  if(!t.found)return `<div class="panel pd-drawer"><h3>Task ${esc(t.id)}<span class="grow"></span><button class="small" onclick="pdClose()">Close</button></h3><div class="hint">Not found.</div></div>`;
  const i=t.item,list=(a)=>(a&&a.length?`<ul>${a.map(s=>`<li>${esc(typeof s==="string"?s:JSON.stringify(s))}</li>`).join("")}</ul>`:"");
  return `<div class="panel pd-drawer"><h3>${esc(i.id)} ${esc(i.title)} <span class="pill ${i.status==="done"?"ok":i.status==="blocked"?"bad":""}">${esc(i.status)}</span><span class="grow"></span><button class="small" onclick="pdClose()">Close</button></h3>
    <div class="meta">${i.type} · layer ${i.layer??"–"} · prio ${Math.round((i.priority||0)*10)/10} · ${i.attempts||0} attempt(s)${i.runtime_s?" · "+dur(i.runtime_s)+" total":""}${i.risk==="high"?" · risk high":""}</div>
    ${i.reason?`<p><b>Why:</b> ${esc(i.reason)}</p>`:""}<p>${esc(i.description||"")}</p>
    ${i.depends_on&&i.depends_on.length?`<div class="meta">builds on ${i.depends_on.map(d=>pdTask(d)).join(", ")}</div>`:""}
    ${t.failures.length?`<h4>Failed attempts</h4>${t.failures.map(f=>`<div class="cap"><div><span class="pill warn">${esc(f.cls||"")}</span> ${esc(f.why)}</div><div class="meta">attempt ${f.attempt??"?"}</div></div>`).join("")}`:""}
    ${t.plan.understanding?`<h4>Plan</h4><p>${esc(t.plan.understanding)}</p>${list(t.plan.steps)}`:""}
    ${t.journal.length?`<h4>Journal</h4><pre class="pd-j">${esc(t.journal.join("\n"))}</pre>`:""}
    ${t.notes.length?`<details data-k="pd-notes"><summary>Notes (${t.notes.length})</summary>${list(t.notes)}</details>`:""}
    <h4>Replay</h4><div class="row" style="gap:6px;flex-wrap:wrap"><input id="pdrm" placeholder="other model (optional), e.g. qwen2.5-coder:14b" style="flex:1;min-width:200px"><input id="pdrh" placeholder="hint (optional)" style="flex:2;min-width:200px"><button class="small" onclick="pdReplay('${esc(i.id)}')">Replay in a sandbox copy</button></div>
    ${(t.replays||[]).filter(r=>r.task===i.id).map(r=>`<div class="cap"><div>${esc(r.model||"same model")} ${r.hint?"· "+esc(r.hint):""}</div><div class="meta"><span class="pill ${r.status==="done"?"ok":"warn"}">${esc(r.status)}</span> ${dur(r.seconds)} · ${esc(r.time)}</div></div>`).join("")}</div>`;
}
function pdGantt(x){
  const rows=x.timeline.filter(pdMatch);if(!rows.length)return "";
  const total=Math.max(1,...x.timeline.map(r=>r.start+r.seconds));
  return rows.map(r=>`<div class="gantt" onclick="pdOpen('${esc(r.id)}')" title="${esc(r.title)}: ${dur(r.seconds)}, ${r.attempts} attempt(s)"><span class="gl">${esc(r.id)} ${esc(r.title)}</span><span class="gt"><i class="${r.status==="done"?"":"run"}" style="margin-left:${(100*r.start/total).toFixed(1)}%;width:${Math.max(1,100*r.seconds/total).toFixed(1)}%"></i></span><span class="meta">${dur(r.seconds)}</span></div>`).join("");
}
function pdBars(x){
  const d=x.daily||[],max=Math.max(1,...d.map(r=>r.tokens));if(!d.some(r=>r.tokens||r.gpu_hours))return "";
  return `<div class="pd-bars">${d.map(r=>`<span title="${r.day}: ${r.tokens} tokens, ${r.gpu_hours} GPU h"><i style="height:${Math.round(100*r.tokens/max)}%"></i></span>`).join("")}</div>
    <div class="meta">last 14 days · tokens ${d.reduce((a,r)=>a+r.tokens,0).toLocaleString()} · GPU ${Math.round(d.reduce((a,r)=>a+r.gpu_hours,0)*10)/10} h</div>`;
}
const PD_WIDGETS={
  attention:{title:"Needs attention",body:x=>(x.findings||[]).map(a=>`<div class="cap"><span class="pill ${PD_LEVEL[a.level]||""}">${a.level}</span> <div>${esc(a.text)}</div></div>`).join("")||`<div class="hint">Nothing needs attention.</div>`},
  now:{title:"Right now",body:x=>{const c=x.current,s=x.status;return (c.task?`<div><b>${esc(c.task)}</b> <span class="meta">attempt ${c.attempt||1}${c.since_s!=null?" · "+dur(c.since_s)+" in this step":""}</span></div>${pdStepRow(x)}${pctBar(c.pct)}<div class="hint">${esc(c.phase)}</div>`
    :`<div class="hint">${esc(c.phase||(s.running?"Choosing the next task…":"The agent is not running."))}</div>`)+
    (x.events.length?`<details data-k="pd-events"><summary>Last ${x.events.length} steps of this iteration</summary>${x.events.map(e=>`<div class="meta">${esc((e.time||"").slice(11))} ${esc(e.task?e.task.split(" ")[0]+" · ":"")}${esc(e.phase)}</div>`).join("")}</details>`:"")}},
  queue:{title:"Next up (pick order)",size:2,body:x=>{const q=x.queue.filter(pdMatch);return q.length?`<table><tr><th>ID</th><th>Task</th><th>Layer</th><th>Prio</th><th>Adj.</th><th></th></tr>${q.map(i=>
    `<tr class="clk" onclick="pdOpen('${esc(i.id)}')"><td>${esc(i.id)}</td><td>${esc(i.title)}${i.source==="user"?' <span class="pill ok">you</span>':""}${i.risk==="high"?' <span class="pill warn">risk</span>':""}</td><td>${i.layer!=null?"L"+i.layer:"–"}</td><td>${i.priority!=null?Math.round(i.priority*10)/10:"–"}</td><td>${i.dyn?(i.dyn>0?"+":"")+i.dyn:""}</td><td>${i.waiting_for.length?`<span class="meta">waits for ${esc(i.waiting_for.join(", "))}</span>`:i.attempts?`<span class="meta">${i.attempts} failed</span>`:""}</td></tr>`).join("")}</table>`:`<div class="hint">No open tasks.</div>`}},
  layers:{title:"Layers",body:x=>(x.layers.map(l=>`<div class="cap"><div>${l.layer!=null?"L"+l.layer:"No layer"} ${esc(l.name)}${l.blocked?` <span class="pill bad">${l.blocked} blocked</span>`:""}</div><div class="meta">${l.done}/${l.total}${l.layer!=null&&l.done===l.total&&l.total?` <button class="sm" onclick="pdRollback(${l.layer})">Roll back here</button>`:""}</div></div><div class="bar"><i style="width:${l.total?Math.round(100*l.done/l.total):0}%"></i></div>`).join("")||`<div class="hint">No tasks yet.</div>`)+`<div class="row"><button class="sm" onclick="pdExport()">Export as repo</button><button class="sm" onclick="pdReport()">Send daily report</button></div>`},
  gates:{title:"Layer gates",body:x=>(x.gates||[]).map(g=>`<div class="cap"><div>L${g.layer} ${esc(g.name)}<small>${esc(g.note||"")}</small></div><span class="pill ${g.status==="passed"?"ok":g.status==="failed"?"bad":"warn"}">${esc(g.status||"?")}</span></div>`).join("")},
  drift:{title:"Architecture drift",body:x=>(x.drift||[]).map(v=>`<div class="cap"><div>${esc(v.file)}<small>${esc(v.problem)}</small></div><span class="pill warn">${esc(v.kind)}</span></div>`).join("")},
  triage:{title:"Triage",body:x=>{const tr=x.triage;return `<div class="hint">${tr.merges_since_layout} merge(s) since the last re-layout${tr.last_replan_at_merge!=null?` · last re-plan at merge ${tr.last_replan_at_merge}`:""}</div>`+
    (tr.log&&tr.log.length?`<details open data-k="pd-tlog"><summary>Priority changes with reasons</summary>${tr.log.slice().reverse().map(l=>`<div class="cap"><div>${pdTask(l.id)} ${esc(l.title)}<small>${esc(l.reason)}</small></div><div class="meta">${l.from} → ${l.to}</div></div>`).join("")}</details>`:"")+
    (tr.adjusted.length?tr.adjusted.map(i=>`<div class="cap"><div>${esc(i.id)} ${esc(i.title)}</div><div class="meta">${i.dyn>0?"+":""}${i.dyn}</div></div>`).join(""):`<div class="hint">No priority adjustments at the moment.</div>`)}},
  timeline:{title:"Task timeline (runtime)",size:2,body:pdGantt},
  causes:{title:"Failure causes",body:x=>(x.causes||[]).filter(c=>pdMatch({id:"",title:"",cls:c.cls})||c.tasks.some(pdMatch)).map(c=>`<div class="cap"><div><span class="pill warn">${esc(c.cls)}</span> ${c.n}×<small>${c.tasks.map(t=>pdTask(t.id,esc(t.id+" "+t.title))).join(" · ")}</small></div></div>`).join("")},
  steptime:{title:"Time per pipeline step",body:x=>{const e=Object.entries(x.step_time||{}),tot=e.reduce((a,[,v])=>a+v,0);return e.length?e.map(([k,v])=>`<div class="cap"><div>${esc(k)}</div><div class="meta">${dur(v)} · ${Math.round(100*v/tot)}%</div></div><div class="bar"><i style="width:${Math.round(100*v/tot)}%"></i></div>`).join(""):""}},
  tokens:{title:"Tokens and GPU",body:pdBars},
  roles:{title:"Model per role",body:x=>Object.entries(x.role_models||{}).map(([k,v])=>`<div class="cap"><div>${esc(k)}</div><div class="meta">${esc(v)}</div></div>`).join("")+(x.advice||[]).map(a=>`<div class="row" style="margin:4px 0"><span class="pill info">advice</span> ${esc(a.text)}</div>`).join("")},
  regress:{title:"Regression tests",body:x=>(x.regressions||[]).slice().reverse().map(r=>`<div class="cap"><div>${esc(r.item)} ${esc(r.title)}</div><div class="meta">${(r.tests||[]).length} test(s)</div></div>`).join("")||`<div class="hint">Every fixed bug gets a mandatory test; none recorded yet.</div>`},
  quality:{title:"Quality",body:x=>{const q=x.quality,f=x.flow||{},bad=Object.entries(q.by_class||{}).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([k,n])=>`<span class="pill warn">${esc(k)} ${n}</span>`).join(" ");
    return `<div class="stats">${pdStat(f.first_try_rate!=null?pct(f.first_try_rate):"–","first try")}${pdStat(f.avg_attempts!=null?f.avg_attempts:"–","attempts / task")}${pdStat(q.fail_rate!=null?pct(q.fail_rate):"–","attempts failing")}${pdStat(f.avg_review_score!=null?f.avg_review_score:"–","review score")}${pdStat(q.flaky,"flaky tests")}${pdStat(q.stalled,"stalls")}</div>${bad?`<div>${bad}</div>`:""}`}},
  model:{title:"Model time",body:x=>{const m=x.models||{},q=x.quality;return `<div class="stats">${pdStat(m.avg_call_s!=null?dur(m.avg_call_s):"–","avg per call")}${pdStat(m.reasoning_share!=null?pct(m.reasoning_share):"–","reasoning share")}${pdStat(q.gpu_hours,"GPU hours")}${pdStat(x.forecast&&x.forecast.known?dur(x.forecast.total_s):"–","forecast for the backlog")}</div>`}},
  code:{title:"Code base",body:x=>{const r=x.repo||{};return `<div class="stats">${pdStat(r.files??"–","files")}${pdStat(r.loc??"–","lines")}${pdStat(r.tests??"–","tests")}${pdStat(r.commits??"–","commits")}</div>`}},
  blocked:{title:"Blocked tasks",body:x=>x.blocked.filter(pdMatch).map(i=>`<div class="cap"><div>${pdTask(i.id,esc(i.id+" "+i.title))}<small>${esc(i.note)}</small></div><div class="meta">${i.attempts} attempts</div></div>`).join("")},
  journal:{title:"Task journal (current task)",size:2,body:x=>x.journal.length?`<pre class="pd-j">${esc(x.journal.join("\n"))}</pre>`:""},
};
function pdBody(d){
  const x=extra&&extra.kind==="pdash"&&extra.name===d.name?extra:null;
  if(!x)return `<div class="hint">Loading…</div>`;
  const s=x.status,h=x.health||{},b=x.backlog||{},key="pd-"+d.name;
  WBS[key]||wbInit(key,PD_WIDGETS,pdRepaint);
  const total=Object.values(b).reduce((a,n)=>a+n,0)-(b.cancelled||0),done=b.done||0;
  const stats=[pdStat(s.running?"running":"stopped","agent"),pdStat(s.iteration,"iteration"),pdStat(s.merged,"merged"),pdStat(`${done}/${total}`,"tasks done"),
    pdStat(s.recent_total?`${s.recent_ok}/${s.recent_total}`:"–","last tasks ok"),pdStat(s.failures,"consecutive failures"),pdStat(s.hours+" h","runtime"),
    pdStat(s.tasks_per_hour!=null?s.tasks_per_hour:"–","merged / hour"),pdStat(h.idle_s!=null?dur(h.idle_s):"–","since last activity"),
    pdStat(x.perf&&x.perf.tps?x.perf.tps:"–","tokens/s avg")].join("");
  const tools=`<div class="dtools"><input id="pdq" placeholder="Filter tasks…" value="${esc(PD.q)}" oninput="pdFilter(this.value)"><span class="grow"></span>
    <button class="small" onclick="pdPause()">${PD.paused?"▶ Resume updates":"⏸ Pause updates"}</button>${wbMenu(key)}</div>`;
  return `<div class="stats">${stats}</div>${tools}${pdDrawer()}${wbTiles(key,x)}`;
}

async function pdRollback(n){if(!confirm(`Reset the project to the state after layer ${n}? Later work is removed from main (a backup tag keeps it). The agent must be stopped.`))return;try{const r=await api(`/api/project/${encodeURIComponent(sel)}/rollback`,{layer:n});toast("Rolled back to layer "+n+", backup tag "+r.backup)}catch(e){toast(e.message,true)}}
async function pdExport(){try{const r=await api(`/api/project/${encodeURIComponent(sel)}/export`,{});toast("Exported "+r.files+" files to "+r.path)}catch(e){toast(e.message,true)}}
async function pdReport(){try{const r=await api(`/api/project/${encodeURIComponent(sel)}/report`,{});toast(r.ok?"Report sent":"No webhook configured for the report event",!r.ok)}catch(e){toast(e.message,true)}}
