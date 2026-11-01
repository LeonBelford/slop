const DKEY="dashpref",DSEC={live:"Live figures",attention:"Needs attention",projects:"Per project (live)",history:"History (30 days)",estimates:"Time estimates",diag:"Project diagnostics"},
  DCARDS={agents:"Agents running",tps:"Tokens / s",tpm:"Tokens / min (avg)",alerts:"Alerts",chars:"Characters now",gpu:"GPU",vram:"VRAM",models:"Loaded models",today:"Today",all:"All time"},
  DFILTERS=[["all","All"],["running","Running"],["idle","Idle"],["attention","Needs attention"]];
const DDEF=()=>({collapsed:{},hide:{},cards:{},order:Object.keys(DSEC),sort:{},q:"",f:"all",paused:false,notify:false,hmetric:"tokens",hscope:"total"});
let DP=(()=>{try{const p={...DDEF(),...JSON.parse(localStorage.getItem(DKEY)||"{}")};p.order=[...p.order.filter(k=>DSEC[k]),...Object.keys(DSEC).filter(k=>!p.order.includes(k))];return p}catch(e){return DDEF()}})();
let DLAST=null;const DT={};
const dsave=()=>{localStorage.setItem(DKEY,JSON.stringify(DP));dhash()};
/* The view (filter, sort, hidden and collapsed sections) is mirrored into the URL hash so a link restores it. */
function dhash(){
  if(view!=="dash")return;
  const u=new URLSearchParams();
  if(DP.q)u.set("q",DP.q);if(DP.f!=="all")u.set("f",DP.f);
  const h=Object.keys(DP.hide).filter(k=>DP.hide[k]),c=Object.keys(DP.collapsed).filter(k=>DP.collapsed[k]);
  if(h.length)u.set("hide",h.join(","));if(c.length)u.set("col",c.join(","));
  const so=Object.entries(DP.sort).map(([t,v])=>`${t}.${v.k}.${v.dir}`);if(so.length)u.set("sort",so.join(","));
  if(DP.hmetric!=="tokens")u.set("hm",DP.hmetric);if(DP.hscope!=="total")u.set("hs",DP.hscope);
  history.replaceState(null,"","#dash"+(u.toString()?"?"+u:""));
}
function dfromhash(){
  if(!location.hash.startsWith("#dash"))return;
  const u=new URLSearchParams(location.hash.split("?")[1]||""),has=k=>u.has(k);
  DP.q=u.get("q")||"";DP.f=DFILTERS.some(x=>x[0]===u.get("f"))?u.get("f"):"all";
  DP.hide={};(u.get("hide")||"").split(",").filter(k=>DSEC[k]).forEach(k=>DP.hide[k]=true);
  DP.collapsed={};(u.get("col")||"").split(",").filter(k=>DSEC[k]).forEach(k=>DP.collapsed[k]=true);
  DP.sort={};(u.get("sort")||"").split(",").forEach(x=>{const[t,k,d]=x.split(".");if(t&&k&&(d==="asc"||d==="desc"))DP.sort[t]={k,dir:d}});
  if(has("hm"))DP.hmetric=u.get("hm");if(has("hs"))DP.hscope=u.get("hs");
}
const nm=n=>esc(n).replace(/'/g,"");
function attention(name){const p=DIAG&&DIAG.projects.find(x=>x.name===name);return !!p&&(p.health.stalled||p.flow.blocked>0||(p.write_problems||[]).length>0)}
function dvisible(name,running){
  if(DP.q&&!name.toLowerCase().includes(DP.q.toLowerCase()))return false;
  return DP.f==="all"||(DP.f==="running"&&running)||(DP.f==="idle"&&!running)||(DP.f==="attention"&&attention(name));
}
function dtoolbar(){
  $("#head").innerHTML=`<div class="top"><div><h1>Dashboard</h1><div class="idea">What the local AI is doing right now. Click a section title to collapse it, a column to sort, an agent name to see its live reasoning.</div></div></div>
  <div class="dtools"><input id="dq" placeholder="Filter projects…" value="${esc(DP.q)}" oninput="DP.q=this.value;dsave();paintDash()">
  <div class="chips">${DFILTERS.map(([k,l])=>`<span class="chip ${DP.f===k?"on":""}" onclick="DP.f='${k}';dsave();dtoolbar();paintDash()">${l}</span>`).join("")}</div>
  <span class="grow"></span><button class="small" id="dpause" onclick="dpause()">${DP.paused?"▶ Resume live updates":"⏸ Pause updates"}</button>
  <button class="small" onclick="dall(true)">Collapse all</button><button class="small" onclick="dall(false)">Expand all</button>
  <details class="dmenu"><summary>View ▾</summary><div class="dpop"><b>Sections</b>${Object.entries(DSEC).map(([k,l])=>`<label><input type="checkbox" ${DP.hide[k]?"":"checked"} onchange="DP.hide['${k}']=!this.checked;dsave();paintDash()"> ${l}</label>`).join("")}
  <b>Figures</b>${Object.entries(DCARDS).map(([k,l])=>`<label><input type="checkbox" ${DP.cards[k]===false?"":"checked"} onchange="DP.cards['${k}']=this.checked;dsave();paintDash()"> ${l}</label>`).join("")}
  <b>Notifications</b><label><input type="checkbox" ${DP.notify?"checked":""} onchange="dnotifyToggle(this)"> Browser notification when a task finishes, an agent stops or a new alert appears</label>
  <button class="small" onclick="dreset()">Reset layout</button></div></details></div>`;
}
function dpause(){DP.paused=!DP.paused;dsave();$("#dpause").textContent=DP.paused?"▶ Resume live updates":"⏸ Pause updates";if(!DP.paused)renderDash(true).catch(()=>{})}
function dall(c){Object.keys(DSEC).forEach(k=>DP.collapsed[k]=c);dsave();paintDash()}
function dreset(){DP=DDEF();dsave();dtoolbar();paintDash()}
function dtoggle(id){DP.collapsed[id]=!DP.collapsed[id];dsave();paintDash()}
function dmove(id,dir){const o=DP.order,i=o.indexOf(id),j=i+dir;if(j<0||j>=o.length)return;[o[i],o[j]]=[o[j],o[i]];dsave();paintDash()}
function dsort(t,k){const s=DP.sort[t];DP.sort[t]=s&&s.k===k?(s.dir==="asc"?{k,dir:"desc"}:null):{k,dir:"asc"};if(!DP.sort[t])delete DP.sort[t];dsave();paintDash()}
function dexport(t){
  const T=DT[t];if(!T)return;const q=v=>`"${String(v??"").replace(/"/g,'""')}"`;
  const csv=[T.cols.map(c=>q(c.l)).join(","),...T.rows.map(r=>T.cols.map(c=>q(c.v(r))).join(","))].join("\n");
  const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));a.download=`codefactory-${t}.csv`;a.click();
}
function dsec(id,body,summary,tid){
  const c=DP.collapsed[id];
  return `<div class="panel dsec"><div class="dsh"><span class="dtitle" onclick="dtoggle('${id}')"><span class="chev">${c?"▸":"▾"}</span> ${DSEC[id]}</span><span class="hint dsum">${summary||""}</span><span class="grow"></span>${tid&&!c?`<button class="small" title="Download as CSV" onclick="dexport('${tid}')">CSV</button>`:""}<button class="small" title="Move up" onclick="dmove('${id}',-1)">▲</button><button class="small" title="Move down" onclick="dmove('${id}',1)">▼</button></div>${c?"":`<div class="dsb">${body}</div>`}</div>`;
}
function dtable(id,cols,rows,empty){
  const s=DP.sort[id],c0=s&&cols.find(c=>c.k===s.k);
  if(c0)rows=[...rows].sort((a,b)=>{const x=c0.v(a),y=c0.v(b),nx=x==null||x==="",ny=y==null||y==="";
    if(nx||ny)return nx&&ny?0:nx?1:-1;return(typeof x==="number"&&typeof y==="number"?x-y:String(x).localeCompare(String(y)))*(s.dir==="desc"?-1:1)});
  DT[id]={cols,rows};
  const head=cols.map(c=>`<th class="sortable" title="${esc(c.t||"Sort by this column")}" onclick="dsort('${id}','${c.k}')">${c.l}${c0===c?(s.dir==="desc"?" ▾":" ▴"):""}</th>`).join("");
  return `<div class="tblwrap dcards"><table><tr>${head}</tr>${rows.map(r=>`<tr>${cols.map(c=>`<td data-l="${esc(c.l)}">${c.h(r)}</td>`).join("")}</tr>`).join("")||`<tr><td colspan="${cols.length}" class="hint">${empty}</td></tr>`}</table></div>`;
}
const sub=t=>`<br><span class="hint" style="margin:0">${t}</span>`;
function openReason(n,tool){if(tool){showTool();toolTab("reason");return}select(n);setTab("reasoning")}
const nameCell=p=>`<b><a class="plink" title="Open this agent's live reasoning" onclick="openReason('${nm(p.name)}',${p.tool?"true":"false"})">${esc(p.name)}</a></b>`;
function sectionAttention(d){
  const D=DIAG,s=D.system,busy=DH.busy.length?Math.round(100*DH.busy.filter(x=>x).length/DH.busy.length):null;
  const q=DP.q.toLowerCase(),all=D.findings||[],list=all.filter(f=>!q||f.text.toLowerCase().includes(q));
  const fnd=list.map(f=>`<div class="row" style="margin:4px 0"><span class="pill ${f.level==="bad"?"bad":f.level==="warn"?"warn":"info"}">${esc(f.level)}</span> ${esc(f.text)}</div>`).join("")||`<div class="hint">${all.length?"No finding matches the filter.":"Nothing needs attention."}</div>`;
  const gpuNote=busy!=null&&d.agents_running?`<div class="row" style="margin:4px 0"><span class="pill ${busy<40?"warn":"ok"}">GPU</span> busy ${busy}% of the last ${Math.round(DH.busy.length*1.5/60*10)/10} min while agents run${busy<40?": it waits a lot (CPU offload, model reloads, tests). Run <code>make doctor</code>.":"."}</div>`:"";
  const sys=[["CPU load",s.load1==null?"–":s.load1+" / "+s.cpus+" cores"],["RAM free",s.ram_free_mb==null?"–":(s.ram_free_mb/1024).toFixed(1)+" / "+(s.ram_total_mb/1024).toFixed(0)+" GB"],["Disk free",s.disk_free_gb+" GB"]].map(([k,v])=>`<span class="pill info">${k}: ${v}</span>`).join(" ");
  const T=D.tool||{},tool=T.proposals?`<span class="pill info">Self-improve: ${T.kept}/${T.proposals} steps kept (${pct(T.success_rate)}), ${T.repaired} saved by repair</span>`:"";
  const n=all.filter(f=>f.level!=="info").length;
  return [`${gpuNote}${fnd}<div style="margin-top:8px">${sys} ${tool}</div>`,n?`${n} issue${n===1?"":"s"}`:"all clear"];
}
function sectionEstimates(){
  const D=DIAG,basis={similar:"similar tasks",type:"same type",all:"all tasks",none:"no data yet"};
  const ps=D.projects.filter(p=>dvisible(p.name,(p.forecast||{}).running!=null));
  const est=ps.map(p=>{const F=p.forecast||{items:[]},r=F.running;
    const run=r?`<div class="row" style="margin:4px 0"><span class="pill ${r.overdue?"warn":"info"}">running</span> ${esc(r.title)}: ${dur(r.elapsed_s)} elapsed${r.estimate_s?`, estimate ${dur(r.estimate_s)}${r.left_s!=null?` (~${dur(r.left_s)} left)`:""}`:""}</div>`:"";
    const list=F.items.filter(i=>i.status==="pending").slice(0,5).map(i=>`<tr><td>${esc(i.title)}</td><td>${i.seconds?"~"+dur(i.seconds)+` <span class="hint" style="margin:0">(${dur(i.low)}–${dur(i.high)})</span>`:"–"}</td><td class="hint">${basis[i.basis]}${i.n?`, ${i.n}`:""}</td></tr>`).join("");
    return `<div style="margin:6px 0"><b>${esc(p.name)}</b>: ${F.open} open, queue ~${F.known?dur(F.total_s):"–"}${F.known<F.open?` (${F.open-F.known} without data)`:""}, learned from ${p.durations||0} finished tasks${run}${list?`<div class="tblwrap"><table><tr><th>Next tasks</th><th>Estimate</th><th>Based on</th></tr>${list}</table></div>`:""}</div>`}).join("");
  const TD=(D.tool||{}).durations||{};
  const toolEst=TD.n&&(!DP.q||"self-improve".includes(DP.q.toLowerCase()))?`<div style="margin:6px 0"><b>Self-improve</b>: a step takes ~${dur(TD.median_s)} (${dur(TD.low)}–${dur(TD.high)}, ${TD.n} steps)</div>`:"";
  const open=D.projects.reduce((a,p)=>a+((p.forecast||{}).open||0),0),tot=D.projects.reduce((a,p)=>a+((p.forecast||{}).known?(p.forecast||{}).total_s:0),0);
  return [`<div class="hint">Calculated from the durations of comparable finished tasks, stored per project.</div>${est||`<div class="hint">No project matches the filter.</div>`}${toolEst}`,`${open} open task${open===1?"":"s"}${tot?`, queue ~${dur(tot)}`:""}`];
}
const HMETRICS={tokens:["Tokens",d=>d.tokens,"#6cb6ff"],tasks:["Finished tasks",d=>d.done,"#4ade80"],failed:["Failed attempts",d=>d.failed,"#f87171"],gpu:["GPU hours",d=>d.gpu_hours,"#c4b5fd"],calls:["Model calls",d=>d.calls,"#fbbf24"]};
let HIST=null,histAt=0;
async function loadHist(){if(Date.now()-histAt<15000)return;histAt=Date.now();try{HIST=await api("/api/history");paintDash()}catch(e){}}
function hbars(rows,fn,color){
  const vals=rows.map(fn),max=Math.max(...vals,0),W=720,H=120,bw=W/rows.length;
  if(!max)return `<div class="hint">No activity recorded in the last ${rows.length} days yet.</div>`;
  const bars=rows.map((r,i)=>{const v=vals[i],h=Math.max(v?2:0,Math.round(H*v/max));return `<g><title>${r.day}: ${v}</title><rect x="${(i*bw+1).toFixed(1)}" y="${H-h}" width="${(bw-2).toFixed(1)}" height="${h}" rx="2" fill="${color}" opacity=".85"/></g>`}).join("");
  return `<svg viewBox="0 0 ${W} ${H+18}" style="width:100%;height:auto;max-height:190px"><text x="0" y="10" fill="#8b949e" font-size="10">max ${nfmt(max)}</text><g transform="translate(0,16)">${bars}</g></svg><div class="row hint" style="justify-content:space-between;margin:0"><span>${rows[0].day}</span><span>${rows[rows.length-1].day}</span></div>`;
}
function sectionHistory(){
  if(!HIST)return [`<div class="hint">Loading history…</div>`,""];
  const names=Object.keys(HIST.projects),scope=DP.hscope==="total"||DP.hscope==="tool"||HIST.projects[DP.hscope]?DP.hscope:"total";
  const rows=scope==="total"?HIST.total:scope==="tool"?HIST.tool:HIST.projects[scope];
  const [label,fn,color]=HMETRICS[DP.hmetric]||HMETRICS.tokens;
  const week=rows.slice(-7).reduce((a,r)=>a+fn(r),0),prev=rows.slice(-14,-7).reduce((a,r)=>a+fn(r),0);
  const trend=prev?` (${week>=prev?"+":""}${Math.round(100*(week-prev)/prev)}% vs. the week before)`:"";
  const ctl=`<div class="row" style="gap:8px;margin:0 0 8px"><select onchange="DP.hmetric=this.value;dsave();paintDash()" style="width:auto">${Object.entries(HMETRICS).map(([k,v])=>`<option value="${k}" ${DP.hmetric===k?"selected":""}>${v[0]}</option>`).join("")}</select>
    <select onchange="DP.hscope=this.value;dsave();paintDash()" style="width:auto"><option value="total" ${scope==="total"?"selected":""}>All projects</option><option value="tool" ${scope==="tool"?"selected":""}>Self-improve</option>${names.map(n=>`<option ${scope===n?"selected":""}>${esc(n)}</option>`).join("")}</select></div>`;
  return [ctl+hbars(rows,fn,color),`${label}: ${nfmt(Math.round(week*100)/100)} in the last 7 days${trend}`];
}
function sectionDiag(){
  const cols=[
    {k:"p",l:"Project",v:p=>p.name,h:p=>`<b><a class="plink" title="Open this project" onclick="select('${nm(p.name)}')">${esc(p.name)}</a></b>${p.health.stalled?` <span class="pill bad">stalled</span>`:""}`},
    {k:"tasks",l:"Tasks",t:"done / all tasks",v:p=>p.flow.done,h:p=>`${p.flow.done}/${p.flow.total}${p.flow.blocked?` <span class="pill warn">${p.flow.blocked} blocked</span>`:""}`},
    {k:"first",l:"First try",t:"tasks that passed on the first attempt",v:p=>p.flow.first_try_rate,h:p=>pct(p.flow.first_try_rate)},
    {k:"att",l:"Attempts / task",v:p=>p.flow.avg_attempts,h:p=>p.flow.avg_attempts??"–"},
    {k:"fail",l:"Failed attempts",v:p=>p.flow.failed_attempts,h:p=>`${p.flow.failed_attempts}${Object.keys(p.flow.fail_causes).length?sub(esc(Object.entries(p.flow.fail_causes).map(([k,v])=>k+" "+v).join(", "))):""}`},
    {k:"rev",l:"Review",t:"average review score",v:p=>p.flow.avg_review_score,h:p=>p.flow.avg_review_score??"–"},
    {k:"tph",l:"Tasks / h",v:p=>p.flow.tasks_per_hour,h:p=>p.flow.tasks_per_hour??"–"},
    {k:"call",l:"Call time",t:"average model call time",v:p=>p.models.avg_call_s,h:p=>`${p.models.avg_call_s==null?"–":p.models.avg_call_s+"s"}${p.models.slowest[0]?sub(`slowest: ${esc(p.models.slowest[0].tag)} ${p.models.slowest[0].avg_s}s`):""}`},
    {k:"reas",l:"Reasoning",t:"share of reasoning in all model output",v:p=>p.models.reasoning_share,h:p=>pct(p.models.reasoning_share)},
    {k:"fr",l:"Failure rate",t:"failed attempts / all attempts",v:p=>(p.quality||{}).fail_rate,h:p=>{const q=p.quality||{};return `${pct(q.fail_rate)}${q.flaky||q.stalled?sub(`${q.flaky} flaky · ${q.stalled} stalled`):""}`}},
    {k:"rr",l:"Retry rate",t:"finished tasks that needed more than one attempt",v:p=>(p.quality||{}).retry_rate,h:p=>pct((p.quality||{}).retry_rate)},
    {k:"gh",l:"GPU hours",t:"time the model spent generating",v:p=>(p.quality||{}).gpu_hours,h:p=>((p.quality||{}).gpu_hours??"–")+" h"},
    {k:"loc",l:"Files / lines",v:p=>(p.repo||{}).loc,h:p=>{const r=p.repo||{};return `${r.files??"–"} / ${r.loc??"–"}${sub(`${r.tests??"–"} test files · ${r.commits??"–"} commits`)}`}},
    {k:"idle",l:"Idle",t:"time since last activity",v:p=>p.health.idle_s,h:p=>`${dur(p.health.idle_s)}${p.health.log_errors?sub(p.health.log_errors+" log errors"):""}`}];
  const live=n=>(DLAST.projects.find(x=>x.name===n)||{}).running;
  const rows=DIAG.projects.filter(p=>dvisible(p.name,!!live(p.name)));
  return [dtable("diag",cols,rows,"No project matches the filter."),`${rows.length} project${rows.length===1?"":"s"}`];
}
const dq=n=>{if(!DIAG)return null;const p=n==="Self-improve"?{quality:(DIAG.tool||{}).quality}:DIAG.projects.find(x=>x.name===n);return p&&p.quality};
function sectionProjects(d){
  const cols=[
    {k:"p",l:"Project",v:p=>p.name,h:p=>`${nameCell(p)} ${p.priority==="side"?`<span class="pill info">side</span>`:""}`},
    {k:"st",l:"State",v:p=>p.running?(p.live.active?"generating":"working"):"idle",h:p=>!p.running?`<span class="pill">idle</span>`:p.live.active?`<span class="pill ok">${p.live.streaming?"generating":"working"}</span>`:`<span class="pill info">${p.state?esc(p.state):"running"}</span>`},
    {k:"call",l:"Current call",v:p=>p.live.active?(p.live.role||p.live.tag):"",h:p=>p.live.active?esc(p.live.role||p.live.tag)+sub(esc(p.live.model||"")):"–"},
    {k:"tps",l:"tok/s",v:p=>p.live.tps||0,h:p=>p.live.tps?(p.live.estimated?"~":"")+p.live.tps:"–"},
    {k:"tok",l:"Tokens",t:"tokens in the current call",v:p=>p.live.active?p.live.tokens:0,h:p=>p.live.active?nfmt(p.live.tokens):"–"},
    {k:"chars",l:"Chars",v:p=>p.live.active?p.live.chars:0,h:p=>p.live.active?nfmt(p.live.chars):"–"},
    {k:"aider",l:"Aider output",v:p=>p.coder_chars||0,h:p=>p.coder_chars?nfmt(p.coder_chars):"–"},
    {k:"total",l:"Total tokens",v:p=>p.tokens,h:p=>nfmt(p.tokens)},
    {k:"tpm",l:"Tokens / min",t:"average over the time the agent worked",v:p=>(p.perf||{}).tokens_per_min,h:p=>(p.perf||{}).tokens_per_min!=null?nfmt(p.perf.tokens_per_min):"–"},
    {k:"wps",l:"Words / s",v:p=>(p.perf||{}).wps,h:p=>(p.perf||{}).wps??"–"},
    {k:"gpu",l:"GPU share",t:"share of working time the model was generating",v:p=>(p.perf||{}).gpu_share,h:p=>pct((p.perf||{}).gpu_share)},
    {k:"avg",l:"Avg call",v:p=>(p.perf||{}).avg_call_s,h:p=>(p.perf||{}).avg_call_s!=null?dur(p.perf.avg_call_s):"–"},
    {k:"fr",l:"Failure rate",t:"failed attempts / all attempts",v:p=>(dq(p.name)||{}).fail_rate,h:p=>pct((dq(p.name)||{}).fail_rate)},
    {k:"gh",l:"GPU hours",v:p=>(dq(p.name)||{}).gpu_hours,h:p=>(dq(p.name)||{}).gpu_hours!=null?dq(p.name).gpu_hours+" h":"–"}];
  const rows=d.projects.filter(p=>dvisible(p.name,p.running));
  return [dtable("projects",cols,rows,"No project matches the filter."),`${rows.filter(p=>p.running).length} running of ${rows.length}`];
}
function sectionLive(d){
  const g=d.gpu,t=d.totals,card=(id,k,v,sb,ex)=>DP.cards[id]===false?"":`<div class="dcard"><div class="k">${k}</div><div class="v">${v}</div><div class="sub">${sb||""}</div>${ex||""}</div>`;
  const ps=d.projects,wall=ps.reduce((a,p)=>a+((p.perf||{}).wall_minutes||0),0),tk=ps.reduce((a,p)=>a+((p.perf||{}).tokens||0),0),fn=(DIAG&&DIAG.findings||[]).filter(f=>f.level!=="info").length;
  return `<div class="dash-cards" style="margin:0">`+[
    card("agents","Agents running",(d.agents_running?`<span class="pulse"></span>`:"")+d.agents_running,`${d.calls_active} model call${d.calls_active===1?"":"s"} active`),
    card("tps","Tokens / s",`${d.tps}`,`${nfmt(d.live_tokens)} tokens in current calls`,spark(DH.tps,0)),
    card("tpm","Tokens / min (avg)",wall>=0.5?nfmt(tk/wall):"–","over the time agents worked"),
    card("alerts","Alerts",`<span style="color:var(--${fn?"warn":"ok"},inherit)">${fn}</span>`,fn?"see Needs attention":"all clear"),
    card("chars","Characters now",nfmt(d.live_chars),"generated in current calls"),
    g?card("gpu","GPU",`${g.util}<small>%</small>`,`${esc(g.name)} · ${g.temp}°C`,spark(DH.gpu,100,"gpu")):card("gpu","GPU","n/a","nvidia-smi not available"),
    g?card("vram","VRAM",`${(g.mem_used/1024).toFixed(1)}<small> / ${(g.mem_total/1024).toFixed(0)} GB</small>`,"video memory used",spark(DH.vram,100,"gpu")):"",
    card("models","Loaded models",d.models.length,d.models.map(m=>esc(m.name)+" "+m.vram_gb+"GB").join(", ")||"none in memory"),
    card("today","Today",nfmt(t.day_tokens)+"<small> tokens</small>",`${t.day_calls} calls · ${nfmt(t.day_chars)} chars`),
    card("all","All time",nfmt(t.tokens)+"<small> tokens</small>",`${t.calls} calls · ${nfmt(t.chars)} chars`)].join("")+`</div>`;
}
let DPREV=null;
async function dnotifyToggle(el){
  if(!el.checked){DP.notify=false;dsave();return}
  if(!("Notification"in window)){el.checked=false;toast("This browser has no notifications",true);return}
  const r=await Notification.requestPermission();DP.notify=r==="granted";el.checked=DP.notify;dsave();
  if(!DP.notify)toast("Notifications were not allowed in the browser",true);
}
function dnotify(d){
  const cur={running:Object.fromEntries(d.projects.map(p=>[p.name,p.running])),alerts:(DIAG&&DIAG.findings||[]).filter(f=>f.level!=="info").length,
    done:Object.fromEntries(((DIAG&&DIAG.projects)||[]).map(p=>[p.name,p.flow.done]))};
  const prev=DPREV;DPREV=cur;
  if(!prev||!DP.notify||!("Notification"in window)||Notification.permission!=="granted")return;
  const say=t=>new Notification("CodeFactory",{body:t,tag:t});
  for(const n in cur.running)if(prev.running[n]&&!cur.running[n])say(`${n}: the agent stopped`);
  for(const n in cur.done)if(prev.done[n]!=null&&cur.done[n]>prev.done[n])say(`${n}: ${cur.done[n]-prev.done[n]} task(s) finished`);
  if(cur.alerts>prev.alerts)say(`${cur.alerts-prev.alerts} new alert(s) on the dashboard`);
}
function paintDash(){
  if(view!=="dash"||!DLAST)return;
  const d=DLAST,sel_=window.getSelection&&String(window.getSelection());
  if(sel_&&$("#body").contains(window.getSelection().anchorNode))return;  // do not destroy a text selection
  const parts={live:[sectionLive(d),""],attention:DIAG?sectionAttention(d):[`<div class="hint">Loading diagnostics…</div>`,""],projects:sectionProjects(d),
    history:sectionHistory(),estimates:DIAG?sectionEstimates():[`<div class="hint">Loading…</div>`,""],diag:DIAG?sectionDiag():[`<div class="hint">Loading…</div>`,""]};
  const tid={projects:"projects",diag:"diag"};
  const keepTop=$("#main").scrollTop;
  $("#body").innerHTML=DP.order.filter(k=>!DP.hide[k]).map(k=>dsec(k,parts[k][0],parts[k][1],tid[k])).join("")||`<div class="hint">All sections are hidden. Use View ▾ to show them.</div>`;
  $("#main").scrollTop=keepTop;
}
let DSSE=null,DSSEAt=0;
function dstream(){
  if(DSSE||!window.EventSource)return;
  DSSE=new EventSource("/api/dashboard/stream");
  DSSE.onmessage=e=>{DSSEAt=Date.now();if(!DP.paused&&view==="dash")dapply(JSON.parse(e.data)).catch(()=>{})};
  DSSE.onerror=()=>{DSSE.close();DSSE=null;DSSEAt=0;setTimeout(()=>{if(view==="dash")dstream()},5000)};
}
function dstop(){if(DSSE){DSSE.close();DSSE=null;DSSEAt=0}}
function showDash(){
  closeSb();view="dash";tab="dash";dirty=false;extra=null;lastBody=lastHead=lastTabs="";renderList();
  $("#now").innerHTML="";$("#now").className="";$("#tabs").innerHTML="";$("#body").dataset.tab="dash";
  dfromhash();dtoolbar();dhash();refresh();loadHist();dstream();
}
async function dapply(d){
  if(view!=="dash")return;
  await loadDiag();loadHist();if(view!=="dash")return;
  if(d.agents_running&&d.gpu){DH.busy.push(d.gpu.util>=40);if(DH.busy.length>120)DH.busy.shift()}
  DH.tps.push(d.tps);if(DH.tps.length>90)DH.tps.shift();
  if(d.gpu){DH.gpu.push(d.gpu.util);DH.vram.push(d.gpu.mem_used/d.gpu.mem_total*100);if(DH.gpu.length>90){DH.gpu.shift();DH.vram.shift()}}
  DLAST=d;dnotify(d);paintDash();
}
async function renderDash(force){
  if(DP.paused&&!force)return;
  if(!force&&DSSE&&Date.now()-DSSEAt<5000)return;  // the live stream is delivering; polling is only the fallback
  await dapply(await api("/api/dashboard"));
}
setInterval(()=>{if(view!=="dash")dstop();else renderDash().catch(()=>{})},1500);
