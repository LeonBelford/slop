/* Graph tools: edit mode (link, merge, split), plan proposal, templates, replay timeline, time formatting. */
const gtEta=s=>!s?"-":s<90?Math.round(s)+" s":s<5400?Math.round(s/60)+" min":(s/3600).toFixed(1)+" h";
function gtSummary(d,comp){return(d.graphs||[]).find(g=>g.ids[0]===comp[0])}
function gtCritical(d){const s=new Set(),e=new Set();(d.graphs||[]).forEach(g=>{g.critical.forEach((id,k)=>{s.add(id);if(k)e.add(g.critical[k-1]+">"+id)})});return{nodes:s,edges:e}}
function gtBandLabel(b,g){
  if(!g)return `Graph ${b.n} · ${b.done}/${b.total} done`;
  const pct=g.total?Math.round(100*g.done/g.total):0;
  return `Graph ${b.n} · ${esc(g.goal.length>38?g.goal.slice(0,37)+"…":g.goal)} · ${g.done}/${g.total} (${pct}%)${g.open?` · ~${gtEta(g.remaining_s)} left`:" · finished"}${g.blocked?` · ${g.blocked} blocked`:""}`;
}
function gtBar(b,g){
  if(!g||!g.total)return "";
  return `<rect x="14" y="${b.y+20}" width="160" height="3" rx="1.5" fill="#ffffff14"/><rect x="14" y="${b.y+20}" width="${Math.round(160*g.done/g.total)}" height="3" rx="1.5" fill="#5fb36b"/>`;
}
function gtTools(i){
  const m=tgState.mode,open=i&&["pending","blocked"].includes(i.status);
  if(m)return `<span class="pill info">${m==="link"?`Click the task ${esc(i.id)} has to wait for`:`Click the open task to merge ${esc(i.id)} with`}</span> <button class="small" onclick="gtMode(null)">Cancel</button>`;
  if(!open)return "";
  return `<button class="small" onclick="gtMode('link')" title="Make this task wait for another one">Needs…</button> <button class="small" onclick="gtMode('merge')" title="Merge two open tasks into one">Merge with…</button> <button class="small" onclick="gtSplit('${esc(i.id)}')" title="Split into 2-4 steps">Split…</button> `;
}
function gtMode(m){tgState.mode=m;tgInfo()}
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
function templateBar(d){
  const t=d.templates||[];if(!t.length)return "";
  return `<span class="sp"></span><select id="tplSel" class="small">${t.map(x=>`<option value="${esc(x.id)}">${esc(x.label)} (${x.steps} steps)</option>`).join("")}</select> <button class="small" onclick="gtTemplate()" title="Add a ready-made task graph for a typical change">+ Graph from template</button>`;
}
async function gtTemplate(){
  const name=prompt("What is it about (e.g. the endpoint or screen name)?");if(!name||!name.trim())return;
  await gtAct("graph-template",{template:$("#tplSel").value,name:name.trim()},"Graph added");
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
