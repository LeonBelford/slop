/* Per-project dashboard: status, current step, queue in pick order, layers, quality, model time, triage, blocked tasks, journal. */
const PD_LEVEL={bad:"bad",warn:"warn",info:"info"};
function pdStat(v,l){return `<div class="stat"><b>${v}</b><span>${l}</span></div>`}
function pdPanel(t,h){return `<div class="panel"><h3>${t}</h3>${h}</div>`}
function pdStepRow(x){
  const cur=x.current.step,idx=x.steps.indexOf(cur);
  return `<div class="pd-steps">${x.steps.map((s,i)=>`<span class="pill ${s===cur?"ok":i<idx?"info":""}">${esc(s)}</span>`).join("")}</div>`;
}
function pdBody(d){
  const x=extra&&extra.kind==="pdash"&&extra.name===d.name?extra:null;
  if(!x)return `<div class="hint">Loading…</div>`;
  const s=x.status,c=x.current,q=x.quality,m=x.models||{},f=x.flow||{},h=x.health||{},b=x.backlog||{};
  const total=Object.values(b).reduce((a,n)=>a+n,0)-(b.cancelled||0),done=b.done||0;
  const att=(x.findings||[]).map(a=>`<div class="cap"><span class="pill ${PD_LEVEL[a.level]||""}">${a.level}</span> <div>${esc(a.text)}</div></div>`).join("")||`<div class="hint">Nothing needs attention.</div>`;
  const stats=[pdStat(s.running?"running":"stopped","agent"),pdStat(s.iteration,"iteration"),pdStat(s.merged,"merged"),pdStat(`${done}/${total}`,"tasks done"),
    pdStat(s.recent_total?`${s.recent_ok}/${s.recent_total}`:"–","last tasks ok"),pdStat(s.failures,"consecutive failures"),pdStat(s.hours+" h","runtime"),
    pdStat(s.tasks_per_hour!=null?s.tasks_per_hour:"–","merged / hour"),pdStat(h.idle_s!=null?dur(h.idle_s):"–","since last activity"),
    pdStat(x.perf&&x.perf.tps?x.perf.tps:"–","tokens/s avg")].join("");
  const now=pdPanel("Right now",c.task?`<div><b>${esc(c.task)}</b> <span class="meta">attempt ${c.attempt||1}${c.since_s!=null?" · "+dur(c.since_s)+" in this step":""}</span></div>${pdStepRow(x)}${pctBar(c.pct)}<div class="hint">${esc(c.phase)}</div>`
    :`<div class="hint">${esc(c.phase||(s.running?"Choosing the next task…":"The agent is not running."))}</div>`)+
    (x.events.length?`<details data-k="pd-events"><summary>Last ${x.events.length} steps of this iteration</summary>${x.events.map(e=>`<div class="meta">${esc((e.time||"").slice(11))} ${esc(e.task?e.task.split(" ")[0]+" · ":"")}${esc(e.phase)}</div>`).join("")}</details>`:"");
  const queue=pdPanel("Next up (pick order)",x.queue.length?`<table><tr><th>ID</th><th>Task</th><th>Layer</th><th>Prio</th><th>Adj.</th><th></th></tr>${x.queue.map(i=>
    `<tr><td>${esc(i.id)}</td><td>${esc(i.title)}${i.source==="user"?' <span class="pill ok">you</span>':""}</td><td>${i.layer!=null?"L"+i.layer:"–"}</td><td>${i.priority!=null?Math.round(i.priority*10)/10:"–"}</td><td>${i.dyn?(i.dyn>0?"+":"")+i.dyn:""}</td><td>${i.waiting_for.length?`<span class="meta">waits for ${esc(i.waiting_for.join(", "))}</span>`:i.attempts?`<span class="meta">${i.attempts} failed</span>`:""}</td></tr>`).join("")}</table>`:`<div class="hint">No open tasks.</div>`);
  const lay=pdPanel("Layers",x.layers.map(l=>`<div class="cap"><div>${l.layer!=null?"L"+l.layer:"No layer"} ${esc(l.name)}${l.blocked?` <span class="pill bad">${l.blocked} blocked</span>`:""}</div><div class="meta">${l.done}/${l.total}</div></div><div class="bar"><i style="width:${l.total?Math.round(100*l.done/l.total):0}%"></i></div>`).join("")||`<div class="hint">No tasks yet.</div>`);
  const tr=x.triage,triage=pdPanel("Triage",`<div class="hint">${tr.merges_since_layout} merge(s) since the last re-layout${tr.last_replan_at_merge!=null?` · last re-plan at merge ${tr.last_replan_at_merge}`:""}</div>`+
    (tr.log&&tr.log.length?`<details open data-k="pd-tlog"><summary>Priority changes with reasons</summary>${tr.log.slice().reverse().map(l=>`<div class="cap"><div>${esc(l.id)} ${esc(l.title)}<small>${esc(l.reason)}</small></div><div class="meta">${l.from} → ${l.to}</div></div>`).join("")}</details>`:"")+(tr.adjusted.length?tr.adjusted.map(i=>`<div class="cap"><div>${esc(i.id)} ${esc(i.title)}</div><div class="meta">${i.dyn>0?"+":""}${i.dyn}</div></div>`).join(""):`<div class="hint">No priority adjustments at the moment.</div>`));
  const bad=Object.entries(q.by_class||{}).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([k,n])=>`<span class="pill warn">${esc(k)} ${n}</span>`).join(" ");
  const qual=pdPanel("Quality",`<div class="stats">${pdStat(f.first_try_rate!=null?pct(f.first_try_rate):"–","first try")}${pdStat(f.avg_attempts!=null?f.avg_attempts:"–","attempts / task")}${pdStat(q.fail_rate!=null?pct(q.fail_rate):"–","attempts failing")}${pdStat(f.avg_review_score!=null?f.avg_review_score:"–","review score")}${pdStat(q.flaky,"flaky tests")}${pdStat(q.stalled,"stalls")}</div>${bad?`<div>${bad}</div>`:""}`);
  const mod=pdPanel("Model time",`<div class="stats">${pdStat(m.avg_call_s!=null?dur(m.avg_call_s):"–","avg per call")}${pdStat(m.reasoning_share!=null?pct(m.reasoning_share):"–","reasoning share")}${pdStat(q.gpu_hours,"GPU hours")}${pdStat(x.forecast&&x.forecast.known?dur(x.forecast.total_s):"–","forecast for the backlog")}</div>`);
  const blk=x.blocked.length?pdPanel("Blocked tasks",x.blocked.map(i=>`<div class="cap"><div>${esc(i.id)} ${esc(i.title)}<small>${esc(i.note)}</small></div><div class="meta">${i.attempts} attempts</div></div>`).join("")):"";
  const jr=x.journal.length?pdPanel("Task journal (current task)",`<pre class="pd-j">${esc(x.journal.join("\n"))}</pre>`):"";
  const repo=x.repo||{};
  const rp=pdPanel("Code base",`<div class="stats">${pdStat(repo.files??"–","files")}${pdStat(repo.loc??"–","lines")}${pdStat(repo.tests??"–","tests")}${pdStat(repo.commits??"–","commits")}</div>`);
  return `<div class="stats">${stats}</div>${pdPanel("Needs attention",att)}<div class="grid2">${now}${queue}${lay}${triage}${qual}${mod}${rp}${blk}</div>${jr}`;
}
