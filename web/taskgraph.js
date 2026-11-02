/* Task graph: the main way to see the plan. Tasks are nodes, depends_on are arrows; every connected graph is its own band.
   Finished graphs are hidden until asked for; a finished task stays visible while its graph still has open work. No libraries. */
const TG_COLORS={done:"#5fb36b",in_progress:"#6fa3d8",pending:"#d6a23c",blocked:"#e06c75"};
const tgState={sel:null,z:1};
let tgItems={};
const tgLive=items=>items.filter(i=>i.status!=="cancelled");
function tgDeps(items){const ids=new Set(items.map(i=>i.id));return Object.fromEntries(items.map(i=>[i.id,[...new Set(i.depends_on||[])].filter(d=>ids.has(d)&&d!==i.id)]))}
function tgComps(items){
  const live=tgLive(items),deps=tgDeps(live),adj=Object.fromEntries(live.map(i=>[i.id,new Set()]));
  for(const k in deps)deps[k].forEach(d=>{adj[k].add(d);adj[d].add(k)});
  const seen=new Set(),out=[];
  Object.keys(deps).sort().forEach(k=>{
    if(seen.has(k))return;const c=[],st=[k];seen.add(k);
    while(st.length){const n=st.pop();c.push(n);adj[n].forEach(m=>{if(!seen.has(m)){seen.add(m);st.push(m)}})}
    out.push(c.sort());
  });
  return out;
}
/* what is shown: all tasks of a graph with open work (finished ones are context), finished graphs only on request */
function tgVisible(items,showDone){
  const by=Object.fromEntries(items.map(i=>[i.id,i])),ids=new Set();
  tgComps(items).forEach(c=>{if(showDone||c.some(n=>by[n].status!=="done"))c.forEach(n=>ids.add(n))});
  return ids;
}
function tgChain(items,id){
  const deps=tgDeps(tgLive(items)),kids={};
  for(const k in deps)deps[k].forEach(d=>(kids[d]=kids[d]||[]).push(k));
  const walk=(m,s,o=new Set([s]))=>{(m[s]||[]).forEach(x=>{if(!o.has(x)){o.add(x);walk(m,x,o)}});return o};
  return new Set([...walk(deps,id),...walk(kids,id)]);
}
function tgLayout(items){
  const by=Object.fromEntries(items.map(i=>[i.id,i])),W=190,H=46,GX=70,GY=14,LAB=24,pos={},bands=[];
  let y0=8,wmax=0;
  tgComps(items).forEach((comp,n)=>{
    const deps=tgDeps(comp.map(id=>by[id])),depth={};
    const d=(id,seen=new Set())=>{
      if(depth[id]!=null)return depth[id];
      if(seen.has(id))return 0;seen.add(id);
      return depth[id]=deps[id].length?1+Math.max(...deps[id].map(x=>d(x,seen))):0;
    };
    comp.forEach(id=>d(id));
    const cols=[];comp.forEach(id=>(cols[depth[id]]=cols[depth[id]]||[]).push(id));
    const row={};
    cols.forEach((c,x)=>{
      const bc=id=>deps[id].length?deps[id].reduce((s,k)=>s+(row[k]||0),0)/deps[id].length:0;
      c.sort((a,b)=>bc(a)-bc(b)||a.localeCompare(b));
      c.forEach((id,y)=>{row[id]=y;pos[id]={x:12+x*(W+GX),y:y0+LAB+y*(H+GY)}});
    });
    const rows=Math.max(...cols.map(c=>c.length)),h=LAB+rows*(H+GY);
    const done=comp.filter(id=>by[id].status==="done").length;
    bands.push({y:y0,h:h-GY/2,w:12+cols.length*(W+GX)-GX+12,n:n+1,done,total:comp.length});
    y0+=h+16;wmax=Math.max(wmax,12+cols.length*(W+GX));
  });
  return {pos,bands,W,H,width:Math.max(wmax,200),height:y0};
}
function tgInfoText(){
  const i=tgState.sel&&tgItems[tgState.sel];
  if(!i)return "Hover or click a task to highlight its chain. Ctrl+scroll to zoom, drag to pan.";
  return `<b>${esc(i.id)}</b> ${esc(i.title)}${i.why?` <span class="meta reason">Why: ${esc(i.why)}</span>`:""} <span class="meta">· click again or on empty space to clear</span> ${typeof taskBtns==="function"&&detail?taskBtns(i,detail.running):""}`;
}
function tgPanel(d){
  const P=pref(),all=tgLive(d.backlog_items||[]),vis=tgVisible(all,P.showDone),items=all.filter(i=>vis.has(i.id));
  const by=Object.fromEntries(all.map(i=>[i.id,i])),finished=tgComps(all).filter(c=>c.every(n=>by[n].status==="done")).length;
  tgItems=by;
  if(tgState.sel&&!by[tgState.sel])tgState.sel=null;
  const live=d.activity&&d.activity.now&&d.activity.now.task?d.activity.now.task.split(" ")[0]:null;
  const bar=`<div class="tg-top"><h3>Task graph</h3><span class="meta">${tgComps(items).length} graph(s) · ${items.filter(i=>i.status!=="done").length} open task(s)</span><span class="sp"></span>
    <button class="small" onclick="setPref('showDone',${!P.showDone})" title="Finished graphs are hidden; a finished task is always shown while its graph still has open work">${P.showDone?"Hide":"Show"} finished graphs${finished?` (${finished})`:""}</button>
    ${d.running?"":`<button class="small primary" onclick="doReplan()" title="Think the plan through again as a graph: re-link, merge tasks that belong together, split big ones into steps">Re-plan as graph</button>`}</div>`;
  if(!items.length)return `<div class="panel">${bar}<div class="hint">${all.length?"Everything is finished. Use “Show finished graphs” to see it.":"No tasks yet."}</div></div>`;
  const L=tgLayout(items),ids=new Set(items.map(i=>i.id)),on=tgState.sel?tgChain(items,tgState.sel):null;
  const bands=L.bands.map(b=>`<g><rect x="2" y="${b.y}" width="${Math.max(b.w,260)}" height="${b.h}" rx="10" fill="#ffffff05" stroke="#ffffff10"/><text x="14" y="${b.y+16}" fill="#8a8a8a" font-size="11">Graph ${b.n} · ${b.done}/${b.total} done${b.done===b.total?" · finished":""}</text></g>`).join("");
  const edges=items.flatMap(i=>(i.depends_on||[]).filter(x=>ids.has(x)&&x!==i.id).map(x=>{
    const a=L.pos[x],b=L.pos[i.id],x1=a.x+L.W,y1=a.y+L.H/2,x2=b.x,y2=b.y+L.H/2,m=(x1+x2)/2,hit=!on||(on.has(x)&&on.has(i.id));
    return `<path class="tg-e" data-a="${esc(x)}" data-b="${esc(i.id)}" d="M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}" fill="none" stroke="${on&&hit?"#6fa3d8":"#555"}" stroke-width="1.5" marker-end="url(#tgA)" style="opacity:${hit?1:.12}"/>`;
  })).join("");
  const nodes=items.map(i=>{
    const p=L.pos[i.id],c=TG_COLORS[i.status]||"#888",t=i.title.length>26?i.title.slice(0,25)+"…":i.title,dim=on&&!on.has(i.id);
    return `<g class="tg-n${i.id===live&&i.status==="in_progress"?" tg-live":""}" data-id="${esc(i.id)}" transform="translate(${p.x},${p.y})" style="opacity:${dim?.25:1}${i.id===tgState.sel?";filter:brightness(1.4)":""}"><title>${esc(i.id+" · "+i.status+" · "+i.title+(i.why?"\nWhy: "+i.why:""))}</title><rect width="${L.W}" height="${L.H}" rx="8" fill="#1c1c1c" stroke="${c}" stroke-width="1.5"/><rect width="5" height="${L.H}" rx="2" fill="${c}"/><text x="14" y="19" fill="#aaa" font-size="11">${esc(i.id)} · ${esc(i.status.replace("_"," "))}${i.layer!=null?" · L"+i.layer:""}</text><text x="14" y="36" fill="#eee" font-size="12.5">${esc(t)}</text></g>`;
  }).join("");
  const n=edges?edges.split("<path").length-1:0,z=tgState.z;
  return `<div class="panel">${bar}
  <div class="tg-bar"><button class="btn sm" data-tg="in">+</button><button class="btn sm" data-tg="out">−</button><button class="btn sm" data-tg="fit">Fit</button><button class="btn sm" data-tg="reset">100%</button><span class="hint" id="tgInfo">${tgInfoText()}</span></div>
  <div class="tg-wrap"><svg width="${L.width*z}" height="${L.height*z}" viewBox="0 0 ${L.width} ${L.height}" data-w="${L.width}" data-h="${L.height}"><defs><marker id="tgA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#777"/></marker></defs>${bands}${edges}${nodes}</svg></div>
  <p class="hint" style="margin-top:6px">Arrows point from a task to the tasks that build on it · ${n} link(s). The agent plans, orders and merges its work along this graph.</p></div>`;
}

/* Interactivity (delegated once): chain highlight on hover/click, wheel zoom, drag pan, zoom buttons. */
function tgMark(svg,id){
  const on=id?tgChain(Object.values(tgItems),id):null;
  svg.querySelectorAll(".tg-n").forEach(n=>{n.style.opacity=!on||on.has(n.dataset.id)?1:.25;n.style.filter=n.dataset.id===id?"brightness(1.4)":""});
  svg.querySelectorAll(".tg-e").forEach(e=>{const hit=!on||(on.has(e.dataset.a)&&on.has(e.dataset.b));e.style.opacity=hit?1:.12;e.setAttribute("stroke",on&&hit?"#6fa3d8":"#555")});
}
function tgZoom(svg,z){
  tgState.z=Math.min(3,Math.max(.25,z));
  svg.setAttribute("width",svg.dataset.w*tgState.z);svg.setAttribute("height",svg.dataset.h*tgState.z);
}
function tgInfo(){const i=document.getElementById("tgInfo");if(i)i.innerHTML=tgInfoText()}
document.addEventListener("mouseover",e=>{const n=e.target.closest&&e.target.closest(".tg-n");if(n&&!tgState.sel)tgMark(n.closest("svg"),n.dataset.id)});
document.addEventListener("mouseout",e=>{const n=e.target.closest&&e.target.closest(".tg-n");if(n&&!tgState.sel)tgMark(n.closest("svg"),null)});
document.addEventListener("click",e=>{
  const b=e.target.closest&&e.target.closest("[data-tg]");
  if(b){
    const svg=document.querySelector(".tg-wrap svg"),w=svg.parentElement,k=b.dataset.tg;
    tgZoom(svg,k==="in"?tgState.z*1.25:k==="out"?tgState.z/1.25:k==="reset"?1:Math.min(1,(w.clientWidth-14)/svg.dataset.w));
    return;
  }
  const n=e.target.closest&&e.target.closest(".tg-n"),inWrap=e.target.closest&&e.target.closest(".tg-wrap");
  if(!n){if(inWrap&&tgState.sel){tgState.sel=null;tgMark(inWrap.querySelector("svg"),null);tgInfo()}return}
  const svg=n.closest("svg");tgState.sel=tgState.sel===n.dataset.id?null:n.dataset.id;tgMark(svg,tgState.sel);tgInfo();
});
document.addEventListener("wheel",e=>{
  const w=e.target.closest&&e.target.closest(".tg-wrap");if(!w||!(e.ctrlKey||e.metaKey||e.shiftKey))return;
  e.preventDefault();tgZoom(w.querySelector("svg"),tgState.z*(e.deltaY<0?1.1:1/1.1));
},{passive:false});
let tgDrag=null;
document.addEventListener("mousedown",e=>{const w=e.target.closest&&e.target.closest(".tg-wrap");if(w&&!e.target.closest(".tg-n"))tgDrag={w,x:e.clientX,y:e.clientY,l:w.scrollLeft,t:w.scrollTop}});
document.addEventListener("mousemove",e=>{if(tgDrag){tgDrag.w.scrollLeft=tgDrag.l-(e.clientX-tgDrag.x);tgDrag.w.scrollTop=tgDrag.t-(e.clientY-tgDrag.y)}});
document.addEventListener("mouseup",()=>{tgDrag=null});
