/* Graph tools: edit mode (link, merge, split), plan proposal, custom graphs, replay timeline, time formatting. */
const gtEta=s=>!s?"-":s<90?Math.round(s)+" s":s<5400?Math.round(s/60)+" min":(s/3600).toFixed(1)+" h";
function gtCritical(d){const s=new Set(),e=new Set();(d.graphs||[]).forEach(g=>{g.critical.forEach((id,k)=>{s.add(id);if(k)e.add(g.critical[k-1]+">"+id)})});return{nodes:s,edges:e}}
function gtTools(i){
  const m=tgState.mode,open=i&&["pending","blocked"].includes(i.status);
  if(m)return `<span class="pill info">${m==="link"?`Click the task ${esc(i.id)} has to wait for`:`Click the open task to merge ${esc(i.id)} with`}</span> <button class="small" onclick="gtMode(null)">Cancel</button>`;
  if(!open)return "";
  return `<button class="small" onclick="gtMode('link')" title="Make this task wait for another one">Needs…</button> <button class="small" onclick="gtMode('merge')" title="Merge two open tasks into one">Merge with…</button> <button class="small" onclick="gtSplit('${esc(i.id)}')" title="Split into 2-4 steps">Split…</button> `;
}
function gtMode(m){tgState.mode=m;pgInfo()}
async function gtAct(action,body,msg){const r=await act(action,body,msg);lastBody="";return r}
async function gtSplit(id){
  const v=prompt("Step titles, separated by ';' (2 to 4 steps):");if(!v)return;
  const steps=v.split(";").map(s=>s.trim()).filter(Boolean);
  if(steps.length<2){toast("At least two steps",true);return}
  await gtAct("graph-split",{id,steps},"Task split into steps");tgState.sel=null;
}
async function gtEdge(a,b){
  if(!confirm(`Remove the dependency: ${b} no longer waits for ${a}?`))return;
  await gtAct("graph-unlink",{id:b,needs:a},"Link removed");
}
/* the click on a node in an edit mode (called by the graph click handler); true = handled */
function gtNodeClick(id){
  const m=tgState.mode,sel=tgState.sel;if(!m||!sel||id===sel)return false;
  tgState.mode=null;
  if(m==="link")gtAct("graph-link",{id:sel,needs:id},"Link added");
  else gtAct("graph-merge",{ids:[sel,id]},"Tasks merged");
  return true;
}
let cgDraft=null;
function templateBar(){
  return `<div class="tg-grp"><button class="small" onclick="cgOpen()" title="Describe a graph yourself: the AI builds it out at the next re-plan, you can add your own steps too">+ Add custom graph</button></div>`;
}
function cgOpen(){cgDraft={name:"",goal:"",steps:""};lastBody="";renderProject()}
function cgSet(k,v){if(cgDraft)cgDraft[k]=v}
async function cgSave(){
  if(!cgDraft)return;
  const r=await gtAct("graph-custom",cgDraft,"Graph added: the next re-plan builds it out");
  if(r){cgDraft=null;lastBody="";renderProject()}
}
function customForm(){
  const c=cgDraft;if(!c)return "";
  return `<div class="tg-form" style="margin-bottom:12px"><b>New custom graph</b>
    <input class="small" placeholder="Name, e.g. Full text search" value="${esc(c.name)}" oninput="cgSet('name',this.value)">
    <textarea rows="3" placeholder="What should it do? The AI turns this into steps at the next re-plan" oninput="cgSet('goal',this.value)">${esc(c.goal)}</textarea>
    <textarea rows="4" placeholder="Your own steps, one per line (optional, they follow each other; the AI adds what is missing)" oninput="cgSet('steps',this.value)">${esc(c.steps)}</textarea>
    <div class="tg-row"><button class="small primary" onclick="cgSave()">Add graph</button><button class="small" onclick="cgDraft=null;lastBody='';renderProject()">Cancel</button></div></div>`;
}
function gtStats(d,m){
  const all=m.all,done=all.filter(i=>i.status==="done").length,open=all.length-done,blocked=all.filter(i=>i.status==="blocked").length,g=d.graphs||[];
  const left=g.reduce((a,x)=>a+(x.remaining_s||0),0),crit=g.reduce((a,x)=>Math.max(a,x.critical_s||0),0),pct=all.length?Math.round(100*done/all.length):0;
  const pill=(n,l,c)=>`<span class="tg-pill${c?" "+c:""}"><b>${n}</b> ${l}</span>`;
  return `<div class="tg-stats">${pill(pct+"%","done")}${pill(open,"open")}${blocked?pill(blocked,"blocked","bad"):""}${pill(m.edges.length,"links")}${left?pill("~"+gtEta(left),"left","warn"):""}${crit?pill("~"+gtEta(crit),"critical path"):""}</div>`;
}
function proposalCard(d){
  const p=d.proposal;if(!p)return "";
  return `<div class="panel" style="border-color:#d6a23c"><h3>Plan proposal <span class="meta">${esc(p.reason||"")} · nothing is applied until you confirm</span></h3>
    <p class="hint">${esc(p.assessment||"")}</p><ul class="hint" style="margin:6px 0 10px 18px">${(p.lines||[]).map(l=>`<li>${esc(l)}</li>`).join("")}</ul>
    ${d.running?'<span class="hint">Stop the agent to apply it.</span>':`<button class="small primary" onclick="gtAct('plan-apply',{},'Applying the proposal: follow it in the log').then(()=>setTab('log'))">Apply</button> `}<button class="small" onclick="gtAct('plan-reject',{},'Proposal discarded')">Discard</button></div>`;
}
/* replay: the graphs as they were after each review */
let gtHist=null,gtAt=0;
async function gtOpenHistory(){
  try{gtHist=(await api(`/api/project/${encodeURIComponent(sel)}/graph-history`)).history}catch(e){toast(e.message,true);return}
  gtAt=gtHist.length-1;lastBody="";renderProject();
}
function gtCloseHistory(){gtHist=null;lastBody="";renderProject()}
function gtSlide(v){gtAt=+v;lastBody="";renderProject()}
function historyPanel(){
  if(!gtHist)return "";
  if(!gtHist.length)return `<div class="panel"><h3>Graph history</h3><div class="hint">No reviews recorded yet.</div><button class="small" onclick="gtCloseHistory()">Close</button></div>`;
  const s=gtHist[Math.min(gtAt,gtHist.length-1)],items=s.nodes.map(n=>({id:n.id,title:n.t,status:n.s,layer:n.l,depends_on:n.d}));
  const L=tgLayout(items),ids=new Set(items.map(i=>i.id));
  const edges=items.flatMap(i=>(i.depends_on||[]).filter(x=>ids.has(x)).map(x=>{const a=L.pos[x],b=L.pos[i.id],x1=a.x+L.W,y1=a.y+L.H/2,x2=b.x,y2=b.y+L.H/2,m=(x1+x2)/2;return `<path d="M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}" fill="none" stroke="#555" stroke-width="1.3"/>`})).join("");
  const nodes=items.map(i=>{const p=L.pos[i.id];return `<g transform="translate(${p.x},${p.y})"><rect width="${L.W}" height="${L.H}" rx="8" fill="#1c1c1c" stroke="${TG_COLORS[i.status]||"#888"}"/><text x="12" y="19" fill="#aaa" font-size="11">${esc(i.id)} · ${esc(i.status.replace("_"," "))}</text><text x="12" y="36" fill="#eee" font-size="12">${esc(i.title)}</text></g>`}).join("");
  const st=s.stats||{};
  return `<div class="panel"><h3>Graph history <span class="meta">${gtAt+1}/${gtHist.length} · ${esc(s.ts||"")}</span></h3>
    <input type="range" min="0" max="${gtHist.length-1}" value="${gtAt}" oninput="gtSlide(this.value)" style="width:100%">
    <p class="hint">${esc(s.reason||"")}${Object.keys(st).length?" · "+Object.entries(st).filter(([,v])=>v).map(([k,v])=>`${k} ${v}`).join(", "):""}</p>
    <div class="tg-wrap"><svg width="${L.width}" height="${L.height}">${edges}${nodes}</svg></div><button class="small" onclick="gtCloseHistory()">Close</button></div>`;
}

/* AI graph ideas and your own standard processes: two separate panels below the graph */
const PR_PRESETS={
  "Refactoring":{steps:"Find duplicated code and over-long functions\nSimplify and split them without changing behaviour\nClean up names and comments",type:"refactor"},
  "Bug hunt":{steps:"Read the code for bugs and unhandled errors: list them in the notes\nFix the most serious bugs and add a test for each",type:"refactor"},
  "Docs":{steps:"Check README and docs against the code and list what is outdated\nUpdate the docs and examples",type:"refactor"},
  "Optimisation":{steps:"Find optimisation chances: slow paths, repeated work, needless dependencies\nApply the cheap wins that keep behaviour the same\nAdd a guard test for the optimised code",type:"refactor"}};
let prDraft=null;
function prNew(k){const x=PR_PRESETS[k]||{steps:"",type:"refactor"};prDraft={id:"",name:k||"",steps:x.steps,type:x.type,sequential:true,every:0};lastBody="";renderProject()}
function prEdit(id){const p=(detail&&detail.processes||[]).find(x=>x.id===id);if(!p)return;prDraft={...p};lastBody="";renderProject()}
function prSet(k,v){if(prDraft)prDraft[k]=v}
async function prSave(){
  if(!prDraft)return;
  const r=await gtAct("process-save",{...prDraft,every:+prDraft.every||0},"Process saved");
  if(r!==false){prDraft=null;lastBody="";renderProject()}
}
function prForm(){
  const p=prDraft;if(!p)return "";
  return `<div class="tg-form"><input class="small" placeholder="Name, e.g. Weekly cleanup" value="${esc(p.name)}" oninput="prSet('name',this.value)">
    <textarea rows="5" placeholder="One step per line. Optional detail after a colon: Find dead code: unused functions and imports" oninput="prSet('steps',this.value)">${esc(p.steps)}</textarea>
    <div class="tg-row"><label>Type <select class="small" onchange="prSet('type',this.value)">${["refactor","feature","test"].map(t=>`<option${p.type===t?" selected":""}>${t}</option>`).join("")}</select></label>
    <label><input type="checkbox" ${p.sequential?"checked":""} onchange="prSet('sequential',this.checked)"> steps follow each other</label>
    <label>Auto every <input class="small" type="number" min="0" max="500" style="width:64px" value="${+p.every||0}" oninput="prSet('every',this.value)"> merged tasks (0 = only on click)</label></div>
    <div class="tg-row"><button class="small primary" onclick="prSave()">Save</button><button class="small" onclick="prDraft=null;lastBody='';renderProject()">Cancel</button></div></div>`;
}
function ideasPanel(d){
  const ideas=d.graph_ideas||[];
  return `<div class="panel tg-half"><h3>Ideas from the AI <span class="meta">made at every re-plan from your goal and the features built so far</span></h3>${ideas.length?ideas.map(i=>`<div class="tg-card"><div><b>${esc(i.title)}</b><div class="hint">${esc(i.why||"")}</div>
    <ol class="hint tg-steps">${i.steps.map(s=>`<li>${esc(s.title)}</li>`).join("")}</ol></div><div class="tg-act"><button class="small primary" onclick="gtAct('idea-add',{id:'${esc(i.id)}'},'Graph added')">Add as graph</button><button class="small" onclick="gtAct('idea-dismiss',{id:'${esc(i.id)}'},'Dismissed')">Dismiss</button></div></div>`).join(""):'<div class="hint">No ideas yet. They appear after the next re-plan.</div>'}</div>`;
}
function processPanel(d){
  const procs=d.processes||[];
  return `<div class="panel tg-half"><h3>Standard processes <span class="meta">your own routines, separate from the AI ideas</span></h3>${procs.map(p=>`<div class="tg-card"><div><b>${esc(p.name)}</b> <span class="meta">${p.every?`every ${p.every} merged tasks`:"on click"}</span>
    <ol class="hint tg-steps">${p.steps.split("\n").filter(x=>x.trim()).slice(0,6).map(x=>`<li>${esc(x.split(":")[0])}</li>`).join("")}</ol></div><div class="tg-act"><button class="small primary" onclick="gtAct('process-run',{id:'${esc(p.id)}'},'Graph added')">Run now</button><button class="small" onclick="prEdit('${esc(p.id)}')">Edit</button><button class="small" onclick="confirm('Delete this process?')&&gtAct('process-delete',{id:'${esc(p.id)}'},'Deleted')">Delete</button></div></div>`).join("")||'<div class="hint">Nothing yet. Start from an example or write your own.</div>'}
    ${prDraft?prForm():`<div class="tg-row" style="margin-top:8px"><span class="hint">New:</span>${Object.keys(PR_PRESETS).map(k=>`<button class="small" onclick="prNew('${k}')">${k}</button>`).join("")}<button class="small" onclick="prNew('')">Own…</button></div>`}</div>`;
}
function routinePanels(d){return `<div class="tg-two">${ideasPanel(d)}${processPanel(d)}</div>`}
