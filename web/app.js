const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let sel=localStorage.getItem("sel")||null, view="project", tab="overview", list={projects:[],init_jobs:[]}, detail=null, extra=null, dirty=false, langs=[];
let toolTab_="tool",toolBranch="",planId="",taskPrefill="",codeBase="head",codeOpen=new Set(),codeView=null,codeInit=false;
let diffView=null, lastBody="", lastHead="", lastTabs="", openKeys=new Set();
const EDITABLE=["ideas","sources","task","tool","settings","goal","profile","knowledge"];
const GROUPS=[["overview","Overview",["overview"]],["task","Do a task",["task"]],["agent","Agent",["profile","knowledge","goal","ideas","sources","settings"]],["activity","Activity",["log","reasoning","llm"]],["code","Code & test",["changes","testing"]],["customize","Customize",["customize"]]];
const LEAF={profile:"Instructions & capabilities",knowledge:"Knowledge files",goal:"Goal",ideas:"Ideas",sources:"Web sources",settings:"Models & limits",log:"Live log",reasoning:"Reasoning",llm:"AI activity",changes:"File changes",testing:"Test version"};
const ACCENTS=["","#5fb36b","#6fa3d8","#d6a23c","#c678dd","#e06c75","#56b6c2"];
const STAT_KEYS=[["merged","Features merged"],["open","Open tasks"],["done","Done"],["blocked","Blocked"],["iterations","Iterations"],["hours","Runtime"],["tokens","Tokens used"],["tps","Tokens / s"],["wps","Words / s"],["tpm","Tokens / min"],["gpu","GPU"],["avgcall","Avg. call time"]];
let lastLeaf={};
function pref(){try{return {accent:"",dense:false,hide:[],stats:["merged","open","done","blocked","hours","tokens","tps","tpm","gpu"],start:"overview",hideDone:false,showFeatures:true,...JSON.parse(localStorage.getItem("pref:"+sel)||"{}")}}catch(e){return {accent:"",dense:false,hide:[],stats:["merged","open","done","blocked","hours","tokens","tps","tpm","gpu"],start:"overview",hideDone:false,showFeatures:true}}}
function setPref(k,v){const p=pref();p[k]=v;localStorage.setItem("pref:"+sel,JSON.stringify(p));applyPref();lastHead=lastTabs=lastBody="";renderProject()}
function togglePref(k,item){const a=pref()[k].slice(),i=a.indexOf(item);i<0?a.push(item):a.splice(i,1);setPref(k,a)}
function applyPref(){const p=pref();document.documentElement.style.setProperty("--acc",p.accent||"");if(!p.accent)document.documentElement.style.removeProperty("--acc");document.body.classList.toggle("dense",!!p.dense)}
const STEPS=[["plan","Plan"],["implement","Implement"],["build","Build & test"],["e2e","Browser test"],["security","Security"],["review","Review"],["cleanup","Cleanup"],["docs","Docs & memory"],["merge","Merge"]];

async function api(path,body){
  const opt=body===undefined?{}:{method:"POST",headers:{"X-UI":"1","Content-Type":"application/json"},body:JSON.stringify(body)};
  let r;
  try{r=await fetch(path,opt)}catch(e){throw new Error("Cannot reach the UI server. Is `make ui` still running?")}
  const text=await r.text();
  let j;
  try{j=JSON.parse(text)}catch(e){
    throw new Error(`Unexpected response from ${path} (HTTP ${r.status}). Open the UI via the address printed by "make ui" (http://127.0.0.1:8765), not as a file or through a proxy.\n`+text.slice(0,160));
  }
  if(!r.ok) throw new Error(j.error||r.statusText); return j;
}
function toast(msg,err){const t=$("#toast");t.textContent=msg;t.className="toast on"+(err?" err":"");clearTimeout(t._h);t._h=setTimeout(()=>t.className="toast",err?7000:3000)}
async function act(a,body,ok){try{const r=await api(`/api/project/${sel}/${a}`,body||{});if(ok)toast(ok);await refresh();return r}catch(e){toast(e.message,true)}}
const fmtDur=s=>{s=Math.max(0,Math.round(s));return s<60?s+"s":Math.floor(s/60)+"m "+(s%60)+"s"};
const fmtDiff=t=>esc(t).split("\n").map(l=>{const c=l.startsWith("+++")||l.startsWith("---")||l.startsWith("diff ")||l.startsWith("index ")?"meta":l.startsWith("+")?"add":l.startsWith("-")?"del":l.startsWith("@@")?"hunk":"";return c?`<span class="${c}">${l}</span>`:l}).join("\n");

async function forceStop(){if(!confirm('Force stop kills the agent and everything it runs right now. The unfinished task is discarded and retried on the next start. Continue?'))return;try{const r=await api(`/api/project/${sel}/force-stop`,{});toast(r.message||'Force-stopped');await refresh()}catch(e){toast(e.message,true)}}
function statusOf(p){return p.running?(p.stopping?["stop","stopping after task"]:["run","running"]):["","stopped"]}
function renderList(){
  const m=list.model_server;if(m)$("#ms").innerHTML=`<span class="dot ${m.ok?"run":"stop"}" style="display:inline-block"></span> Model server ${m.ok?"online · "+m.models+" models":"offline ("+esc(m.url)+")"}`;
  const jobs=list.init_jobs.map(j=>`<div class="job">${j.running?"Creating":"Failed"}: <b>${esc(j.name)}</b><br><code>${esc((j.log||"").split("\n").slice(-3).join(" "))}</code></div>`).join("");
  $("#plist").innerHTML=jobs+(list.projects.map(p=>{const[c,t]=statusOf(p);
    return `<div class="card ${view==="project"&&p.name===sel?"sel":""}" onclick="select('${esc(p.name)}')"><div class="n"><span class="dot ${c}"></span>${esc(p.name)}${p.priority==="side"?` <span class="pill info" title="Side agent: only uses the GPU while the main agent is idle">side</span>`:""}</div><div class="s">${t} · ${p.merged} features · ${p.backlog.pending||0} open</div></div>`}).join("")||(jobs?"":'<div class="hint" style="padding:10px">No projects yet.</div>'));
}
function select(n){closeSb();view="project";sel=n;localStorage.setItem("sel",n);applyPref();tab=pref().start||"overview";dirty=false;detail=null;extra=null;diffView=null;codeView=null;codeOpen=new Set();codeInit=false;lastBody=lastHead=lastTabs="";renderList();refresh()}
function showTool(){closeSb();view="tool";tab="tool";toolTab_="tool";codeView=null;codeInit=false;codeOpen=new Set();dirty=false;extra=null;diffView=null;lastBody=lastHead=lastTabs="";renderList();refresh()}

/* ---- live status panel (re-rendered every second from the last data) ---- */
function colorLog(t){
  return t.split("\n").map(l=>{
    let c="";
    if(/\] ▶ \[security\]|ecurity/.test(l))c=/block|fail|critical|high/i.test(l)&&!/non-blocking/.test(l)?"lg-err":"lg-sec";
    else if(/Traceback|Error|error|failed|FAIL|red\b|rolled back|discarded|blocked|✖|✘/.test(l))c="lg-err";
    else if(/warn|skipped|revision|retry|waiting|Debug round|not reachable/i.test(l))c="lg-warn";
    else if(/✔|merged|passed|green|OK\b/.test(l))c="lg-ok";
    else if(/\] ▶ /.test(l))c="lg-step";
    return c?`<span class="${c}">${esc(l)}</span>`:esc(l);
  }).join("\n");
}
const REASON_TAGS={impact:["lg-step","Plan"],plan_critique:["lg-warn","Plan check"],security_review:["lg-sec","Security"],
  review:["lg-ok","Review"],reflect:["lg-step","Reflection"],decision:["lg-warn","Decision"],product_review:["lg-sec","Product audit"],
  ideate_critic:["lg-warn","Idea check"],replan:["lg-step","Replan"],tool_impact:["lg-step","Plan"],tool_plan_critique:["lg-warn","Plan check"],
  tool_review:["lg-ok","Review"],tool_files:["lg-step","Pick files"],improve_plan:["lg-step","Plan"]};
let bigPre=new Set(),liveHtml="";
function rv(v){
  if(v===null||v===undefined||v==="")return `<span class="meta">-</span>`;
  if(Array.isArray(v))return v.length?`<ul>${v.map(x=>`<li>${rv(x)}</li>`).join("")}</ul>`:`<span class="meta">-</span>`;
  if(typeof v==="object")return `<dl>${Object.entries(v).map(([k,val])=>`<dt>${esc(k)}</dt><dd>${rv(val)}</dd>`).join("")}</dl>`;
  return mdInline(String(v));
}
const tagInfo=t=>REASON_TAGS[t]||(t==="coder"||/^(code|fix|edit)/.test(t)?["lg-step","Code"]:["lg-step",t]);
const pillCls=c=>c==="lg-err"?"bad":c==="lg-warn"?"warn":c==="lg-sec"?"info":"ok";
function liveBox(label,text,cls){return text?`<div class="stream"><div class="meta">${label}</div><pre data-p="live-${esc(label)}" class="${cls||""} ${bigPre.has("live-"+label)?"big":""}">${esc(text)}<span class="caret"></span></pre></div>`:""}
function reasoningLive(){
  const el=$("#reasonlive");if(!el)return;
  if(view==="tool")return liveInto(el,extra&&extra.act,extra&&extra.act);
  liveInto(el,detail&&detail.activity,extra&&extra.kind==="reasoning"?extra:{});
}
let LS=null;
function liveInto(el,a,x){
  if(!a||!a.running){if(el.innerHTML){el.innerHTML="";el.dataset.sig="";liveHtml=""}return}
  const d=LS||x||{},st=d.stream,t=Date.now()/1000;
  let kind,key,head,boxes=[];
  if(a.llm){
    const [cls,label]=tagInfo(a.llm.tag),got=st&&st.tag===a.llm.tag,s=Math.round(t-a.llm.since);
    kind="llm";key=a.llm.tag;
    head=`Thinking as <b class="${cls}">${esc(label)}</b> (<code>${esc(a.llm.model)}</code>) · ${s}s${!got&&s>=8?" (model may still be loading)":""}`;
    if(got)boxes=[["Reasoning so far",st.thinking,"thinktxt"],["Answer so far",st.answer,""]];
  }else if(a.coder){
    kind="coder";key=a.coder.tag;head=`Aider is writing code for <b class="lg-step">${esc(a.coder.tag)}</b> · ${Math.round(t-a.coder.since)}s`;boxes=[["Aider output",d.coder_live,""]];
  }else if(a.cmd){
    kind="cmd";key=a.cmd.name;head=`Running <b class="lg-warn">${esc(a.cmd.name)}</b>: <code>${esc(a.cmd.cmd)}</code> · ${Math.round(t-a.cmd.since)}s (no model involved, this is your project's own build/tests)`;boxes=[["Output",d.cmd_live,""]];
  }else{if(el.innerHTML){el.innerHTML="";el.dataset.sig="";liveHtml=""}return}
  boxes=boxes.filter(b=>b[1]);
  const sig=kind+"|"+key+"|"+boxes.map(b=>b[0]).join(",");
  const main=$("#main"),h0=main.scrollHeight,top=main.scrollTop;
  if(el.dataset.sig!==sig){
    el.dataset.sig=sig;
    el.innerHTML=`<div class="msg assistant reason-card" style="max-width:100%"><div class="think"><span class="dots"><span></span><span></span><span></span></span><span class="lhead"></span></div>
      ${boxes.map(([label,,cls])=>`<div class="stream"><div class="meta"><span class="live-badge">● live</span> ${esc(label)} <span class="lcount"></span><button class="small jump" onclick="jumpLatest(this)">↓ latest</button></div>
      <pre data-p="live-${esc(label)}" class="${cls} ${bigPre.has("live-"+label)?"big":""}"><span class="lt"></span><span class="caret"></span></pre></div>`).join("")}</div>`;
  }
  el.querySelector(".lhead").innerHTML=head;
  boxes.forEach(([label,text])=>{
    const pre=[...el.querySelectorAll("pre[data-p]")].find(p=>p.dataset.p==="live-"+label);if(!pre)return;
    if(pre._target===undefined){pre._target=text;pre._shown=text.length;pre.querySelector(".lt").textContent=text;pre.scrollTop=pre.scrollHeight}
    else pre._target=text;
    el.querySelectorAll(".lcount")[boxes.findIndex(b=>b[0]===label)].textContent=text.length.toLocaleString()+" chars";
  });
  liveHtml=el.innerHTML;
  if(top>20)main.scrollTop=top+(main.scrollHeight-h0);
}
function jumpLatest(btn){const pre=btn.closest(".stream").querySelector("pre");pre.scrollTop=pre.scrollHeight}
/* typewriter: reveals streamed text smoothly instead of in 0.4 s bursts; never rebuilds the DOM, so selection and scroll survive */
function typeTick(){
  document.querySelectorAll("#reasonlive pre[data-p]").forEach(pre=>{
    const tg=pre._target;if(tg===undefined)return;
    const node=pre.querySelector(".lt");let sh=pre._shown||0;
    const near=pre.scrollHeight-pre.scrollTop-pre.clientHeight<48;
    if(sh>tg.length){node.textContent=tg;sh=tg.length}
    else if(sh<tg.length){
      const n=Math.max(3,Math.ceil((tg.length-sh)/10));
      if(node.firstChild)node.firstChild.appendData(tg.slice(sh,sh+n));else node.textContent=tg.slice(sh,sh+n);
      sh=Math.min(tg.length,sh+n);
    }
    pre._shown=sh;
    if(near)pre.scrollTop=pre.scrollHeight;
    pre.closest(".stream").classList.toggle("away",!near);
  });
  requestAnimationFrame(typeTick);
}
requestAnimationFrame(typeTick);
const bp=(k,cls,html)=>`<pre data-p="${k}" class="${cls} ${bigPre.has(k)?"big":""}">${html}</pre>`;
function thoughtCard(e,i){
  const [cls,label]=tagInfo(e.tag);
  let obj=null;try{obj=JSON.parse(e.answer)}catch(_){}
  const sec=(k,title,body,open)=>body?`<details data-k="th-${e.n}-${k}" ${open?"open":""}><summary>${title}</summary><div class="inner">${body}</div></details>`:"";
  return `<div class="msg assistant reason-card" style="max-width:100%;animation-delay:${Math.min(i,6)*0.04}s">
    <div class="row" style="justify-content:space-between;margin-bottom:6px"><span><span class="pill ${pillCls(cls)}">${esc(label)}</span> <span class="meta">#${e.n} · ${esc(e.role)} · <code>${esc(e.model)}</code>${e.seconds?` · ${e.seconds}s`:""}${e.task?` · task ${esc(e.task)}`:""}</span></span><span class="meta">${esc(e.time)}</span></div>
    ${sec("t","Model reasoning (thinking)",e.thinking?bp(e.n+"t","thinktxt",esc(e.thinking)):"")}
    ${sec("a","Answer",obj?`<div class="fold ${bigPre.has(e.n+"a")?"big":""}" data-p="${e.n}a">${rv(obj)}</div>`:bp(e.n+"a","",esc(e.answer)),true)}
    ${sec("p","What it was asked (prompt)",bp(e.n+"p","",esc(e.prompt)))}</div>`;
}
function reasoningBody(){
  const src=view==="tool"?(extra&&extra.act):(extra&&extra.kind==="reasoning"?extra:null);
  if(!src)return `<div class="hint">Loading…</div>`;
  const cards=src.entries.map(thoughtCard).join("");
  const arch=view!=="tool"&&src.archived?`<p class="hint">${src.archived} older calls are stored compressed in <code>agent/thoughts-archive/</code>.</p>`:"";
  return `${arch}<p class="hint">Every model call in full, live: the reasoning, the answer and the prompt, plus what Aider is writing (newest first). Models that think (deepseek-r1, qwen3, ...) show their reasoning too.</p>
  <div id="reasonlive"></div>
  <div class="reason-list">${cards||'<div class="hint">Nothing recorded yet. Every thought appears here as soon as the agent works.</div>'}</div>`;
}

function buildTree(files){
  const root={d:{},f:[],add:0,del:0,n:0};
  files.forEach(f=>{let node=root;const parts=f.path.split("/");
    parts.slice(0,-1).forEach(p=>{node=node.d[p]||(node.d[p]={d:{},f:[],add:0,del:0,n:0})});
    node.f.push({...f,name:parts[parts.length-1]})});
  const sum=n=>{n.add=n.f.reduce((a,f)=>a+Math.max(f.add,0),0);n.del=n.f.reduce((a,f)=>a+f.del,0);n.n=n.f.filter(f=>f.status).length;
    Object.values(n.d).forEach(c=>{sum(c);n.add+=c.add;n.del+=c.del;n.n+=c.n})};
  sum(root);return root;
}
const jsq=s=>esc(String(s).replace(/\\/g,"\\\\").replace(/'/g,"\\'"));
function treeHtml(node,prefix,depth){
  const dirs=Object.keys(node.d).sort().map(k=>{
    const c=node.d[k],path=prefix+k,open=codeOpen.has(path);
    return `<div class="tr dir" style="padding-left:${depth*14}px" onclick="toggleDir('${jsq(path)}')"><span class="car">${open?"▾":"▸"}</span>📁 ${esc(k)}${c.n?`<span class="tb">${c.n} changed</span>`:""}</div>${open?treeHtml(c,path+"/",depth+1):""}`}).join("");
  const files=node.f.sort((a,b)=>a.name.localeCompare(b.name)).map(f=>{
    const cur=codeView&&codeView.path===f.path;
    const badge=f.status==="A"?`<span class="tb new">new</span>`:f.status==="M"?`<span class="tb mod">+${f.add} −${f.del}</span>`:"";
    return `<div class="tr file ${cur?"cur":""} ${f.status?"chg":""}" style="padding-left:${depth*14+16}px" onclick="openFile('${jsq(f.path)}')">${esc(f.name)}${badge}</div>`}).join("");
  return dirs+files;
}
function toggleDir(p){codeOpen.has(p)?codeOpen.delete(p):codeOpen.add(p);lastBody="";view==="tool"?renderTool():renderProject()}
async function openFile(path){
  try{const r=view==="tool"?await api(`/api/tool/file?branch=${encodeURIComponent(toolBranch)}&path=${encodeURIComponent(path)}`)
      :await api(`/api/project/${encodeURIComponent(sel)}/file?path=${encodeURIComponent(path)}&base=${encodeURIComponent(codeBase)}`);
    codeView=r;lastBody="";view==="tool"?renderTool():renderProject()}catch(e){toast(e.message,true)}
}
function setBase(v){codeBase=v;extra=null;lastBody="";renderProject();fetchExtra().then(()=>{if(codeView)openFile(codeView.path);else renderProject()})}
function fileViewer(){
  if(!codeView)return `<div class="hint" style="padding:12px">Pick a file in the tree to see its code. Green lines are new or changed.</div>`;
  const v=codeView;
  if(v.binary)return `<div class="hint" style="padding:12px">${esc(v.path)}: binary file (${v.size} bytes)</div>`;
  const ch=new Set(v.changed),lines=v.text.split("\n");
  const body=lines.map((l,i)=>`<div class="cl ${ch.has(i+1)?"new":""}"><span class="ln">${i+1}</span><span class="ct">${esc(l)||" "}</span></div>`).join("");
  return `<div class="cvh"><b>${esc(v.path)}</b> <span class="meta">${lines.length} lines · ${v.new_file?"new file":ch.size+" new/changed"}${v.truncated?" · truncated":""}</span></div><div class="code">${body}</div>`;
}
function codePanel(x){
  const root=buildTree(x.tree.files);
  if(view==="tool")return `<div class="panel"><h3>Changes in <code>${esc(toolBranch)}</code> <button class="small" onclick="codeOpen=new Set(${JSON.stringify([...new Set(x.tree.files.flatMap(f=>f.path.split("/").slice(0,-1).map((_,i,a)=>a.slice(0,i+1).join("/"))))])});lastBody='';renderTool()">expand all</button></h3>
    <div class="codewrap"><div class="tree">${x.tree.files.length?treeHtml(root,"",0):'<div class="hint">No changed files.</div>'}</div><div class="viewer">${fileViewer()}</div></div></div>`;
  const bases=[["head","Uncommitted changes"],["last:1","Last commit"],["last:5","Last 5 commits"],["last:20","Last 20 commits"],["last:200","Everything recent"]];
  return `<div class="panel"><h3>Code <select onchange="setBase(this.value)" style="margin-left:10px">${bases.map(([v,l])=>`<option value="${v}" ${v===codeBase?"selected":""}>New since: ${l}</option>`).join("")}</select>
    <button class="small" onclick="codeOpen=new Set(${JSON.stringify([...new Set(x.tree.files.flatMap(f=>f.path.split("/").slice(0,-1).map((_,i,a)=>a.slice(0,i+1).join("/"))))])});lastBody='';renderProject()">expand all</button>
    <button class="small" onclick="codeOpen=new Set();lastBody='';renderProject()">collapse all</button></h3>
    <div class="codewrap"><div class="tree">${x.tree.files.length?treeHtml(root,"",0):'<div class="hint">No files yet.</div>'}</div><div class="viewer">${fileViewer()}</div></div></div>`;
}
function pctBar(pct){
  if(pct==null)return "";
  return `<div class="pctwrap" title="Estimated share of this task done"><div class="pctbar"><div class="pctfill" style="width:${pct}%"></div></div><span class="pcttxt">${pct}%</span></div>`;
}
function microSteps(steps){
  if(!steps||steps.length<2)return "";
  const rows=steps.slice(-8).reverse().map(s=>`<div class="micro">${s.step?`<span class="pill">${esc(s.step)}</span> `:""}${esc(s.phase)}${s.detail?` — <span class="meta">${esc(s.detail)}</span>`:""}</div>`).join("");
  return `<details class="microsteps" open><summary>Sub-steps so far</summary>${rows}</details>`;
}
function stepTimes(list,idx,durations,liveStep,liveSince,t){
  return `<div class="steps">${list.map(([k,l],i)=>{
    const closed=(durations&&durations[k])||0,live=k===liveStep&&liveSince?Math.max(0,t-liveSince):0,total=closed+live;
    const time=(i<=idx&&total>0.5)?`<span class="stime">${fmtDur(total)}</span>`:"";
    return `<div class="step ${i<idx?"done":i===idx?"cur":""}">${l}${time}</div>`}).join("")}</div>`;
}
function renderNow(){
  const box=$("#now");
  if(view==="dash"){box.innerHTML="";box.className="";return}
  if(view==="tool"){
    const a=extra&&extra.act;
    if(!a||!a.running){box.className="now";box.innerHTML=`<div class="what">No self-change running</div>`;return}
    const t=Date.now()/1000,ph=a.phase,idx=ph?TOOL_STEPS.findIndex(s=>s[0]===ph.step):-1;
    const call=a.coder?`<div class="call">Aider (<code>${esc(a.coder.model)}</code>) is editing ${esc(a.coder.files.join(", ")||"files")}, ${fmtDur(t-a.coder.since)}</div>`
      :a.cmd?`<div class="call">Running <b>${esc(a.cmd.name)}</b> (<code>${esc(a.cmd.cmd)}</code>), ${fmtDur(t-a.cmd.since)}</div>`
      :a.llm?`<div class="call">Waiting for the <b>${esc(a.llm.role)}</b> model (<code>${esc(a.llm.model)}</code>) on <b>${esc(a.llm.tag)}</b>, ${fmtDur(t-a.llm.since)}</div>`:"";
    box.className="now live";
    box.innerHTML=`<div class="what">Changing the tool: ${ph?esc(ph.step):"starting"}</div>${pctBar(ph&&ph.pct)}<div class="sub">${ph?esc(ph.detail)+` · ${fmtDur(t-ph.since)} in this step`:""}</div>
      ${stepTimes(TOOL_STEPS,idx,ph&&ph.durations,ph&&ph.step,ph&&ph.since,t)}${microSteps(ph&&ph.steps)}${call}`;
    reasoningLive();return;
  }
  if(view!=="project"||!detail){box.innerHTML="";return}
  const a=detail.activity||{},n=a.now,t=Date.now()/1000;
  if(!a.running){box.className="now";box.innerHTML=`<div class="what">Agent is not running</div><div class="sub">Press Start for autonomous work, or give it one concrete job on the "Do a task" tab.</div>${a.queued_user_tasks&&a.queued_user_tasks.length?`<div class="sub">Queued for you: ${a.queued_user_tasks.map(esc).join("; ")}</div>`:""}${timeline(a.events)}`;return}
  box.className="now live";
  const idx=n?STEPS.findIndex(s=>s[0]===n.step):-1;
  const stepper=idx>=0||(n&&n.task)?stepTimes(STEPS,idx,n&&n.durations,n&&n.step,n&&n.since,t):"";
  const call=a.cmd?`<div class="call">Running <b>${esc(a.cmd.name)}</b> (<code>${esc(a.cmd.cmd)}</code>), ${fmtDur(t-a.cmd.since)}. Output: "Reasoning" tab.</div>`:a.coder?`<div class="call">Aider (code model <code>${esc(a.coder.model)}</code>) is editing ${a.coder.files.length?esc(a.coder.files.join(", ")):"files"} for <b>${esc(a.coder.tag)}</b>, ${fmtDur(t-a.coder.since)}</div>`
    :a.llm?`<div class="call">Waiting for the <b>${esc(a.llm.role)}</b> model (<code>${esc(a.llm.model)}</code>) on <b>${esc(a.llm.tag)}</b>, ${fmtDur(t-a.llm.since)}. Open "AI activity" to see the prompt once it answers.</div>`:"";
  reasoningLive();
  box.innerHTML=`<div class="what">${n?esc(n.phase):"Starting…"}</div>${pctBar(n&&n.pct)}
  <div class="sub">${n&&n.task?`Task: ${esc(n.task)} · attempt ${n.attempt}/3 · `:""}${n?`in this step for ${fmtDur(t-n.since)}`:""}${n&&n.detail?`<br>${esc(n.detail)}`:""}</div>
  ${stepper}${microSteps(n&&n.steps)}${schedLine(a.sched)}${call}${a.queued_user_tasks&&a.queued_user_tasks.length?`<div class="sub" style="margin-top:8px">Your queued tasks: ${a.queued_user_tasks.map(esc).join("; ")}</div>`:""}${timeline(a.events)}`;
}
function schedLine(sc){
  if(sc&&sc.state==="queued")return `<div class="call"><b class="lg-warn">⏳ Queued: ${esc(sc.detail||"waiting for another project")}</b></div>`;
  if(!sc||sc.priority!=="side")return "";
  const m={waiting:["lg-warn","⏸ Side agent: waiting for a free GPU (the main agent is working)"],preempted:["lg-warn","⏸ Side agent: stopped for the main agent, retrying when it is idle"],running:["lg-ok","▶ Side agent: using the GPU while main is idle"]}[sc.state]||["",sc.state];
  return `<div class="call"><b class="${m[0]}">${m[1]}</b>${sc.detail?` <span class="hint">${esc(sc.detail)}</span>`:""}</div>`;
}
function timeline(ev){
  if(!ev||!ev.length)return "";
  return `<div class="timeline">${ev.slice(-8).reverse().map(e=>`<div><span class="t">${esc((e.time||"").slice(11,19))}</span><span>${esc(e.phase)}${e.task?` <span class="meta">· ${esc(e.task.slice(0,60))}</span>`:""}${e.detail?` <span class="meta">· ${esc(e.detail.slice(0,100))}</span>`:""}</span></div>`).join("")}</div>`;
}

/* ---- project view ---- */
const WORDS_PER_TOKEN=0.75;
function perfFigures(d){
  const p=d.perf||{},l=d.live||{},g=d.gpu,on=l.active&&l.streaming;
  const sp=on?l.tps:p.tps,est=on&&l.estimated?"~":"";
  const gpu=g?[g.util+"%","GPU · "+(g.mem_used/1024).toFixed(1)+"/"+(g.mem_total/1024).toFixed(0)+" GB"+(p.gpu_share!=null?" · "+pct(p.gpu_share)+" generating":"")]:[p.gpu_share!=null?pct(p.gpu_share):"–","generating share of work time"];
  return {tokens:[nfmt(p.tokens||0),"tokens used"+(p.day_tokens?" · "+nfmt(p.day_tokens)+" today":"")],
    tps:[sp?est+sp:"–",on?"tokens/s now":"tokens/s avg"],wps:[sp?est+(sp*WORDS_PER_TOKEN).toFixed(1):"–",on?"words/s now":"words/s avg"],
    tpm:[p.tokens_per_min!=null?nfmt(p.tokens_per_min):"–","tokens/min avg"],gpu,avgcall:[p.avg_call_s!=null?dur(p.avg_call_s):"–","avg per model call"]};
}
function statCards(d){
  const v={merged:[d.merged,"features merged"],open:[d.backlog.pending||0,"open tasks"],done:[d.backlog.done||0,"done"],blocked:[d.backlog.blocked||0,"blocked"],iterations:[d.iteration,"iterations"],hours:[d.hours+" h","runtime"],...perfFigures(d)};
  return pref().stats.filter(k=>v[k]).map(k=>`<div class="stat"><b>${v[k][0]}</b><span>${v[k][1]}</span></div>`).join("");
}
function toolStats(x){
  const v=perfFigures(x),T=x.tool_durations||{};
  const avg=T.n?[dur(T.median_s),`avg per step (${dur(T.low)}–${dur(T.high)}, ${T.n} steps)`]:["–","avg per step (no data yet)"];
  return [["wps",v.wps],["tps",v.tps],["avg",avg],["tpm",v.tpm],["gpu",v.gpu],["tokens",v.tokens]].map(([k,c])=>`<div class="stat"><b>${c[0]}</b><span>${c[1]}</span></div>`).join("");
}
function renderProject(){
  if(!sel){$("#head").innerHTML=`<div class="empty"><h2>No project selected</h2><p>Pick a project on the left or create one with <b>+ New idea</b>.</p></div>`;$("#tabs").innerHTML=$("#body").innerHTML="";return}
  if(!detail)return;
  applyPref();
  const d=detail,[c,t]=statusOf(d);
  const pillF=d.failures?`<span class="pill bad">${d.failures} consecutive failures</span>`:"";
  const head=`<div class="top"><div><h1>${esc(d.name)} <span class="pill ${c==="run"?"ok":c==="stop"?"warn":""}">${t}</span> ${pillF}</h1><div class="idea">${esc(d.idea)}</div></div>
  <div class="btns">
    ${d.running?`<button class="danger" onclick="act('stop',{},'Will stop after the current task')" ${d.stopping?"disabled":""}>Stop</button><button class="danger" title="Kill switch: ends the agent and all its processes immediately; unfinished work is discarded" onclick="forceStop()">Force stop</button>`:`<button class="primary" onclick="act('start',{},'Agent started')">Start</button>`}
    ${list&&list.chat_enabled===false?"":`<button onclick="showChat(null,['${esc(d.name)}'])" title="Open a chat that knows this project">Chat about this</button>`}
    <button onclick="doTry()" ${d.try_running?"disabled":""}>${d.try_running?"Building test version…":"Test now"}</button>
  </div></div>
  <div class="stats">${statCards(d)}</div>`;
  if(head!==lastHead){$("#head").innerHTML=head;lastHead=head}
  const hide=pref().hide,cur=GROUPS.find(g=>g[2].includes(tab))||GROUPS[0];lastLeaf[cur[0]]=tab;
  const tb=`<div class="tabs">${GROUPS.filter(g=>g===cur||!hide.includes(g[0])||g[0]==="customize").map(g=>`<div class="tab ${g===cur?"on":""}" onclick="setTab('${lastLeaf[g[0]]||g[2][0]}')">${g[1]}</div>`).join("")}</div>`+
    (cur[2].length>1?`<div class="subtabs">${cur[2].map(k=>`<div class="subtab ${tab===k?"on":""}" onclick="setTab('${k}')">${LEAF[k]}</div>`).join("")}</div>`:"");
  if(tb!==lastTabs){$("#tabs").innerHTML=tb;lastTabs=tb}
  const body=projectBody(d);
  if(body!==null)setBody(body);
}
function setBody(html){
  const b=$("#body"),el=document.activeElement;
  if(EDITABLE.includes(tab)&&(dirty||(el&&["INPUT","TEXTAREA"].includes(el.tagName)&&b.contains(el)))&&b.dataset.tab===tab)return;
  if(html===lastBody&&b.dataset.tab===tab)return;
  const keep=$("#main").scrollTop,h0=$("#main").scrollHeight,lb=$("#logbox"),stick=lb&&lb.scrollHeight-lb.scrollTop-lb.clientHeight<40;
  const anchored=tab==="reasoning"||(view==="tool"&&toolTab_==="reason");
  b.innerHTML=html;b.dataset.tab=tab;lastBody=html;if(tab==="settings")loadModels();
  b.querySelectorAll("details[data-k]").forEach(x=>{if(openKeys.has(x.dataset.k))x.open=true;x.addEventListener("toggle",()=>{x.open?openKeys.add(x.dataset.k):openKeys.delete(x.dataset.k)})});
  $("#main").scrollTop=anchored&&keep>20?keep+Math.max(0,$("#main").scrollHeight-h0):keep;
  const nb=$("#logbox");if(nb&&(stick||!lb))nb.scrollTop=nb.scrollHeight;
}
function projectBody(d){
  if(tab==="overview"){
    const P=pref();
    const live=d.activity&&d.activity.now,liveId=live&&live.task?live.task.split(" ")[0]:null,livePct=live?live.pct:null;
    const rows=d.backlog_items.filter(i=>!(P.hideDone&&i.status==="done")).map(i=>{
      const cur=i.id===liveId&&i.status!=="done"&&i.status!=="blocked";
      const prog=cur&&livePct!=null?pctBar(livePct):i.status==="done"?`<span class="meta">100%</span>`:`<span class="meta">-</span>`;
      return `<tr><td>${esc(i.id)}</td><td>${esc(i.title)}${i.source==="ideas"?' <span class="pill ok">from you</span>':i.source==="user"?' <span class="pill info">your task</span>':""}</td><td>${esc(i.type)}</td><td>${Math.round(i.priority*10)/10}</td><td><span class="pill ${i.status==="done"?"ok":i.status==="blocked"?"bad":""}">${esc(i.status)}</span></td><td style="min-width:90px">${prog}</td><td style="white-space:nowrap">${taskBtns(i)}</td></tr>`}).join("");
    return `<div class="${P.showFeatures?"grid2":""}"><div class="panel"><h3>Backlog</h3><table><tr><th>ID</th><th>Task</th><th>Type</th><th>Prio</th><th>Status</th><th>Progress</th><th></th></tr>${rows||'<tr><td colspan=7 class="hint">empty</td></tr>'}</table></div>
    ${P.showFeatures?`<div class="panel"><h3>Finished features</h3>${d.features.length?d.features.map(f=>`<div style="padding:5px 0;border-top:1px solid var(--line)">${esc(f.title)}</div>`).join(""):'<div class="hint">None yet.</div>'}</div>`:""}</div>`;
  }
  if(tab==="task"){
    const mine=d.backlog_items.filter(i=>i.source==="user");
    return `<div class="panel"><h3>Give the agent a concrete job</h3>
    <p class="hint">The agent does <b>exactly this</b> in the product, with the full pipeline (plan, implement, build, tests, review, merge), and nothing else: no new ideas. If it is idle it runs just this job and stops; if it is already running it does your job next.</p>
    <textarea id="task" rows="6" oninput="dirty=true" placeholder="e.g. Extend the website with a dark mode toggle, remembered across visits"></textarea>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="primary" onclick="runTask()">Run this task</button></div></div>
    <div class="panel" style="margin-top:16px"><h3>Quick feedback</h3><p class="hint">A bug or wish in one sentence. The agent re-plans on its next pass.</p>
    <div class="row"><input id="say" placeholder="e.g. The save button does nothing" onkeydown="if(event.key==='Enter')say()"><button class="primary" onclick="say()">Send</button></div></div>
    <div class="panel" style="margin-top:16px"><h3>Your tasks</h3>${mine.length?`<table>${mine.map(i=>`<tr><td>${esc(i.id)}</td><td>${esc(i.title)}</td><td><span class="pill ${i.status==="done"?"ok":i.status==="blocked"?"bad":""}">${esc(i.status)}</span></td></tr>`).join("")}</table>`:'<div class="hint">None yet.</div>'}</div>`;
  }
  if(tab==="reasoning")return reasoningBody();
  if(tab==="llm"){
    if(!extra||extra.kind!=="llm")return `<div class="hint">Loading…</div>`;
    return `<p class="hint">Everything the model was asked and what it answered, newest first (the end of each prompt is shown). Roles: planner = plans and ideas, reviewer = reviews, summarizer = notes and docs.</p>`+
      (extra.entries.length?extra.entries.map((e,i)=>`<details data-k="llm-${esc(e.head)}"><summary><span class="pill info">${esc(e.head.replace(/^.*?\[/,"").replace(/\].*$/,""))}</span><span class="meta">${esc(e.head.replace(/\[.*$/,""))}</span></summary><div class="inner"><div class="hint">Prompt (end)</div><pre>${esc(e.prompt)}</pre><div class="hint" style="margin-top:10px">Answer</div><pre>${esc(e.answer)}</pre></div></details>`).join(""):'<div class="hint">No model calls recorded yet.</div>');
  }
  if(tab==="changes"){
    if(!extra||extra.kind!=="changes")return `<div class="hint">Loading…</div>`;
    const x=extra;
    const un=x.uncommitted.length?`<div class="panel" style="margin-bottom:16px"><h3>Uncommitted right now (work in progress)</h3><pre>${esc(x.uncommitted.join("\n"))}</pre></div>`:"";
    const files=x.tree?codePanel(x):`<div class="hint">Loading…</div>`;
    const commits=`<div class="panel"><h3>Commits (newest first)</h3>${x.commits.map(c=>`<details data-k="c-${esc(c.sha)}"><summary><code>${esc(c.sha)}</code> ${esc(c.subject)} <span class="meta">${esc((c.date||"").slice(0,16).replace("T"," "))} · ${c.files.length} files</span></summary><div class="inner">${c.files.map(f=>`<div class="fl"><a onclick="showDiff('${esc(c.sha)}','${esc(f.path).replace(/'/g,"")}')">${esc(f.path)}</a><span class="add">+${f.add}</span><span class="del">-${f.del}</span></div>`).join("")||'<div class="hint">no file changes</div>'}<div style="margin-top:6px"><button class="small" onclick="showDiff('${esc(c.sha)}','')">Whole commit diff</button></div></div></details>`).join("")||'<div class="hint">No commits yet.</div>'}</div>`;
    const dv=diffView?`<div class="panel" style="margin-top:16px"><h3>${esc(diffView.title)} <button class="small" onclick="diffView=null;lastBody='';renderProject()">close</button></h3><pre>${fmtDiff(diffView.text)}</pre></div>`:"";
    return un+dv+`<div style="margin-top:${dv||un?16:0}px">${files}</div><div style="margin-top:16px">${commits}</div>`;
  }
  if(tab==="ideas"){
    return `<div class="panel"><h3>IDEAS.md: your directives</h3><p class="hint">Wishes, implementation hints, rework requests, "do not do". Read continuously, even while the agent works.</p>
    <textarea id="ideas" rows="18" oninput="dirty=true">${esc(d.ideas)}</textarea>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="primary" onclick="saveFile('ideas','IDEAS.md')">Save</button></div></div>`;
  }
  if(tab==="sources"){
    return `<div class="panel"><h3>SOURCES.md: websites the agent should read</h3>
</p>
    <label class="fld">Web access<select id="web-mode-q" onchange="setWebMode(this.value)">${[["off","Off: no web access"],["sources","Sources only: just the pages listed below"],["open","Open web search: the agent may also search the web"]].map(([m,t])=>`<option value="${m}" ${m===d.web_mode?"selected":""}>${t}</option>`).join("")}</select></label>
    <textarea id="sources" rows="14" oninput="dirty=true" placeholder="https://example.com/docs   use this for the payment flow">${esc(d.sources)}</textarea>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="primary" onclick="saveFile('sources','SOURCES.md')">Save</button></div>
    <h3 style="margin-top:18px">Notifications</h3><p class="hint">Sends a small JSON message to a webhook (ntfy, Slack, Discord, Home Assistant, ...) when a task finishes, a task is blocked, the agent stalls on a repeated failure, or the run stops.</p>
    <label class="fld">Webhook URL<input id="hook-url" value="${esc((d.notify||{}).webhook||"")}" placeholder="https://ntfy.sh/your-topic" oninput="dirty=true"></label>
    <div class="row" style="flex-wrap:wrap;gap:10px;margin:6px 0">${["done","blocked","stalled","stopped"].map(e=>`<label><input type="checkbox" class="hook-ev" value="${e}" ${((d.notify||{}).events||["done","blocked","stalled","stopped"]).includes(e)?"checked":""} onchange="dirty=true"> ${e}</label>`).join("")}</div>
    <div class="row" style="justify-content:flex-end;margin-top:4px"><button class="primary" onclick="saveNotify()">Save</button></div></div>`;
  }
  if(tab==="testing"){
    return `<div class="panel"><p class="hint">"Test now" lets the agent finish its current task, builds the last working state and shows here how to run it. Press Start afterwards to continue.</p>
    ${d.testing?`<pre>${esc(d.testing)}</pre>`:'<div class="hint">No test version built yet.</div>'}${d.try_running||d.try_out?`<h4>Output</h4><pre>${esc(d.try_out)}</pre>`:""}</div>`;
  }
  if(tab==="log")return `<div class="loglegend"><span class="lg-step">step</span><span class="lg-ok">success</span><span class="lg-sec">security</span><span class="lg-warn">warning</span><span class="lg-err">error</span></div><pre id="logbox">${colorLog(d.log||"No log yet.")}${d.run_out&&/Traceback|Error/.test(d.run_out)?"\n\n--- run.out ---\n"+colorLog(d.run_out):""}${d.do_out&&/Traceback|Error/.test(d.do_out)?"\n\n--- do.out ---\n"+colorLog(d.do_out):""}</pre>`;
  if(tab==="settings")return settingsBody(d);
  if(tab==="profile")return profileBody(d);
  if(tab==="knowledge")return knowledgeBody(d);
  if(tab==="customize")return customizeBody(d);
  return `<div class="panel"><h3>GOAL.md: vision and quality bar</h3><p class="hint">The agent measures its work against this. Edit it to change direction; for single wishes use "Quick feedback" under "Do a task".</p>
    <textarea id="goal" rows="20" oninput="dirty=true">${esc(d.goal||"")}</textarea>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="primary" onclick="saveFile('goal','GOAL.md')">Save</button></div></div>`;
}
const AUTO=["auto","on","off"];
const CAP_GROUPS=[["Before coding",[["plan_critique","Plan review","A second look at multi-file plans before coding starts"],["test_first","Tests first","Writes the failing tests before the code, so a small model has a concrete target. auto = features and bug fixes",AUTO]]],
 ["While coding",[["step_by_step","Step by step","Splits a big plan into up to 3 coding passes. auto = plans with 4+ files or steps",AUTO],["require_tests","Tests required","Features and bug fixes without tests are rejected"],["protect_tests","Protect existing tests","Rejects a change that deletes tests instead of fixing the code"],["targeted_tests","Targeted tests first","After every coder round only the tests that belong to the change run first (fast feedback); the full suite still runs before merging"],["flaky_retry","Flaky test retry","A failing test is run once more; passing the second time is recorded as flaky instead of starting a debug round"],["verify_cache","Result cache","An unchanged tree is not built and tested twice"]]],
 ["Before merging",[["acceptance_check","Acceptance check","Ticks off every acceptance criterion against the diff, one fix round if one is unmet"],["coverage_gate","Coverage of new lines","Asks for more tests when fewer than this share of the added lines is executed by tests (needs the coverage package)",["off","50","70","90"]],["mutation_check","Mutation check","Flips operators in the new code and expects the tests to fail; adds tests if they do not notice (slower)"],["cleanup_pass","Cleanup pass","A short tidy-up of every change before review"],["security","Security gate","Static rules, scanners and a runtime probe before every merge",["on","off"]],["e2e","Browser tests","Real-browser check for web products (needs Playwright)",AUTO]]],
 ["After a failure",[["retry_replan","Retry strategy","Before a retry, analyses why the last attempt failed and picks a different approach"]]]];
const CAPS=CAP_GROUPS.flatMap(g=>g[1]);
function profileBody(d){
  const x=extra&&extra.kind==="profile"?extra:null;
  if(!x)return `<div class="hint">Loading…</div>`;
  const p=x.profile,F=x.features,sw=(id,on)=>`<label class="sw"><input type="checkbox" id="${id}" ${on?"checked":""} onchange="dirty=true"><span class="sw-track"><span class="sw-thumb"></span></span></label>`;
  const cap=CAP_GROUPS.map(([g,list])=>`<h4 style="margin:14px 0 4px">${g}</h4>`+list.map(([k,l,sub,opts])=>`<div class="cap"><div>${l}<small>${sub}</small></div>${opts?`<select id="cap-${k}" style="width:auto" onchange="dirty=true">${opts.map(t=>`<option ${t===F[k]?"selected":""}>${t}</option>`).join("")}</select>`:sw("cap-"+k,F[k])}</div>`).join("")).join("");
  return `<div class="panel"><h3>${esc(p.title||d.name)}</h3><p class="hint">${esc(p.description||d.idea)}</p>
    <div class="row" style="flex-wrap:wrap;gap:6px"><span class="pill info">${x.context.length} knowledge file(s)</span><span class="pill info">web: ${esc(x.web_mode)}</span><span class="pill info">security gate: ${esc(F.security)}</span><span class="pill info">browser tests: ${esc(F.e2e)}</span></div></div>
  <div class="panel" style="margin-top:16px"><h3>Details</h3><div class="grid2"><label class="fld">Name shown for the agent<input id="pf-title" value="${esc(p.title)}" maxlength="80" oninput="dirty=true" placeholder="${esc(d.name)}"></label>
    <label class="fld">Answer style<select id="pf-tone" onchange="dirty=true">${["neutral","concise","thorough","playful"].map(t=>`<option ${t===p.tone?"selected":""}>${t}</option>`).join("")}</select></label></div>
    <label class="fld" style="margin-top:10px">Description (what is this agent for?)<textarea id="pf-description" rows="2" maxlength="400" oninput="dirty=true">${esc(p.description)}</textarea></label></div>
  <div class="panel" style="margin-top:16px"><h3>Instructions</h3><p class="hint">Rules the agent follows in every task of this project: tech choices, code style, things to avoid, how to word commit messages. Added to every build prompt.</p>
    <textarea id="pf-instructions" rows="12" maxlength="8000" oninput="dirty=true" placeholder="- Use TypeScript and Tailwind&#10;- Never add a new dependency without a reason in the commit message&#10;- Keep functions under 40 lines">${esc(p.instructions)}</textarea></div>
  <div class="panel" style="margin-top:16px"><h3>Capabilities</h3>${cap}
    <p class="hint">Models, limits, web access and run mode are under <a onclick="setTab('settings')" style="cursor:pointer;text-decoration:underline">Models &amp; limits</a>; files and sources under <a onclick="setTab('knowledge')" style="cursor:pointer;text-decoration:underline">Knowledge files</a>. ${d.running?"Capabilities apply at the next start.":""}</p></div>
  <div class="row" style="justify-content:flex-end;margin-top:12px"><button class="primary" onclick="saveProfile()">Save</button></div>`;
}
async function saveProfile(){
  const v=id=>$("#"+id).value,features={};
  CAPS.forEach(([k,,,o])=>features[k]=o?v("cap-"+k):$("#cap-"+k).checked);
  try{await api(`/api/project/${sel}/profile`,{title:v("pf-title"),description:v("pf-description"),tone:v("pf-tone"),instructions:v("pf-instructions"),features});dirty=false;toast("Saved");extra=null;await fetchExtra();lastBody="";renderProject()}catch(e){toast(e.message,true)}
}
let ctxView=null;
function knowledgeBody(d){
  const x=extra&&extra.kind==="profile"?extra:null;
  if(!x)return `<div class="hint">Loading…</div>`;
  const rows=x.context.map(c=>`<div class="ctxrow"><span><a onclick="viewCtx('${esc(c.name).replace(/'/g,"")}')" style="cursor:pointer">${esc(c.name)}</a> <span class="meta">${nfmt(c.bytes)} bytes</span></span><button class="small danger" onclick="delCtx('${esc(c.name).replace(/'/g,"")}')">Remove</button></div>`).join("")||'<div class="hint">No files yet.</div>';
  const view=ctxView?`<div class="panel" style="margin-top:16px"><h3>${esc(ctxView.name)} <button class="small" onclick="ctxView=null;lastBody='';renderProject()">close</button></h3><pre>${esc(ctxView.text.slice(0,20000))}</pre></div>`:"";
  return `<div class="panel"><h3>Context files</h3><p class="hint">Give the agent background it cannot guess: specs, API docs, style guides, sample data. Text files only (md, txt, json, csv, code…), up to ${Math.round(x.limits.file/1000)} KB each and ${x.limits.files} files. For each task the agent reads the parts that match the task.</p>
    <div class="drop" id="drop" ondragover="event.preventDefault();this.classList.add('over')" ondragleave="this.classList.remove('over')" ondrop="event.preventDefault();this.classList.remove('over');uploadCtx(event.dataTransfer.files)">Drop files here or <input type="file" id="ctxfile" multiple style="width:auto;display:inline-block" onchange="uploadCtx(this.files)"></div>
    <details style="margin-top:10px"><summary>Or paste text as a file</summary><div class="inner"><div class="row"><input id="ctxname" placeholder="name.md" style="max-width:220px"></div><textarea id="ctxtext" rows="6" style="margin-top:8px" oninput="dirty=true"></textarea><div class="row" style="justify-content:flex-end;margin-top:8px"><button class="primary" onclick="pasteCtx()">Add</button></div></div></details>
    <div style="margin-top:12px">${rows}</div></div>${view}
  <div class="panel" style="margin-top:16px"><h3>Other knowledge</h3><div class="cap"><div>Web sources<small>${x.sources.trim()?x.sources.trim().split("\n").filter(l=>l.trim()&&!l.trim().startsWith("#")).length+" URL(s), web access: "+esc(x.web_mode):"None, web access: "+esc(x.web_mode)}</small></div><button class="small" onclick="setTab('sources')">Edit</button></div>
    <div class="cap"><div>Goal and ideas<small>Product vision and your standing wishes</small></div><span><button class="small" onclick="setTab('goal')">Goal</button> <button class="small" onclick="setTab('ideas')">Ideas</button></span></div></div>`;
}
async function ctxReload(){extra=null;await fetchExtra();lastBody="";renderProject()}
async function uploadCtx(files){
  let n=0;
  for(const f of files){try{await api(`/api/project/${sel}/context-add`,{name:f.name,text:await f.text()});n++}catch(e){toast(f.name+": "+e.message,true)}}
  if(n)toast(n+" file(s) added");dirty=false;await ctxReload();
}
async function pasteCtx(){try{await api(`/api/project/${sel}/context-add`,{name:$("#ctxname").value,text:$("#ctxtext").value});toast("Added");dirty=false;await ctxReload()}catch(e){toast(e.message,true)}}
async function delCtx(n){if(!confirm(`Remove ${n}?`))return;try{await api(`/api/project/${sel}/context-delete`,{name:n});ctxView=null;await ctxReload()}catch(e){toast(e.message,true)}}
async function viewCtx(n){try{const r=await api(`/api/project/${encodeURIComponent(sel)}/context?name=${encodeURIComponent(n)}`);ctxView=r;lastBody="";renderProject()}catch(e){toast(e.message,true)}}
function customizeBody(d){
  const P=pref(),chk=(on,fn)=>`<label class="sw"><input type="checkbox" ${on?"checked":""} onchange="${fn}"><span class="sw-track"><span class="sw-thumb"></span></span></label>`;
  return `<div class="panel"><h3>Look and feel of this project</h3><p class="hint">Saved in this browser for <b>${esc(d.name)}</b>.</p>
    <div class="cap"><div>Accent colour<small>Highlights tabs, stats and the project list</small></div><div class="sw-row">${ACCENTS.map(c=>`<button class="swatch ${P.accent===c?"on":""}" style="background:${c||"#e4e4e4"}" onclick="setPref('accent','${c}')" title="${c||"default"}"></button>`).join("")}</div></div>
    <div class="cap"><div>Compact layout<small>Tighter spacing to see more at once</small></div>${chk(P.dense,"setPref('dense',this.checked)")}</div>
    <div class="cap"><div>Opens on<small>The page shown when you select this project</small></div><select style="width:auto" onchange="setPref('start',this.value)">${GROUPS.flatMap(g=>g[2]).filter(k=>k!=="customize").map(k=>`<option value="${k}" ${P.start===k?"selected":""}>${esc(LEAF[k]||(k==="overview"?"Overview":"Do a task"))}</option>`).join("")}</select></div></div>
  <div class="panel" style="margin-top:16px"><h3>Header figures</h3>${STAT_KEYS.map(([k,l])=>`<div class="cap"><div>${l}</div>${chk(P.stats.includes(k),`togglePref('stats','${k}')`)}</div>`).join("")}</div>
  <div class="panel" style="margin-top:16px"><h3>Overview</h3><div class="cap"><div>Finished features list</div>${chk(P.showFeatures,"setPref('showFeatures',this.checked)")}</div><div class="cap"><div>Hide done tasks in the backlog</div>${chk(P.hideDone,"setPref('hideDone',this.checked)")}</div></div>
  <div class="panel" style="margin-top:16px"><h3>Pages</h3><p class="hint">Hide pages you do not need. They stay reachable while you are on them.</p>${GROUPS.filter(g=>g[0]!=="customize"&&g[0]!=="overview").map(g=>`<div class="cap"><div>${g[1]}</div>${chk(!P.hide.includes(g[0]),`togglePref('hide','${g[0]}')`)}</div>`).join("")}
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button onclick="localStorage.removeItem('pref:'+sel);setPref('accent','')">Reset to defaults</button></div></div>`;
}
function taskBtns(i){
  const b=(a,l,c)=>`<button class="small ${c||""}" onclick="taskAct('${a}','${esc(i.id)}')">${l}</button> `;
  if(i.status==="pending")return b("task-top","Top")+b("task-cancel","Cancel");
  if(i.status==="blocked")return b("task-reopen","Reopen")+b("task-cancel","Cancel");
  if(i.status==="cancelled")return b("task-reopen","Reopen");
  return "";
}
async function taskAct(a,id){await act(a,{id})}
function settingsBody(d){
  const x=extra&&extra.kind==="settings"?extra:null;
  if(!x)return `<div class="hint">Loading…</div>`;
  const st=x.settings,lb=x.limit_bounds;
  const model=(id,l,v,o={})=>{const pre=o.prefix||"",cur=String(v||"").replace(pre,""),names=[...x.installed_models];
    if(cur&&!names.includes(cur))names.unshift(cur);
    return `<label class="fld">${l}<select id="${id}" data-pre="${esc(pre)}">${o.empty?`<option value="">(auto-detect)</option>`:""}${names.map(n=>`<option value="${esc(pre+n)}" ${n===cur?"selected":""}>${esc(n)}${x.installed_models.includes(n)?"":" (not installed)"}</option>`).join("")}</select></label>`};
  const lim=Object.entries(lb).map(([k,[lo,hi]])=>`<label class="fld">${esc(k.replace(/_/g," "))} <span class="meta">${lo}–${hi}</span><input id="lim-${k}" type="number" min="${lo}" max="${hi}" value="${st.limits[k]}"></label>`).join("");
  const auto=st.routing.mode!=="off",rs=x.resolved,sug=x.catalog.filter(m=>!m.installed&&m.tier!=="light").slice(0,6);
  return `<div class="panel"><h3>Models</h3>
    <p class="hint">${x.installed_models.length?"Pick from your installed Ollama models.":"Ollama is not reachable, so the list is empty."}</p>
    <label class="fld">Model selection<select id="rt-mode" onchange="dirty=true;$('#rt-auto').style.display=this.value==='off'?'none':''">${[["auto","Automatic: the best installed model for each task"],["off","Fixed: always the models below"]].map(([m,l])=>`<option value="${m}" ${(m==="auto")===auto?"selected":""}>${l}</option>`).join("")}</select></label>
    <p class="hint">Automatic: simple tasks go to a small fast coder, complex tasks and retries to a stronger one, and the strongest model does the final review. It uses what is installed; the coder below is the standard one. Fixed: every task uses exactly the models you pick.</p>
    <div class="grid2" style="margin-top:10px">${model("m-planner","Planner (plans, ideas)",st.models.planner)}${model("m-reviewer","Reviewer",st.models.reviewer)}${model("m-summarizer","Summarizer (notes, docs)",st.models.summarizer)}${model("m-coder","Coder (Aider), standard",st.coder_model,{prefix:"ollama_chat/"})}</div>
    <div id="rt-auto" style="${auto?"":"display:none"}"><div class="grid2" style="margin-top:10px">${model("rt-light","Light coder (simple tasks)",st.routing.light,{empty:true})}${model("rt-heavy","Heavy coder (complex tasks, retries)",st.routing.heavy,{empty:true})}${model("rt-final","Final reviewer",st.routing.final_reviewer,{empty:true})}</div>
    <p class="hint" style="margin-top:8px">Leave a field on (auto-detect) to let the tool choose. Picked now: light <b>${esc(rs.light||"none (uses the coder)")}</b> · heavy <b>${esc(rs.heavy||"none (uses the coder)")}</b> · final reviewer <b>${esc(rs.final_reviewer||"none (uses the reviewer)")}</b>. Switching models costs a few seconds of load time.</p></div></div>
    <div class="panel" style="margin-top:16px"><h3>Install a model</h3><div class="row"><input id="mdlName" placeholder="e.g. qwen2.5-coder:7b (any name from ollama.com/library)" onkeydown="if(event.key==='Enter')pullModel($('#mdlName').value)"><button class="primary" onclick="pullModel($('#mdlName').value)">Install</button></div>${sug.length?`<div class="chips" style="margin-top:8px">${sug.map(m=>`<span class="chip" title="${esc(m.note)} · ~${m.gb} GB · ${esc(m.fit)}" onclick="$('#mdlName').value='${esc(m.name)}'">${esc(m.name)}</span>`).join("")}</div><p class="hint">Suggestions for automatic selection (hover for size and fit). Larger models than your VRAM spill into RAM and run slower.</p>`:""}</div>
    <div id="mlist" style="margin-top:16px"></div>
    <div class="panel" style="margin-top:16px"><h3>Limits and behaviour</h3><div class="grid2">${lim}
    <label class="fld">web access<select id="web-mode">${["off","sources","open"].map(m=>`<option ${m===st.web_mode?"selected":""}>${m}</option>`).join("")}</select></label>
    <label class="fld">documentation language<input id="doc-lang" value="${esc(st.doc_language)}"></label>
    <label class="fld">run mode<select id="run-mode"><option value="default" ${st.run.mode==="default"?"selected":""}>default: stop when 3 idea rounds find nothing</option><option value="rounds" ${st.run.mode==="rounds"?"selected":""}>fixed number of idea rounds</option><option value="endless" ${st.run.mode==="endless"?"selected":""}>endless: build until I stop it</option></select></label>
    <label class="fld">idea rounds (for "fixed")<input id="run-rounds" type="number" min="1" max="1000" value="${esc(st.run.rounds)}"></label>
    <label class="fld">GPU priority<select id="sc-prio"><option value="main" ${st.scheduler.priority==="main"?"selected":""}>normal (shares the GPU fairly)</option><option value="side" ${st.scheduler.priority==="side"?"selected":""}>side (only while main is idle)</option></select></label>
    <label class="fld">side waits until main is idle for (s)<input id="sc-idle" type="number" min="0" max="3600" value="${esc(st.scheduler.min_idle)}"></label>
    <label class="fld">Parallel model calls (same model only)<input id="sc-slots" type="number" min="1" max="4" value="${esc(st.scheduler.slots||2)}"></label></div>
    <p class="hint">Run one project as <b>main</b> and others as <b>side</b>: side agents use the GPU only in the pauses of main (its builds, tests, waiting) and are stopped as soon as main needs the model again.</p>
    <div class="row" style="justify-content:space-between;margin-top:10px"><span class="hint">Stack: <b>${esc(st.preset)}</b> (fixed after creation). ${d.running?"Changes apply at the next start.":""}</span><button class="primary" onclick="saveSettings()">Save settings</button></div></div>
    <div class="panel" style="margin-top:16px"><h3>Rename project</h3><div class="row"><input id="newname" value="${esc(d.name)}"><button onclick="renameProject()" ${d.running?"disabled":""}>Rename</button></div></div>
    <div class="panel" style="margin-top:16px;border-color:var(--bad)"><h3>Delete project</h3>
    <p class="hint">Permanently deletes <code>projects/${esc(d.name)}</code>: code, git history, memory and logs. The agent must be stopped. Type the project name to confirm.</p>
    <div class="row"><input id="delconfirm" placeholder="${esc(d.name)}"><button class="danger" onclick="deleteProject()" ${d.running?"disabled":""}>Delete forever</button></div></div>`;
}
async function saveSettings(){
  const v=id=>$("#"+id).value,limits={};
  document.querySelectorAll("[id^=lim-]").forEach(e=>limits[e.id.slice(4)]=e.value);
  try{const r=await api(`/api/project/${sel}/settings`,{models:{planner:v("m-planner"),reviewer:v("m-reviewer"),summarizer:v("m-summarizer")},coder_model:v("m-coder"),limits,web_mode:v("web-mode"),routing:{mode:v("rt-mode"),light:v("rt-light"),heavy:v("rt-heavy"),final_reviewer:v("rt-final")},doc_language:v("doc-lang"),run:{mode:v("run-mode"),rounds:v("run-rounds")},scheduler:{priority:v("sc-prio"),min_idle:v("sc-idle"),slots:v("sc-slots")}});
    dirty=false;toast(r.applies==="now"?"Settings saved":"Saved, applies at the next start");extra=null;await fetchExtra();lastBody="";renderProject()}catch(e){toast(e.message,true)}
}
async function renameProject(){
  try{const r=await api(`/api/project/${sel}/rename`,{name:$("#newname").value});sel=r.name;localStorage.setItem("sel",sel);dirty=false;detail=null;extra=null;lastBody=lastHead=lastTabs="";toast("Renamed");await refresh()}catch(e){toast(e.message,true)}
}
async function deleteProject(){
  const n=sel;if(!confirm(`Delete "${n}" and all its files permanently?`))return;
  try{await api(`/api/project/${n}/delete`,{confirm:$("#delconfirm").value});toast("Project deleted");sel=null;localStorage.removeItem("sel");tab="overview";dirty=false;detail=null;extra=null;lastBody=lastHead=lastTabs="";await refresh()}catch(e){toast(e.message,true)}
}
function setTab(k){$("#main").scrollTop=0;LS=null;liveHtml="";tab=k;dirty=false;extra=null;lastBody="";$("#body").dataset.tab="";lastTabs="";renderProject();fetchExtra().then(renderProject)}
async function fetchExtra(){
  try{
    if(view==="chat"||view==="dash")return;
    if(view==="tool"){
      const [t,act]=await Promise.all([api("/api/tool"),api("/api/tool/activity")]);
      extra={kind:"tool",...t,act};
      if(toolTab_==="plan")extra.plan=await api("/api/tool/run?id="+encodeURIComponent(planId));
      if(toolTab_==="changes"){if(!toolBranch&&t.branches.length)toolBranch=t.branches[0].name;
        if(toolBranch){extra.branchRun=(await api("/api/tool/run?branch="+encodeURIComponent(toolBranch))).run;extra.tree=await api("/api/tool/tree?branch="+encodeURIComponent(toolBranch));
          if(!codeInit&&extra.tree.files.length){codeInit=true;extra.tree.files.forEach(f=>f.path.split("/").slice(0,-1).forEach((_,i,a)=>codeOpen.add(a.slice(0,i+1).join("/"))))}}}
      return}
    if(!sel)return;
    if(tab==="llm")extra={kind:"llm",...await api(`/api/project/${encodeURIComponent(sel)}/llm`)};
    else if(tab==="profile"||tab==="knowledge")extra={kind:"profile",...await api(`/api/project/${encodeURIComponent(sel)}/profile`)};
    else if(tab==="settings"){if(!extra||extra.kind!=="settings")extra={kind:"settings",...await api(`/api/project/${encodeURIComponent(sel)}/settings`)}}
    else if(tab==="changes"){const n=encodeURIComponent(sel);const [c,t]=await Promise.all([api(`/api/project/${n}/changes`),api(`/api/project/${n}/tree?base=${encodeURIComponent(codeBase)}`)]);extra={kind:"changes",...c,tree:t};
      if(!codeInit&&t.files.length){codeInit=true;t.files.filter(f=>f.status).forEach(f=>f.path.split("/").slice(0,-1).forEach((_,i,a)=>codeOpen.add(a.slice(0,i+1).join("/"))))}}
    else if(tab==="reasoning")extra={kind:"reasoning",...await api(`/api/project/${encodeURIComponent(sel)}/thoughts`)};
  }catch(e){}
}
async function showDiff(sha,path){
  try{const r=await api(`/api/project/${encodeURIComponent(sel)}/diff?sha=${sha}&path=${encodeURIComponent(path)}`);diffView={title:path||("commit "+sha),text:r.diff};lastBody="";renderProject();$("#main").scrollTop=0}catch(e){toast(e.message,true)}
}
async function showFileHistory(path){
  if(!extra||!extra.commits)return;
  const c=extra.commits.find(c=>c.files.some(f=>f.path===path));if(c)showDiff(c.sha,path);
}
async function doTry(){await act("try",{},"Building a test version once the current task is done");setTab("testing")}
async function say(){const v=$("#say").value.trim();if(!v)return;try{await api(`/api/project/${sel}/say`,{msg:v});toast("Feedback added");$("#say").value="";dirty=false;await refresh()}catch(e){toast(e.message,true)}}
async function saveFile(kind,label){try{await api(`/api/project/${sel}/${kind}`,{text:$("#"+kind).value});dirty=false;toast(label+" saved");await refresh()}catch(e){toast(e.message,true)}}
async function setWebMode(m){try{await api(`/api/project/${encodeURIComponent(sel)}/settings`,{web_mode:m});toast("Web access: "+m)}catch(e){toast(e.message||String(e),true)}await refresh()}
async function saveNotify(){try{await api(`/api/project/${encodeURIComponent(sel)}/settings`,{notify:{webhook:$("#hook-url").value.trim(),events:[...document.querySelectorAll(".hook-ev:checked")].map(x=>x.value)}});dirty=false;toast("Saved")}catch(e){toast(e.message||String(e),true)}}
async function runTask(){
  const v=$("#task").value.trim();if(v.length<5){toast("Describe the task first",true);return}
  const r=await act("do",{task:v});
  if(r){toast(r.queued?"Queued: the running agent does it next":"Started: the agent does only this task");$("#task").value="";dirty=false;setTab("overview")}
}

/* ---- tool view ---- */
const TOOL_EXAMPLES=["Add a make target that prints the status of all projects","Make the UI log tab show the last 500 lines","Show the GPU temperature on the dashboard","Add a button to copy a project's log to the clipboard"];
const TOOL_STEPS=[["plan","Plan"],["backup","Backup"],["impact","Impact"],["implement","Implement"],["security","Security"],["test","Test"],["review","Review"],["docs","Docs"]];
function toolTab(k){$("#main").scrollTop=0;LS=null;liveHtml="";toolTab_=k;dirty=false;lastBody=lastTabs="";extra=null;codeView=null;renderTool();refresh()}
function renderTool(){
  $("#head").innerHTML=`<div class="top"><div><h1>Change the tool itself</h1><div class="idea">Tell it what to change in this program (Makefile, scripts, web UI, agent loop, docs). It does only what you write, on a separate git branch, and never merges by itself.</div></div></div><div class="stats" id="toolStats"></div>`;
  const tabs=[["tool","Task"],["plan","Plan"],["reason","Reasoning"],["changes","Changes"],["log","Live log"]];
  const tb=`<div class="tabs">${tabs.map(([k,l])=>`<div class="tab ${toolTab_===k?"on":""}" onclick="toolTab('${k}')">${l}</div>`).join("")}</div>`;
  if(tb!==lastTabs){$("#tabs").innerHTML=tb;lastTabs=tb}
  const x=extra&&extra.kind==="tool"?extra:{running:false,log:"",branches:[]};
  $("#toolStats").innerHTML=toolStats(x);
  if(toolTab_==="reason"){setBody(reasoningBody());reasoningLive();return}
  if(toolTab_==="plan")return setBody(planBody(x));
  if(toolTab_==="log")return setBody(`<div class="panel"><h3>Live log</h3><div class="loglegend"><span class="lg-step">step</span><span class="lg-ok">ok</span><span class="lg-sec">security</span><span class="lg-warn">warning</span><span class="lg-err">error</span></div><pre>${x.log?colorLog(x.log):"Nothing yet."}</pre></div>`);
  if(toolTab_==="changes"){
    const br=x.branches||[];if(!toolBranch&&br.length)toolBranch=br[0].name;
    const sel=`<select onchange="toolBranch=this.value;codeView=null;extra=null;lastBody='';refresh()">${br.map(b=>`<option ${b.name===toolBranch?"selected":""}>${esc(b.name)}</option>`).join("")}</select>`;
    return setBody(br.length?`<div class="row" style="margin-bottom:8px">Result branch: ${sel}</div>${(()=>{const b=br.find(z=>z.name===toolBranch);return b?`<div class="chips" style="margin-bottom:6px">${branchBadges(b)}</div><div class="row" style="margin-bottom:12px;flex-wrap:wrap">${branchActions(b,x.running)}</div>`:""})()}${restartBanner(x.build)}${runSummary(x.branchRun)}${previewBox(x.preview,x.last_merge,x.running)}${x.tree?codePanel(x):'<div class="hint">Loading…</div>'}${diffView?`<div class="panel" style="margin-top:16px"><h3>${esc(diffView.title)}</h3><pre>${fmtDiff(diffView.text)}</pre></div>`:""}`:`${previewBox(x.preview,x.last_merge,x.running)}<div class="hint">No result branches yet. Start a task on the Task tab.</div>`);
  }
  const body=`<div class="panel"><h3>What should change?</h3>
  <p class="hint">Examples: "Add a make target that prints the status of all projects", "Make the UI log tab show the last 500 lines", "Change the Makefile so make start-tmux also opens make thinking". Tests must stay green and docs are updated; guards for web fetching, CI and this feature itself cannot be changed.</p>
  <div class="chips" style="margin:6px 0">${TOOL_EXAMPLES.map((t,i)=>`<span class="chip" onclick="taskPrefill=TOOL_EXAMPLES[${i}];toolTab('tool')">${esc(t.slice(0,48))}${t.length>48?"…":""}</span>`).join("")}</div>
  <textarea id="tool" rows="6" oninput="dirty=true" ${x.running?"disabled":""}>${esc(taskPrefill)}</textarea>
  <div class="row" style="justify-content:flex-end;margin-top:10px">${x.running?'<button class="danger" onclick="toolCancel()">Cancel</button>':""}<button class="primary" onclick="runTool()" ${x.running?"disabled":""}>${x.running?"Working…":"Do this"}</button></div>
  ${x.running?'<p class="hint">Follow the checklist and every step in the <a href="#" onclick="planId=\'\';toolTab(\'plan\');return false">Plan</a> tab.</p>':""}
  ${x.log?`<h4>Progress</h4><pre>${colorLog(x.log)}</pre>`:""}</div>
  <div class="panel" style="margin-top:16px"><h3>Result branches</h3><p class="hint">Open the <b>Changes</b> tab to browse every changed file, try the branch and merge it there. Nothing changes in the running program until you merge.</p>
  ${x.branches.length?x.branches.map(b=>`<details data-k="${esc(b.name)}"><summary><code>${esc(b.name)}</code> ${esc(b.subject)} <span class="meta">${esc(b.date.slice(0,16))}</span></summary><div class="inner"><div class="chips" style="margin-bottom:8px">${branchBadges(b)}</div><pre>${esc(b.stat.join("\n")||"no changes")}</pre><div class="row" style="margin-top:8px;flex-wrap:wrap"><button class="small" onclick="toolDiff('${esc(b.name)}')">Show full diff</button><button class="small" onclick="toolBranch='${esc(b.name)}';toolTab('changes')">Try / merge</button>${branchActions(b,x.running)}</div></div></details>`).join(""):'<div class="hint">No branches yet.</div>'}</div>
  ${diffView?`<div class="panel" style="margin-top:16px"><h3>${esc(diffView.title)}</h3><pre>${fmtDiff(diffView.text)}</pre></div>`:""}`;
  setBody(body);
  if(taskPrefill){dirty=true;taskPrefill=""}
  reasoningLive();
}
const REQ_ICON={done:["✔","lg-ok"],partial:["◐","lg-warn"],missing:["✘","lg-err"],failed:["✘","lg-err"],unverified:["?","lg-warn"],open:["○",""]};
const STAGE_NAME={impact:"Impact",implement:"Implement",tests_added:"New tests",security:"Security",test:"Tests",review:"Review",docs:"Docs"};
const STAGE_CLS={ok:"ok",failed:"bad",warn:"warn",running:"info"};
function reqList(run){
  const rs=run.requirements||[];
  return rs.length?`<ul class="todo">${rs.map(r=>{const [ic,c]=REQ_ICON[r.status]||["○",""];return `<li><span class="${c}" style="font-weight:700">${ic}</span> ${esc(r.text)} <span class="meta">${esc(r.status)}${r.evidence?" · "+esc(r.evidence):""}</span></li>`}).join("")}</ul>`:'<div class="hint">No requirements recorded.</div>';
}
function runSummary(run){
  if(!run)return "";
  const rs=run.requirements||[],done=rs.filter(r=>r.status==="done").length;
  return `<details class="panel" style="margin-bottom:12px"><summary><b>What was asked: ${done}/${rs.length} requirements done</b>${(run.tests_added||[]).length?` <span class="pill ok">${run.tests_added.length} new test file(s)</span>`:` <span class="pill warn">no new tests</span>`}</summary><div class="inner">${reqList(run)}<div class="hint">Details in the <a href="#" onclick="planId='${esc(run.id)}';toolTab('plan');return false">Plan</a> tab.</div></div></details>`;
}
function branchBadges(b){
  const bad=[];
  if(b.merged)bad.push('<span class="pill info">already merged</span>');
  else{
    bad.push(`<span class="pill">${b.ahead} commit${b.ahead===1?"":"s"}</span>`);
    bad.push(b.mergeable===true?'<span class="pill ok">merges cleanly</span>':b.mergeable===false?'<span class="pill bad">conflicts with current code</span>':'<span class="pill">merge not checked</span>');
    if(b.behind)bad.push(`<span class="pill warn">current code moved on by ${b.behind}</span>`);
  }
  if(b.req_total)bad.push(`<span class="pill ${b.req_done===b.req_total?"ok":"warn"}">${b.req_done}/${b.req_total} requirements</span>`);
  bad.push(b.tests_added?`<span class="pill ok">${b.tests_added} new test file(s)</span>`:'<span class="pill warn">no new tests</span>');
  return bad.join(" ");
}
function branchActions(b,busy){
  return `<button class="small" ${busy||!b.behind||b.merged?"disabled":""} onclick="updateBranch('${esc(b.name)}')" title="Merge the current code into this branch so it stays mergeable">Update from current code</button>
  <button class="small danger" ${busy?"disabled":""} onclick="discardBranch('${esc(b.name)}')">Discard</button>
  ${b.task?`<button class="small" onclick="reuseTask(${esc(JSON.stringify(b.task))})" title="Put the original request into the Task box">Reuse request</button>`:""}`;
}
async function toolPost(path,body,ok){try{const r=await api(path,body);if(ok!==false)toast(r.message||"Done");lastBody="";await refresh();return r}catch(e){toast(e.message,true)}}
function toolCancel(){if(confirm("Stop the running self-change? Work in progress is discarded (finished commits stay on the branch)."))toolPost("/api/tool/cancel",{},false).then(()=>toast("Cancelled"))}
function discardBranch(n){if(confirm(`Delete ${n}? The changes on it are lost (the backup tags stay).`)){if(toolBranch===n)toolBranch="";toolPost("/api/tool/discard",{branch:n})}}
function updateBranch(n){toast("Merging the current code into the branch…");toolPost("/api/tool/update-branch",{branch:n})}
function reuseTask(t){taskPrefill=t;toolTab("tool")}
function statsLine(s){return s&&s.proposals?`<div class="hint" style="margin-bottom:8px">Track record: ${s.kept}/${s.proposals} steps kept (${Math.round(s.success_rate*100)}%), ${s.repaired} saved by the repair loop, ${s.avg_fix_rounds} failed rounds on average.${Object.entries(s.models||{}).map(([m,v])=>` ${esc(m)}: ${v.ok}/${v.tries}`).join(" ·")}</div>`:""}
function restartBanner(b){
  return b&&b.restart_needed?`<div class="panel" style="margin-bottom:12px;border-color:var(--warn)"><b class="lg-warn">Restart needed:</b> the code on disk (${esc(b.head.slice(0,8))}) is newer than the running UI (${esc(b.sha.slice(0,8))}). Stop it and start <code>make ui</code> again.</div>`:"";
}
function planBody(x){
  const runs=x.runs||[],pl=x.plan||{},run=pl.run;
  const sel=runs.length?`<div class="row" style="margin-bottom:12px">Run: <select onchange="planId=this.value;extra=null;lastBody='';refresh()">${runs.map(r=>`<option value="${esc(r.id)}" ${run&&r.id===run.id?"selected":""}>${esc(r.id)} · ${esc(r.status)} · ${r.done}/${r.total} · ${esc(r.title)}</option>`).join("")}</select></div>`:"";
  if(!run)return `${restartBanner(x.build)}<div class="hint">No runs yet. Describe a change on the <b>Task</b> tab: the request is split into a checklist and every step shows up here.</div>`;
  const ph=pl.phase&&pl.running?`<div class="hint"><b>Now:</b> ${esc(pl.phase.step)} ${esc(pl.phase.detail||"")} <button class="small danger" onclick="toolCancel()">Cancel</button></div>`:"";
  const again=run.instruction&&!pl.running&&["failed","cancelled"].includes(run.status)?`<div style="margin-top:8px"><button class="small" onclick="reuseTask(${esc(JSON.stringify(run.instruction))})">Run this request again</button></div>`:"";
  const props=(run.proposals||[]).map(p=>`<div class="panel" style="margin-top:10px"><h3 style="margin:0">${esc(p.title)} <span class="pill ${p.status==="kept"?"ok":p.status==="reverted"?"bad":"info"}">${esc(p.status)}</span></h3>
    ${(p.models||[]).length?`<div class="hint">Stronger model used: ${esc(p.models.join(", "))}</div>`:""}${(p.round_log||[]).map(r=>`<div class="hint">Round ${r.round} failed at <b>${esc(r.stage)}</b>: ${esc(r.problem||"")}${r.diagnosis?`<br>Diagnosis: ${esc(r.diagnosis)}`:""}</div>`).join("")}${p.rounds?`<div class="hint">${p.rounds} attempt(s) failed a check; the agent diagnosed the problem and repaired it${p.status==="kept"?" (final attempt passed)":""}.</div>`:""}${(p.tests_modified||[]).length?`<div class="hint"><span class="pill warn">existing tests adapted</span> ${esc(p.tests_modified.join(", "))}: look at this in the diff before merging.</div>`:""}
    <div class="chips" style="margin:8px 0">${Object.keys(STAGE_NAME).map(k=>`<span class="pill ${STAGE_CLS[p.stages[k]]||""}">${STAGE_NAME[k]}: ${esc(p.stages[k]||"pending")}</span>`).join("")}</div>
    ${p.note?`<div class="hint"><b>Note:</b> ${esc(p.note)}</div>`:""}${p.plan?`<details><summary>Plan and impact analysis</summary><div class="inner"><pre>${esc(p.plan)}</pre></div></details>`:""}</div>`).join("");
  return `${restartBanner(x.build)}${statsLine(x.stats)}${sel}<div class="panel"><h3>Todo: requirements <span class="pill ${run.status==="done"?"ok":["failed","cancelled"].includes(run.status)?"bad":"info"}">${esc(run.status)}</span></h3>
  ${run.instruction?`<p class="hint">${esc(run.instruction)}</p>`:""}${reqList(run)}${ph}${run.summary?`<div class="hint">${esc(run.summary)}</div>`:""}${again}
  ${run.branch?`<div class="hint">Result: <code>${esc(run.branch)}</code> · ${(run.files||[]).length} file(s) changed · <a href="#" onclick="toolBranch='${esc(run.branch)}';toolTab('changes');return false">Try it / merge</a></div>`:""}</div>
  <h4 style="margin-top:16px">Plan and progress per change</h4>${props||'<div class="hint">Planning…</div>'}`;
}
function verifyList(v){
  if(!v)return "";
  if(v.state==="running")return '<div class="hint">Checking that the preview really runs this branch…</div>';
  return `<div class="hint"><b class="${v.ok?"lg-ok":"lg-err"}">${v.ok?"Verified: the preview runs exactly this branch":"Verification failed: do not trust this preview"}</b></div><ul class="todo">${(v.checks||[]).map(c=>`<li><span class="${c.ok?"lg-ok":"lg-err"}" style="font-weight:700">${c.ok?"✔":"✘"}</span> ${esc(c.name)} ${c.detail?`<span class="meta">${esc(c.detail)}</span>`:""}</li>`).join("")}</ul>`;
}
function previewBox(pv,lm,busy){
  pv=pv||{};
  const ready=!!toolBranch&&!busy,dis=ready?"":"disabled",why=!toolBranch?"Available as soon as a change has produced a result branch.":busy?"Available when the current self-change has finished.":"";
  const url=pv.running?`${location.protocol}//${location.hostname}:${pv.port}/`:"";
  const rc=pv.tests_rc;
  return `<div class="panel" style="margin-bottom:12px"><h3>Try it before you merge</h3>
  <p class="hint">Checks the selected branch out in a separate folder and starts its UI on its own port with an empty projects folder. Your running tool, your projects and <code>main</code> stay untouched.</p>
  ${why?`<p class="hint"><b>${why}</b></p>`:""}
  <div class="row" style="flex-wrap:wrap">
    <button class="primary" ${dis} onclick="previewDo('/api/tool/preview',{branch:toolBranch})">${pv.running?"Restart preview":"Try this version"}</button>
    <button ${dis} onclick="previewDo('/api/tool/preview',{branch:toolBranch,tests:true})" title="Start the preview and run all unit tests plus the selftest in it">Try + run tests</button>
    ${pv.running||pv.tests_running?`<button class="danger" onclick="previewDo('/api/tool/preview/stop',{})">Stop and remove</button>`:""}
    ${url?`<a href="${esc(url)}" target="_blank" rel="noopener">Open preview (${esc(pv.branch)}) →</a>`:""}
  </div>
  ${pv.running?verifyList(pv.verify):""}
  ${pv.tests_running?`<div class="hint">Tests are running…</div>`:rc==null?"":`<div class="hint"><b class="${rc===0?"lg-ok":"lg-err"}">${rc===0?"Tests and selftest passed":"Tests failed (exit "+rc+")"}</b></div>`}
  ${pv.tests_log&&(pv.tests_running||rc!=null)?`<details><summary>Test output</summary><div class="inner"><pre>${esc(pv.tests_log)}</pre></div></details>`:""}
  <div class="row" style="flex-wrap:wrap;margin-top:10px">
    <button class="primary" ${dis} onclick="mergeTool()" title="Merges the branch into the tool's current branch. A backup tag is created first.">Merge this branch</button>
    ${lm?`<button class="danger" onclick="undoMerge()" title="Go back to ${esc(lm.tag)}">Undo last merge</button>`:""}
  </div>
  <p class="hint" style="margin-top:8px">Merge = local merge with a backup tag and an undo (you need a restart of the UI afterwards). Don't want it? <code>git branch -D ${esc(toolBranch||"self-improve/…")}</code></p></div>`;
}
async function mergeTool(){
  const pv=extra&&extra.preview;
  if(!confirm(`Merge ${toolBranch} into the tool now?${pv&&pv.branch===toolBranch&&pv.verify&&pv.verify.ok?"":"\n\nNote: this branch was not verified in the preview."}${pv&&pv.tests_rc===0&&pv.branch===toolBranch?"":"\nNote: you have not run the tests for this branch in the preview."}`))return;
  try{const r=await api("/api/tool/merge",{branch:toolBranch});toast(r.message);lastBody="";await refresh()}catch(e){toast(e.message,true)}
}
async function undoMerge(){
  if(!confirm("Undo the last merge (git reset --hard to the backup tag)?"))return;
  try{const r=await api("/api/tool/undo-merge",{});toast(r.message);lastBody="";await refresh()}catch(e){toast(e.message,true)}
}
async function previewDo(path,body){try{await api(path,body);lastBody="";await refresh()}catch(e){toast(e.message,true)}}
async function runTool(){
  const v=$("#tool").value.trim();if(v.length<10){toast("Describe the change in a sentence or two",true);return}
  try{await api("/api/tool/task",{task:v});toast("Started. Follow the checklist in the Plan tab.");dirty=false;planId="";toolTab("plan")}catch(e){toast(e.message,true)}
}
async function toolDiff(b){try{const r=await api("/api/tool/diff?branch="+encodeURIComponent(b));diffView={title:b,text:r.diff};lastBody="";renderTool()}catch(e){toast(e.message,true)}}

/* ---- dialogs ---- */
function showHelp(){
  const l=langs.find(x=>x.key===$("#nPreset").value);
  $("#nHelp").innerHTML=l&&!l.available?`<div class="warnbox">${esc(l.label)} is not installed on this machine.\n${esc(l.help)}</div>`:"";
}
async function openNew(){
  $("#nIdea").value="";$("#nName").value="";
  try{langs=(await api("/api/languages")).stacks}catch(e){langs=[];toast(e.message,true)}
  $("#nPreset").innerHTML='<option value="auto">Auto (agent picks the best fit)</option>'+langs.map(l=>`<option value="${esc(l.key)}">${esc(l.label)}${l.available?"":" (not installed)"}</option>`).join("");
  showHelp();dNew.showModal();
}
async function createProject(){
  try{const r=await api("/api/projects",{idea:$("#nIdea").value,name:$("#nName").value,preset:$("#nPreset").value});dNew.close();toast("Creating project \""+r.name+"\"…");await refresh()}catch(e){toast(e.message,true)}
}
async function openMaster(){try{$("#mText").value=(await api("/api/master")).text;dMaster.showModal()}catch(e){toast(e.message,true)}}
async function saveMaster(){try{await api("/api/master",{text:$("#mText").value});toast("Master prompt saved (applies from the next start)");dMaster.close()}catch(e){toast(e.message,true)}}

/* ---- chat (own DOM, never re-rendered by the poll so streaming is not interrupted) ---- */
let chat={list:[],models:[],def:"",cur:null,ctrl:null};
function mdInline(t){
  return esc(t).replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g,"<b>$1</b>").replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,"$1<i>$2</i>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function mdLite(t){
  return t.split(/```/).map((p,i)=>{
    if(i%2)return `<pre>${esc(p.replace(/^[^\n]*\n/,""))}</pre>`;
    const out=[];let list=null,para=[];
    const flush=()=>{if(para.length){out.push(`<p>${para.join("<br>")}</p>`);para=[]}};
    const close=()=>{if(list){out.push(`</${list}>`);list=null}};
    for(const line of p.split("\n")){
      let m;
      if(m=line.match(/^(#{1,4})\s+(.*)/)){flush();close();out.push(`<h${m[1].length+2}>${mdInline(m[2])}</h${m[1].length+2}>`)}
      else if(m=line.match(/^\s*[-*]\s+(.*)/)){flush();if(list!=="ul"){close();out.push("<ul>");list="ul"}out.push(`<li>${mdInline(m[1])}</li>`)}
      else if(m=line.match(/^\s*\d+[.)]\s+(.*)/)){flush();if(list!=="ol"){close();out.push("<ol>");list="ol"}out.push(`<li>${mdInline(m[1])}</li>`)}
      else if(m=line.match(/^>\s?(.*)/)){flush();close();out.push(`<blockquote>${mdInline(m[1])}</blockquote>`)}
      else if(/^\s*([-*_]\s*){3,}$/.test(line)){flush();close();out.push("<hr>")}
      else if(!line.trim()){flush();close()}
      else{close();para.push(mdInline(line))}
    }
    flush();close();return out.join("");
  }).join("");
}
async function showChat(id,projects){
  closeSb();view="chat";tab="chat";dirty=false;extra=null;lastBody=lastHead=lastTabs="";renderList();
  $("#now").innerHTML="";$("#now").className="";$("#tabs").innerHTML="";
  $("#head").innerHTML=`<div class="top"><div><h1>Chat</h1><div class="idea">Talk to a local model. Link projects so it knows their goal, backlog and recent activity.</div></div></div>`;
  try{
    const r=await api("/api/chats");chat.list=r.chats;chat.models=r.installed_models;chat.def=r.default_model;
    if(projects){chat.cur=await api("/api/chats",{projects})}
    else if(id){chat.cur=await api("/api/chat/"+id)}
    else if(chat.cur&&chat.list.some(c=>c.id===chat.cur.id)){chat.cur=await api("/api/chat/"+chat.cur.id)}
    else chat.cur=chat.list[0]?await api("/api/chat/"+chat.list[0].id):null;
    if(projects)chat.list=(await api("/api/chats")).chats;
  }catch(e){toast(e.message,true)}
  drawChat();
}
const SUGGEST_LINKED=["What should we build next?","Summarize the current state of the project","Which open tasks are the riskiest, and why?"];
const SUGGEST_FREE=["Help me turn a rough idea into a project plan","Compare two tech stacks for a small web app","Review my architecture idea and find weak spots"];
function drawChat(){
  const c=chat.cur,b=$("#body");b.dataset.tab="chat";
  const opts=chat.list.map(x=>`<option value="${x.id}" ${c&&x.id===c.id?"selected":""}>${esc(x.title)}</option>`).join("");
  const models=[...new Set([...(c?[c.model]:[]),...chat.models])];
  const names=list.projects.map(p=>p.name);
  const sug=(c&&c.projects.length?SUGGEST_LINKED:SUGGEST_FREE).map(t=>`<button class="sugg" onclick="chatAsk(this.dataset.t)" data-t="${esc(t)}">${esc(t)}</button>`).join("");
  b.innerHTML=`<div class="chat">
  <div class="chat-top"><select id="chSel" onchange="showChat(this.value)">${opts||"<option>No chats yet</option>"}</select>
    <button onclick="newChat()">+ New chat</button>${c?`<button class="ghost danger" onclick="delChat()">Delete</button>`:""}</div>
  ${c?`<div class="chat-meta"><label>Model<select id="chModel" onchange="chatUpdate({model:this.value})">${models.map(m=>`<option ${m===c.model?"selected":""}>${esc(m)}</option>`).join("")}</select></label>
    <div class="chips-wrap"><span class="lbl">Projects</span><div class="chips">${names.map(n=>`<span class="chip ${c.projects.includes(n)?"on":""}" onclick="toggleLink('${esc(n)}')">${esc(n)}</span>`).join("")||'<span class="hint" style="margin:0">no projects yet</span>'}</div></div>
    <label>Answer buttons use<select id="chTarget">${c.projects.map(n=>`<option>${esc(n)}</option>`).join("")||"<option value=''>(link a project)</option>"}</select></label></div>
  <div class="msgs" id="msgs">${c.messages.map(msgHtml).join("")||`<div class="chat-empty"><div class="big">What are we working on?</div><div class="hint">Ask anything. Link a project above and the model knows its goal, backlog and recent work.</div><div class="suggs">${sug}</div></div>`}</div>
  <div class="composer"><textarea id="chIn" rows="1" placeholder="Message CodeFactory…  (Enter sends, Shift+Enter for a new line)" oninput="grow(this)" onkeydown="chatKey(event)"></textarea>
    <button class="primary send" id="chSend" onclick="chatSend()" title="Send (Enter)">↑</button><button class="send" id="chStop" onclick="chat.ctrl&&chat.ctrl.abort()" title="Stop answering" style="display:none">■</button></div>`
  :`<div class="chat-empty"><div class="big">Start a conversation</div><button class="primary" onclick="newChat()">+ New chat</button></div>`}</div>`;
  const m=$("#msgs");if(m)m.scrollTop=m.scrollHeight;
  const i=$("#chIn");if(i)i.focus();
}
function grow(t){t.style.height="auto";t.style.height=Math.min(t.scrollHeight,200)+"px"}
function chatKey(e){if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();chatSend()}}
function chatAsk(t){$("#chIn").value=t;chatSend()}
function msgHtml(m,i){
  return m.role==="user"?`<div class="msg user">${esc(m.content)}</div>`
   :`<div class="msg assistant" data-i="${i}">${mdLite(m.content)}<div class="acts"><button class="small ghost" onclick="copyMsg(${i})">Copy</button><button class="small ghost" onclick="fromChat(${i},'say')">Add to ideas</button><button class="small ghost" onclick="fromChat(${i},'do')">Run as task</button></div></div>`;
}
function copyMsg(i){navigator.clipboard.writeText(chat.cur.messages[i].content).then(()=>toast("Copied"),()=>toast("Copy not allowed here",true))}
async function newChat(){const p=chat.cur?chat.cur.projects:[];chat.cur=null;await showChat(null,p)}
async function delChat(){if(!confirm("Delete this chat?"))return;try{await api(`/api/chat/${chat.cur.id}/delete`,{});chat.cur=null;await showChat()}catch(e){toast(e.message,true)}}
async function chatUpdate(b){try{chat.cur=await api(`/api/chat/${chat.cur.id}/update`,b);if(b.projects){const t=$("#chIn").value;drawChat();$("#chIn").value=t}}catch(e){toast(e.message,true)}}
function toggleLink(n){const p=chat.cur.projects.slice(),i=p.indexOf(n);i<0?p.push(n):p.splice(i,1);chatUpdate({projects:p})}
async function fromChat(i,kind){
  const t=$("#chTarget").value,m=chat.cur.messages[i];
  if(!t){toast("Link a project to this chat first",true);return}
  if(kind==="do"&&!confirm(`Send this answer to the agent of "${t}" as a task?`))return;
  try{await api(`/api/project/${t}/${kind}`,kind==="do"?{task:m.content}:{msg:m.content});toast(kind==="do"?"Task sent":"Added to ideas")}catch(e){toast(e.message,true)}
}
function setBusy(on){$("#chSend").style.display=on?"none":"";$("#chStop").style.display=on?"":"none"}
async function chatSend(){
  const inp=$("#chIn"),text=inp.value.trim();if(!text||chat.ctrl)return;
  inp.value="";grow(inp);const box=$("#msgs");
  const em=box.querySelector(".chat-empty");if(em)em.remove();
  box.insertAdjacentHTML("beforeend",`<div class="msg user">${esc(text)}</div><div class="msg assistant" id="live"><div class="think"><span class="dots"><span></span><span></span><span></span></span><span id="thinkT">Thinking…</span></div></div>`);
  const live=$("#live");box.scrollTop=box.scrollHeight;
  const t0=Date.now(),timer=setInterval(()=>{const e=$("#thinkT");if(!e){clearInterval(timer);return}const s=Math.round((Date.now()-t0)/1000);e.textContent=`Thinking… ${s}s`+(s>=8?" (the model may still be loading)":"")},500);
  chat.ctrl=new AbortController();setBusy(true);
  const id=chat.cur.id;let acc="";
  try{
    const r=await fetch(`/api/chat/${id}/send`,{method:"POST",headers:{"X-UI":"1","Content-Type":"application/json"},body:JSON.stringify({content:text}),signal:chat.ctrl.signal});
    if(!r.ok){let j={};try{j=await r.json()}catch(e){}throw new Error(j.error||r.statusText)}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf="";
    for(;;){
      const {done,value}=await rd.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      let k;while((k=buf.indexOf("\n\n"))>=0){
        const ev=buf.slice(0,k).replace(/^data: /,"");buf=buf.slice(k+2);
        if(ev==="[DONE]")continue;
        const j=JSON.parse(ev);
        if(j.delta){acc+=j.delta;live.classList.add("streaming");live.innerHTML=mdLite(acc);if(box.scrollHeight-box.scrollTop-box.clientHeight<140)box.scrollTop=box.scrollHeight}
        if(j.error)toast(j.error,true);
      }
    }
  }catch(e){if(e.name!=="AbortError")toast(e.message,true)}
  clearInterval(timer);chat.ctrl=null;
  if(view==="chat"&&chat.cur&&chat.cur.id===id){
    try{chat.cur=await api("/api/chat/"+id);chat.list=(await api("/api/chats")).chats}catch(e){}
    drawChat();
  }
}

/* ---- models: install and remove through Ollama ---- */
let mdl=null,mdlTimer=null;
async function loadModels(){
  const el=$("#mlist");if(!el||tab!=="settings"||view!=="project"){clearTimeout(mdlTimer);return}
  try{mdl=await api("/api/models")}catch(e){return}
  const m=mdl,act=Object.entries(m.pulls).filter(([k,v])=>v.active||v.error||v.status==="done");
  const bar=v=>{const p=v.total?Math.round(v.completed/v.total*100):0;return `<div class="bar"><i style="width:${p}%"></i></div><span class="meta">${esc(v.status)}${v.total?` · ${p}% of ${(v.total/1e9).toFixed(1)} GB`:""}</span>`};
  const html=`${m.ok?"":`<div class="warnbox">Ollama is not reachable at ${esc(m.url)}. Start it with <code>ollama serve</code>.</div>`}
  ${act.length?`<div class="panel" style="margin-bottom:16px"><h3>Downloads</h3>${act.map(([k,v])=>`<div class="dl"><div class="row" style="justify-content:space-between"><b>${esc(k)}</b>${v.active?`<button class="small" onclick="modelCancel('${esc(k)}')">Cancel</button>`:`<span class="pill ${v.error?"bad":"ok"}">${v.error?esc(v.error):"installed"}</span>`}</div>${v.active?bar(v):""}</div>`).join("")}</div>`:""}
  <div class="panel"><h3>Installed (${m.installed.length})</h3>
  ${m.installed.length?`<table><tr><th>Model</th><th>Size</th><th>Used by</th><th></th></tr>${m.installed.map(x=>`<tr><td><code>${esc(x.name)}</code></td><td>${x.gb} GB</td><td>${x.used_by.map(u=>esc(u.project)+(u.running?" (running)":"")).join(", ")||"-"}</td><td style="text-align:right"><button class="small danger" onclick="removeModel('${esc(x.name)}')">Remove</button></td></tr>`).join("")}</table>`:'<div class="hint">Nothing installed yet.</div>'}</div>
  <div class="panel" style="margin-top:16px"><h3>Recommended (optional)</h3><p class="hint">Sizes are approximate. Larger models than your VRAM spill into RAM and run slower.</p>
  <table><tr><th>Model</th><th>Role</th><th>Size</th><th>Fits a 12 GB GPU?</th><th></th></tr>${m.catalog.map(c=>{const p=m.pulls[c.name];return `<tr><td>${esc(c.name)} ${c.installed?'<span class="pill ok">installed</span>':""}</td><td>${esc(c.tier)}</td><td>~${c.gb} GB</td><td>${esc(c.fit)}<div class="hint">${esc(c.note)}</div></td><td style="text-align:right">${c.installed?"":`<button class="small primary" onclick="pullModel('${esc(c.name)}')" ${p&&p.active?"disabled":""}>${p&&p.active?"Downloading…":"Install"}</button>`}</td></tr>`}).join("")}</table></div>`;
  if(el.dataset.h!==html){el.innerHTML=html;el.dataset.h=html}
  clearTimeout(mdlTimer);mdlTimer=setTimeout(loadModels,Object.values(m.pulls).some(v=>v.active)?1000:5000);
}
async function pullModel(n){n=(n||"").trim();if(!n){toast("Enter a model name",true);return}try{await api("/api/models/pull",{name:n});toast("Download started");$("#mdlName")&&($("#mdlName").value="");await loadModels()}catch(e){toast(e.message,true)}}
async function modelCancel(n){try{await api("/api/models/cancel",{name:n});toast("Cancelling…")}catch(e){toast(e.message,true)}}
async function removeModel(n){
  const u=(mdl.installed.find(x=>x.name===n)||{used_by:[]}).used_by;
  if(!confirm(`Remove ${n}?`+(u.length?`\nIt is set in: ${u.map(x=>x.project).join(", ")}. Those projects need another model before they can run.`:"")))return;
  try{await api("/api/models/delete",{name:n});toast("Removed "+n);extra=null;lastBody="";await fetchExtra();await loadModels()}catch(e){toast(e.message,true)}
}


let shownErr=false;

function applyChatToggle(){
  const on=!list||list.chat_enabled!==false;
  const b=$("#chatBtn");if(b)b.style.display=on?"":"none";
  const c=$("#chatOn");if(c)c.checked=on;
}
async function setChatEnabled(v){
  try{await api("/api/ui-settings",{chat_enabled:v});toast(v?"Chat enabled":"Chat disabled: it no longer uses the model server");
    if(!v&&view==="chat"){view="project";tab="overview"}lastHead=lastBody="";await refresh()}catch(e){toast(e.message,true);applyChatToggle()}
}
let upd=null;
async function checkUpdate(fetchNow){
  try{upd=await api(fetchNow?"/api/update/check":"/api/update",fetchNow?{}:undefined);
    const b=$("#updBtn");if(b)b.textContent=upd.state==="behind"?`Update available (${upd.commits.length})`:"Update";
    if(b)b.classList.toggle("primary",upd.state==="behind")}catch(e){}
  return upd;
}
async function openUpdate(){
  toast("Checking GitHub …");const u=await checkUpdate(true);if(!u)return toast("Update check failed",true);
  if(u.state!=="behind")return toast(u.message);
  const msg=`${u.message}\n\n${u.commits.slice(0,12).join("\n")}${u.dirty&&u.dirty.length?"\n\nLocal changes block the update: "+u.dirty.join(", "):""}\n\nInstall now? (A backup tag is created; running agents must be stopped. The UI restarts if code changed.)`;
  if(u.dirty&&u.dirty.length)return alert(msg);
  if(!confirm(msg))return;
  try{const r=await api("/api/update/apply",{});toast(r.message,!r.ok);
    if(r.ok&&r.restart){setTimeout(()=>location.reload(),4000)}}catch(e){toast(e.message,true)}
  checkUpdate(false);
}
setTimeout(()=>checkUpdate(false),1500);
async function refresh(){
  try{
    list=await api("/api/projects");shownErr=false;applyChatToggle();
    if(view==="dash"){await renderDash();return}
    if(view==="project"&&(!sel||!list.projects.some(p=>p.name===sel)))sel=list.projects[0]?.name||null;
    renderList();
    if(view==="project"&&sel)detail=await api("/api/project/"+encodeURIComponent(sel));
    await fetchExtra();
    if(view==="chat"){renderNow();return}
    if(view==="tool")renderTool();else renderProject();
    renderNow();
  }catch(e){if(!shownErr){shownErr=true;toast(e.message,true)}}
}
document.addEventListener("click",e=>{
  const el=e.target.closest(".reason-card pre[data-p], .reason-card .fold[data-p]");
  if(!el||(getSelection()&&String(getSelection()).length))return;
  const on=el.classList.toggle("big");on?bigPre.add(el.dataset.p):bigPre.delete(el.dataset.p);
  lastBody="";
});
async function openQr(){
  try{const r=await api("/api/lan");if(!r.lan)return;
    $("#qrBox").innerHTML=`<h3 style="margin:0 0 10px">Open on your phone</h3>${r.svg}<p class="hint" style="margin:10px 0 4px">Scan with the camera (same Wi-Fi). The link contains your access token, so do not share it.</p><code>${esc(r.url)}</code>`;
    $("#dQr").showModal()}catch(e){toast(e.message,true)}
}
api("/api/lan").then(r=>{if(r&&r.lan)$("#qrBtn").style.display=""}).catch(()=>{});
/* Re-rendering must never move what the user scrolled: keep scroll positions (also horizontal ones: tables, tabs) across innerHTML swaps */
(()=>{
  const d=Object.getOwnPropertyDescriptor(Element.prototype,"innerHTML"),KEEP=new Set(["body","now","tabs","head"]);
  Object.defineProperty(Element.prototype,"innerHTML",{get(){return d.get.call(this)},set(v){
    if(!this.id||!KEEP.has(this.id)){d.set.call(this,v);return}
    const main=document.getElementById("main"),top=main?main.scrollTop:0,pos=[];
    this.querySelectorAll("*").forEach((e,i)=>{if(e.scrollLeft||e.scrollTop)pos.push([i,e.scrollLeft,e.scrollTop])});
    d.set.call(this,v);
    const all=this.querySelectorAll("*");
    pos.forEach(([i,l,t])=>{const e=all[i];if(e){e.scrollLeft=l;e.scrollTop=t}});
    if(main&&this.id!=="body"&&main.scrollTop!==top)main.scrollTop=top;
  }});
})();
const mobileQ=matchMedia("(max-width:800px)");
function toggleSb(){
  if(mobileQ.matches)document.body.classList.toggle("sb-open");
  else{document.body.classList.toggle("sb-off");localStorage.setItem("sbOff",document.body.classList.contains("sb-off")?"1":"")}
}
function closeSb(){document.body.classList.remove("sb-open")}
if(!mobileQ.matches&&localStorage.getItem("sbOff"))document.body.classList.add("sb-off");
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeSb()});

/* ---- dashboard: live AI numbers, polled every 2 s while the view is open ---- */
const DH={tps:[],gpu:[],vram:[],busy:[]};let DIAG=null,diagAt=0;
const nfmt=n=>n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e4?(n/1e3).toFixed(1)+"k":String(Math.round(n));
function spark(a,max,cls){
  if(a.length<2)return `<svg class="spark ${cls||""}" viewBox="0 0 100 34" preserveAspectRatio="none"></svg>`;
  const m=max||Math.max(1,...a),pts=a.map((v,i)=>`${(i/(a.length-1)*100).toFixed(1)},${(32-Math.min(v,m)/m*30).toFixed(1)}`).join(" ");
  return `<svg class="spark ${cls||""}" viewBox="0 0 100 34" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;
}
async function loadDiag(){if(Date.now()-diagAt<10000)return;diagAt=Date.now();try{DIAG=await api("/api/diagnostics")}catch(e){}}
const pct=v=>v==null?"–":Math.round(v*100)+"%";
const dur=s=>s==null?"–":s<90?Math.round(s)+"s":s<5400?Math.round(s/60)+" min":(s/3600).toFixed(1)+" h";
