/* The project graph: ONE interlinked graph of the whole project, drawn as bubbles (like Obsidian / Anytype).
   A bubble is a task: size = how many tasks build on it, fill = status, ring = layer (stage). Lines = "builds on".
   Layers pull the bubbles into bands from the foundation (top) down to the features; finished work is dimmed, can be linked-only, all or hidden.
   No libraries: a small force simulation, drag, pan, zoom, search (also by file), chain highlight. Positions survive the live refresh. */
const PG_PAL=["#6fa3d8","#b48ead","#d6a23c","#5fb3a8","#e06c75","#9ccc65","#f0a1a8","#8fbcbb"],PG_GAP=150;
const pg={pos:{},view:{x:60,y:40,k:1},fitted:false,sig:"",sigDone:"",alpha:0,raf:0,hover:null,find:"",stage:null,drag:null,pan:null,m:null,plan:[],proj:null};
const pgCol=l=>l==null?"#888":PG_PAL[l%PG_PAL.length];
const pgHash=s=>{let x=7;for(const c of s)x=(x*31+c.charCodeAt(0))>>>0;return x};
const pgLayerName=l=>{const p=(pg.plan||[]).find(x=>x.layer===l);return p?p.name:""};

/* what is on the canvas: open tasks always; finished ones by mode (auto = only while linked to open work, all, none) */
function pgModel(d){
  const mode=pref().doneMode||"auto",all=tgLive(d.backlog_items||[]),by=Object.fromEntries(all.map(i=>[i.id,i])),deps=tgDeps(all),kids={};
  for(const k in deps)deps[k].forEach(x=>(kids[x]=kids[x]||[]).push(k));
  const open=id=>by[id].status!=="done";
  const show=i=>i.status!=="done"||mode==="all"||(mode==="auto"&&[...deps[i.id],...(kids[i.id]||[])].some(open));
  const nodes=all.filter(show),ids=new Set(nodes.map(n=>n.id)),hid={},edges=[];
  nodes.forEach(i=>{hid[i.id]=deps[i.id].filter(x=>!ids.has(x)).length;deps[i.id].forEach(x=>{if(ids.has(x))edges.push([x,i.id])})});
  return {all,by,deps,kids,nodes,ids,edges,hid,mode};
}
const pgR=i=>(8+2.5*Math.min(((pg.m.kids[i.id]||[]).length),6))*(i.status==="done"?.7:1);

function pgSeed(){
  const m=pg.m;
  m.nodes.forEach(n=>{
    if(pg.pos[n.id])return;
    const near=[...m.deps[n.id],...(m.kids[n.id]||[])].map(x=>pg.pos[x]).filter(Boolean),h=pgHash(n.id);
    const jx=(h%80)-40,jy=((h>>7)%80)-40;
    if(near.length)pg.pos[n.id]={x:near.reduce((s,p)=>s+p.x,0)/near.length+jx,y:near.reduce((s,p)=>s+p.y,0)/near.length+jy,vx:0,vy:0};
    else pg.pos[n.id]={x:(h%900)-450,y:n.layer!=null?n.layer*PG_GAP+jy:((h>>5)%500)-250,vx:0,vy:0};
  });
}
function pgTick(){
  const m=pg.m,N=m.nodes,P=pg.pos,a=pg.alpha,layers=(pref().layout||"layers")==="layers",R={};
  N.forEach(n=>{R[n.id]=pgR(n);const p=P[n.id];p.fx=0;p.fy=0});
  for(let i=0;i<N.length;i++)for(let j=i+1;j<N.length;j++){
    const p=P[N[i].id],q=P[N[j].id];let dx=p.x-q.x,dy=p.y-q.y,d2=dx*dx+dy*dy;
    if(d2>160000)continue;if(d2<1){dx=Math.random()-.5;dy=Math.random()-.5;d2=1}
    const d=Math.sqrt(d2),f=(2600+(R[N[i].id]+R[N[j].id])*60)/d2;
    p.fx+=dx/d*f;p.fy+=dy/d*f;q.fx-=dx/d*f;q.fy-=dy/d*f;
  }
  m.edges.forEach(([x,y])=>{
    const p=P[x],q=P[y],dx=q.x-p.x,dy=q.y-p.y,d=Math.sqrt(dx*dx+dy*dy)||1,f=(d-(80+R[x]+R[y]))*.04;
    p.fx+=dx/d*f;p.fy+=dy/d*f;q.fx-=dx/d*f;q.fy-=dy/d*f;
  });
  N.forEach(n=>{
    const p=P[n.id];p.fx-=p.x*.004;
    if(layers&&n.layer!=null)p.fy+=(n.layer*PG_GAP-p.y)*.1;else p.fy-=p.y*.004;
    if(p.pin)return;
    p.vx=(p.vx+p.fx*a)*.6;p.vy=(p.vy+p.fy*a)*.6;p.x+=p.vx;p.y+=p.vy;
  });
  pg.alpha*=.985;
}
function pgPlace(){
  const svg=document.getElementById("pgSvg");if(!svg||!pg.m)return;
  const v=pg.view,P=pg.pos;
  svg.querySelectorAll(".pg-n").forEach(g=>{const p=P[g.dataset.id];if(p)g.setAttribute("transform",`translate(${p.x.toFixed(1)},${p.y.toFixed(1)})`)});
  svg.querySelectorAll(".pg-e,.pg-eh").forEach(e=>{
    const a=P[e.dataset.a],b=P[e.dataset.b];if(!a||!b)return;
    const ra=pgR(pg.m.by[e.dataset.a]),rb=pgR(pg.m.by[e.dataset.b])+3,dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1;
    e.setAttribute("d",`M${(a.x+dx/d*ra).toFixed(1)},${(a.y+dy/d*ra).toFixed(1)} Q${((a.x+b.x)/2-dy*.12).toFixed(1)},${((a.y+b.y)/2+dx*.12).toFixed(1)} ${(b.x-dx/d*rb).toFixed(1)},${(b.y-dy/d*rb).toFixed(1)}`);
  });
  pgApplyView(svg);
}
function pgApplyView(svg){
  const v=pg.view;svg=svg||document.getElementById("pgSvg");if(!svg)return;
  svg.querySelector("#pgRoot").setAttribute("transform",`translate(${v.x.toFixed(1)},${v.y.toFixed(1)}) scale(${v.k.toFixed(3)})`);
  svg.setAttribute("class",v.k<.7?"z-lo":v.k>1.3?"z-hi":"z-mid");
  svg.querySelectorAll(".pg-lt").forEach(t=>t.setAttribute("x",(8-v.x)/v.k));
}
function pgLoop(){
  pg.raf=0;if(!document.getElementById("pgSvg"))return;
  pgTick();pgTick();pgPlace();
  if(pg.alpha>.03||pg.drag)pg.raf=requestAnimationFrame(pgLoop);
}
function pgFit(){
  const svg=document.getElementById("pgSvg");if(!svg||!pg.m||!pg.m.nodes.length)return;
  const ps=pg.m.nodes.map(n=>pg.pos[n.id]),x0=Math.min(...ps.map(p=>p.x))-50,x1=Math.max(...ps.map(p=>p.x))+50,y0=Math.min(...ps.map(p=>p.y))-50,y1=Math.max(...ps.map(p=>p.y))+60;
  const w=svg.clientWidth||900,h=svg.clientHeight||560,k=Math.max(.25,Math.min(1.3,w/(x1-x0),h/(y1-y0)));
  pg.view={k,x:(w-(x1-x0)*k)/2-x0*k,y:(h-(y1-y0)*k)/2-y0*k};
  pgApplyView(svg);
}
function pgStart(){
  const svg=document.getElementById("pgSvg");if(!svg||!pg.m)return;
  pgSeed();
  const changed=pg.sigDone!==pg.sig;
  if(changed){
    if(!pg.fitted){pg.alpha=1;for(let k=0;k<160;k++)pgTick();pg.alpha=.15}else pg.alpha=Math.max(pg.alpha,.5);
  }
  pgPlace();
  if(!pg.fitted){pgFit();pg.fitted=true}
  pgMark();pg.sigDone=pg.sig;
  if(changed&&!pg.raf)pg.raf=requestAnimationFrame(pgLoop);
}
function pgKick(){pg.alpha=Math.max(pg.alpha,.4);if(!pg.raf)pg.raf=requestAnimationFrame(pgLoop)}

/* highlight: the chain of the hovered/selected task, search hits (id, title or file), a stage; everything else dims */
function pgMark(){
  const svg=document.getElementById("pgSvg");if(!svg||!pg.m)return;
  const m=pg.m,f=pg.find.toLowerCase().trim(),focus=tgState.sel||pg.hover,chain=focus&&m.by[focus]?tgChain(m.all,focus):null;
  const hit=f?new Set(m.nodes.filter(i=>(i.id+" "+i.title+" "+(i.files||[]).join(" ")).toLowerCase().includes(f)).map(i=>i.id)):null;
  const lay=pg.stage!=null?new Set(m.nodes.filter(i=>i.layer===pg.stage).map(i=>i.id)):null;
  const on=id=>(!chain||chain.has(id))&&(!hit||hit.has(id))&&(!lay||lay.has(id));
  svg.querySelectorAll(".pg-n").forEach(g=>{const id=g.dataset.id;g.classList.toggle("dim",!on(id));g.classList.toggle("sel",id===tgState.sel);g.classList.toggle("hot",!!(hit&&hit.has(id)))});
  svg.querySelectorAll(".pg-e").forEach(e=>{const lit=on(e.dataset.a)&&on(e.dataset.b);e.classList.toggle("dim",!lit);e.classList.toggle("lit",!!chain&&lit)});
}
function pgInfo(){const i=document.getElementById("tgInfo");if(i)i.innerHTML=tgInfoText()}
function pgSelect(id){tgState.sel=tgState.sel===id?null:id;tgState.edge=null;tgState.mode=null;pgMark();pgInfo()}
function pgFind(v){pg.find=v;pgMark()}
function pgFile(f){pg.find=f;const i=document.getElementById("pgFind");if(i)i.value=f;pgMark()}
function pgStage(l){pg.stage=pg.stage===l?null:l;document.querySelectorAll(".pg-chip").forEach(c=>c.classList.toggle("on",+c.dataset.l===pg.stage));pgMark()}
function pgCycleDone(){setPref("doneMode",{auto:"all",all:"none",none:"auto"}[pref().doneMode||"auto"])}

function tgInfoText(){
  const m=pg.m,i=tgState.sel&&m&&m.by[tgState.sel];
  if(tgState.edge)return `Link <b>${esc(tgState.edge.a)}</b> → <b>${esc(tgState.edge.b)}</b> <button class="small" onclick="gtEdge('${esc(tgState.edge.a)}','${esc(tgState.edge.b)}')">Remove link</button> <button class="small" onclick="tgState.edge=null;pgInfo()">Keep</button>`;
  if(!i)return `<span class="hint">Click a bubble for its links, files and reason · hover shows its chain · drag a bubble or the background · wheel zooms · click a line to remove a link. Size = how many tasks build on it, ring = layer, orange line = critical path.</span>`;
  const chip=id=>`<span class="pg-ch" onclick="pgSelect('${esc(id)}')" title="${esc(m.by[id].title)} · ${esc(m.by[id].status)}">${esc(id)} ${esc(m.by[id].title.length>30?m.by[id].title.slice(0,29)+"…":m.by[id].title)}</span>`;
  const needs=m.deps[i.id],later=m.kids[i.id]||[],files=i.files||[],ln=i.layer!=null?pgLayerName(i.layer):"";
  const st=`<span class="pill ${i.status==="done"?"ok":i.status==="blocked"?"bad":""}">${esc(i.status.replace("_"," "))}</span>`;
  const files_=files.length?files.map(f=>`<span class="pg-ch file" onclick="pgFile('${esc(f)}')" title="Show every task that touched this file">${esc(f)}</span>`).join(""):'<span class="meta">not known yet (filled when it is built or scouted)</span>';
  return `<div class="pg-info"><div><b>${esc(i.id)}</b> ${esc(i.title)} ${st}${i.layer!=null?` <span class="pill info" title="Build layer">L${i.layer}${ln?" "+esc(ln):""}</span>`:""}${i.source==="ideas"?' <span class="pill ok">from you</span>':i.source==="user"?' <span class="pill info">your task</span>':""}</div>
    ${i.why?`<div class="meta reason">Why: ${esc(i.why)}</div>`:""}${i.result?`<div class="meta">Result: ${esc(i.result)}</div>`:""}${i.status==="blocked"&&i.reason?`<div class="meta reason">Blocked: ${esc(i.reason)}</div>`:""}
    <div class="pg-rows"><div><span class="meta">Builds on</span> ${needs.length?needs.map(chip).join(""):'<span class="meta">nothing: part of the foundation</span>'}</div>
    <div><span class="meta">Unlocks</span> ${later.length?later.map(chip).join(""):'<span class="meta">nothing yet</span>'}</div>
    <div><span class="meta">Files</span> ${files_}</div></div>
    <div class="tg-row" style="margin-top:6px">${typeof gtTools==="function"?gtTools(i):""}${typeof taskBtns==="function"&&detail&&!tgState.mode?taskBtns(i,detail.running):""}</div></div>`;
}

function pgBar(d,m){
  const P=pref(),lay=P.layout||"layers",doneLbl={auto:"Done: linked only",all:"Done: all",none:"Done: hidden"}[m.mode],finished=m.all.filter(i=>i.status==="done").length;
  return `<div class="tg-top"><div class="tg-head"><h3>Project graph</h3>${gtStats(d,m)}</div>
    <div class="tg-tools"><div class="tg-grp"><button class="small" onclick="pgCycleDone()" title="Finished tasks (${finished}): linked only = shown while they are linked to open work, all, or hidden. They stay in the graph as links.">${doneLbl}</button><button class="small" onclick="setPref('layout','${lay==="layers"?"free":"layers"}')" title="Layers: foundation at the top, features below. Free: only the links decide.">${lay==="layers"?"Layers":"Free"}</button><button class="small" onclick="gtOpenHistory()" title="See the graph as it was after each review">History</button></div>
    <div class="tg-grp">${typeof templateBar==="function"?templateBar():""}<button class="small" onclick="confirm('Rebuild all links from scratch? Tasks without links are tied to the layer below them now, and the next re-plan links every task to what it builds on.')&&gtAct('graph-rebuild',{},'Links are rebuilt: the next re-plan finishes it')" title="Build the dependencies of the whole project again, from the foundation up">Rebuild links</button></div>
    ${d.running?"":`<div class="tg-grp"><button class="small" onclick="doPreview()" title="Let the agent propose changes; nothing is applied until you confirm">Preview re-plan</button><button class="small primary" onclick="doReplan()" title="Think the whole graph through again: re-link, merge, split, and suggest new graphs">Re-plan</button></div>`}</div></div>`;
}
function pgStages(m){
  const ls=[...new Set(m.all.map(i=>i.layer).filter(l=>l!=null))].sort((a,b)=>a-b);
  return ls.map(l=>{const t=m.all.filter(i=>i.layer===l),dn=t.filter(i=>i.status==="done").length,nm=pgLayerName(l);
    return `<span class="pg-chip${pg.stage===l?" on":""}" data-l="${l}" onclick="pgStage(${l})" style="--c:${pgCol(l)}" title="${esc(nm)}: ${dn} of ${t.length} done. Click to focus this layer."><i></i>L${l}${nm?" "+esc(nm.length>22?nm.slice(0,21)+"…":nm):""} <b>${dn}/${t.length}</b></span>`}).join("");
}
function tgPanel(d){
  if(pg.proj!==sel){pg.proj=sel;pg.pos={};pg.fitted=false;pg.sigDone="";pg.stage=null;pg.find="";tgState.sel=null}
  pg.plan=d.plan||[];
  const m=pg.m=pgModel(d);tgItems=m.by;
  if(tgState.sel&&!m.by[tgState.sel])tgState.sel=null;
  const bar=pgBar(d,m),form=typeof customForm==="function"?customForm():"";
  if(!m.nodes.length)return `<div class="panel">${bar}${form}<div class="hint">${m.all.length?"Everything is finished. Use “Done: …” to show the finished tasks.":"No tasks yet."}</div></div>`;
  const crit=gtCritical(d),blocks=d.blocks||{},live=d.activity&&d.activity.now&&d.activity.now.task?d.activity.now.task.split(" ")[0]:null,layers=(pref().layout||"layers")==="layers";
  const lines=layers?[...new Set(m.nodes.map(n=>n.layer).filter(l=>l!=null))].sort((a,b)=>a-b).map(l=>`<g class="pg-lay"><line x1="-8000" x2="8000" y1="${l*PG_GAP}" y2="${l*PG_GAP}"/><text class="pg-lt" y="${l*PG_GAP-6}" fill="${pgCol(l)}">L${l} ${esc(pgLayerName(l))}</text></g>`).join(""):"";
  const edges=m.edges.map(([a,b])=>{const dn=m.by[a].status==="done"&&m.by[b].status==="done",cr=crit.edges.has(a+">"+b);
    return `<path class="pg-e${dn?" old":""}${cr?" crit":""}" data-a="${esc(a)}" data-b="${esc(b)}" marker-end="url(#pgA)"/><path class="pg-eh" data-a="${esc(a)}" data-b="${esc(b)}"/>`}).join("");
  const nodes=m.nodes.map(i=>{
    const r=pgR(i),col=TG_COLORS[i.status]||"#888",t=i.title.length>30?i.title.slice(0,29)+"…":i.title,extra=(m.hid[i.id]?` ✓${m.hid[i.id]}`:"")+(blocks[i.id]?` ⛔${blocks[i.id]}`:"")+(crit.nodes.has(i.id)?" ★":"");
    return `<g class="pg-n st-${esc(i.status)}${i.id===live&&i.status==="in_progress"?" pg-live":""}" data-id="${esc(i.id)}"><title>${esc(i.id+" · "+i.status+" · "+i.title+(i.why?"\nWhy: "+i.why:"")+((i.files||[]).length?"\nFiles: "+i.files.slice(0,5).join(", "):""))}</title><circle r="${r.toFixed(1)}" fill="${col}" fill-opacity="${i.status==="done"?.35:.8}" stroke="${pgCol(i.layer)}" stroke-width="2.5"/><text class="pg-l" y="${(r+13).toFixed(1)}" text-anchor="middle">${esc(t)}${extra}</text></g>`;
  }).join("");
  pg.sig=m.nodes.map(n=>n.id+n.status).join()+"|"+m.edges.map(e=>e.join(">")).join()+"|"+layers+"|"+m.mode;
  setTimeout(pgStart,0);
  return `<div class="panel">${bar}${form}
  <div class="tg-bar"><button class="btn sm" onclick="pgZoomBy(1.25)">+</button><button class="btn sm" onclick="pgZoomBy(.8)">−</button><button class="btn sm" onclick="pgFit()">Fit</button><button class="btn sm" onclick="pgKick()" title="Shake the layout">Relax</button>
    <input id="pgFind" class="small" style="width:210px" placeholder="Search task, id or file…" value="${esc(pg.find)}" oninput="pgFind(this.value)"><span class="pg-stages">${pgStages(m)}</span></div>
  <div class="pg-wrap"><svg id="pgSvg" class="z-mid"><defs><marker id="pgA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#ffffff50"/></marker></defs><g id="pgRoot">${lines}${edges}${nodes}</g></svg></div>
  <div id="tgInfo">${tgInfoText()}</div>
  <p class="hint" style="margin-top:6px">${m.edges.length} link(s) between ${m.nodes.length} task(s). The agent plans along this graph: a task is only started when what it builds on is done, and it gets the files of those tasks as its starting point.</p></div>`;
}
function pgZoomBy(f){
  const svg=document.getElementById("pgSvg");if(!svg)return;
  const v=pg.view,w=svg.clientWidth/2,h=svg.clientHeight/2,k=Math.min(3,Math.max(.2,v.k*f));
  pg.view={k,x:w-(w-v.x)/v.k*k,y:h-(h-v.y)/v.k*k};pgApplyView(svg);
}

/* interaction (delegated once) */
const pgWorld=(svg,e)=>{const r=svg.getBoundingClientRect(),v=pg.view;return{x:(e.clientX-r.left-v.x)/v.k,y:(e.clientY-r.top-v.y)/v.k}};
document.addEventListener("mousedown",e=>{
  const svg=e.target.closest&&e.target.closest("#pgSvg");if(!svg)return;
  const n=e.target.closest(".pg-n");
  if(n)pg.drag={id:n.dataset.id,sx:e.clientX,sy:e.clientY,moved:false};
  else pg.pan={sx:e.clientX,sy:e.clientY,x:pg.view.x,y:pg.view.y,moved:false};
});
document.addEventListener("mousemove",e=>{
  const svg=document.getElementById("pgSvg");
  if(pg.drag&&svg){
    const d=pg.drag;if(!d.moved&&Math.hypot(e.clientX-d.sx,e.clientY-d.sy)<4)return;
    d.moved=true;const p=pg.pos[d.id],w=pgWorld(svg,e);if(!p)return;
    p.x=w.x;p.y=w.y;p.vx=p.vy=0;p.pin=true;pg.alpha=Math.max(pg.alpha,.25);if(!pg.raf)pg.raf=requestAnimationFrame(pgLoop);
  }else if(pg.pan&&svg){
    const a=pg.pan;if(Math.hypot(e.clientX-a.sx,e.clientY-a.sy)>3)a.moved=true;
    pg.view.x=a.x+e.clientX-a.sx;pg.view.y=a.y+e.clientY-a.sy;pgApplyView(svg);
  }
});
document.addEventListener("mouseup",()=>{
  const d=pg.drag,a=pg.pan;pg.drag=pg.pan=null;
  if(d){
    const p=pg.pos[d.id];if(p)p.pin=false;
    if(!d.moved&&!(typeof gtNodeClick==="function"&&gtNodeClick(d.id)))pgSelect(d.id);
  }else if(a&&!a.moved&&tgState.sel){tgState.sel=null;tgState.mode=null;tgState.edge=null;pgMark();pgInfo()}
});
document.addEventListener("click",e=>{
  const eh=e.target.closest&&e.target.closest(".pg-eh");
  if(eh&&!(pg.pan&&pg.pan.moved)){tgState.edge={a:eh.dataset.a,b:eh.dataset.b};pgInfo()}
});
document.addEventListener("mouseover",e=>{const n=e.target.closest&&e.target.closest(".pg-n");if(n&&!pg.drag&&pg.hover!==n.dataset.id){pg.hover=n.dataset.id;pgMark()}});
document.addEventListener("mouseout",e=>{const n=e.target.closest&&e.target.closest(".pg-n");if(n&&pg.hover){pg.hover=null;pgMark()}});
document.addEventListener("wheel",e=>{
  const svg=e.target.closest&&e.target.closest("#pgSvg");if(!svg)return;
  e.preventDefault();
  const r=svg.getBoundingClientRect(),v=pg.view,k=Math.min(3,Math.max(.2,v.k*(e.deltaY<0?1.1:1/1.1))),mx=e.clientX-r.left,my=e.clientY-r.top;
  pg.view={k,x:mx-(mx-v.x)/v.k*k,y:my-(my-v.y)/v.k*k};pgApplyView(svg);
},{passive:false});
