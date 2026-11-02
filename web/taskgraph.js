/* Task dependency graph: layered left-to-right layout (a task sits one column right of its deepest dependency), SVG, no libraries. */
const TG_COLORS={done:"#5fb36b",in_progress:"#6fa3d8",pending:"#d6a23c",blocked:"#e06c75"};
function tgLayout(items){
  const by=Object.fromEntries(items.map(i=>[i.id,i])),depth={};
  const d=(id,seen=new Set())=>{
    if(depth[id]!=null)return depth[id];
    if(seen.has(id))return 0;seen.add(id);
    const deps=((by[id]||{}).depends_on||[]).filter(x=>by[x]);
    return depth[id]=deps.length?1+Math.max(...deps.map(x=>d(x,seen))):0;
  };
  items.forEach(i=>d(i.id));
  const cols=[];items.forEach(i=>(cols[depth[i.id]]=cols[depth[i.id]]||[]).push(i));
  const W=190,H=46,GX=70,GY=14,pos={};
  cols.forEach((c,x)=>(c||[]).forEach((i,y)=>pos[i.id]={x:12+x*(W+GX),y:12+y*(H+GY)}));
  return {pos,W,H,width:12+cols.length*(W+GX),height:12+Math.max(1,...cols.map(c=>(c||[]).length))*(H+GY)};
}
function taskGraphBody(d){
  tgState.sel=null;tgState.z=1;
  const hide=pref().hideDone,all=d.backlog_items||[];
  const items=all.filter(i=>!hide||i.status!=="done");
  if(!items.length)return `<div class="panel"><h3>Task graph</h3><div class="hint">No tasks yet.</div></div>`;
  const L=tgLayout(items),ids=new Set(items.map(i=>i.id));
  const edges=items.flatMap(i=>(i.depends_on||[]).filter(x=>ids.has(x)).map(x=>{
    const a=L.pos[x],b=L.pos[i.id],x1=a.x+L.W,y1=a.y+L.H/2,x2=b.x,y2=b.y+L.H/2,m=(x1+x2)/2;
    return `<path class="tg-e" data-a="${x}" data-b="${i.id}" d="M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}" fill="none" stroke="#555" stroke-width="1.5" marker-end="url(#tgA)"/>`;
  })).join("");
  const nodes=items.map(i=>{
    const p=L.pos[i.id],c=TG_COLORS[i.status]||"#888";
    return `<g class="tg-n" data-id="${esc(i.id)}" data-deps="${esc((i.depends_on||[]).join(","))}" transform="translate(${p.x},${p.y})"><title>${esc(i.id+" · "+i.status+" · "+i.title)}</title><rect width="${L.W}" height="${L.H}" rx="8" fill="#1c1c1c" stroke="${c}" stroke-width="1.5"/><rect width="5" height="${L.H}" rx="2" fill="${c}"/><text x="14" y="19" fill="#aaa" font-size="11">${esc(i.id)} · ${esc(i.status.replace("_"," "))}</text><text x="14" y="36" fill="#eee" font-size="12.5">${esc(i.title.length>26?i.title.slice(0,25)+"…":i.title)}</text></g>`;
  }).join("");
  const n=edges?edges.split("<path").length-1:0;
  return `<div class="panel"><h3>Task graph</h3><p class="hint">Arrows point from a task to the tasks that build on it. ${items.length} tasks, ${n} dependencies.${n?"":" None of the tasks depends on another one yet."}</p>
  <div class="tg-bar"><button class="btn sm" data-tg="in">+</button><button class="btn sm" data-tg="out">−</button><button class="btn sm" data-tg="fit">Fit</button><button class="btn sm" data-tg="reset">100%</button><span class="hint" id="tgInfo">Hover or click a task to highlight its chain. Ctrl+scroll to zoom, drag to pan.</span></div>
  <div class="tg-wrap"><svg width="${L.width}" height="${L.height}" data-w="${L.width}" data-h="${L.height}"><defs><marker id="tgA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#777"/></marker></defs>${edges}${nodes}</svg></div></div>`;
}

/* Interactivity (delegated once): chain highlight on hover/click, wheel zoom, drag pan, zoom buttons. */
const tgState={sel:null,z:1};
function tgChain(svg,id){
  const nodes=[...svg.querySelectorAll(".tg-n")],deps=Object.fromEntries(nodes.map(n=>[n.dataset.id,n.dataset.deps?n.dataset.deps.split(","):[]]));
  const kids={};for(const k in deps)deps[k].forEach(d=>(kids[d]=kids[d]||[]).push(k));
  const walk=(m,s,o=new Set([s]))=>{(m[s]||[]).forEach(x=>{if(!o.has(x)){o.add(x);walk(m,x,o)}});return o};
  return new Set([...walk(deps,id),...walk(kids,id)]);
}
function tgMark(svg,id){
  const on=id?tgChain(svg,id):null;
  svg.querySelectorAll(".tg-n").forEach(n=>{n.style.opacity=!on||on.has(n.dataset.id)?1:.25;n.style.filter=n.dataset.id===id?"brightness(1.4)":""});
  svg.querySelectorAll(".tg-e").forEach(e=>{const hit=!on||(on.has(e.dataset.a)&&on.has(e.dataset.b));e.style.opacity=hit?1:.12;e.setAttribute("stroke",on&&hit?"#6fa3d8":"#555")});
}
function tgZoom(svg,z){
  tgState.z=Math.min(3,Math.max(.25,z));
  svg.setAttribute("width",svg.dataset.w*tgState.z);svg.setAttribute("height",svg.dataset.h*tgState.z);
  svg.setAttribute("viewBox",`0 0 ${svg.dataset.w} ${svg.dataset.h}`);
}
document.addEventListener("mouseover",e=>{const n=e.target.closest&&e.target.closest(".tg-n");if(n&&!tgState.sel)tgMark(n.closest("svg"),n.dataset.id)});
document.addEventListener("mouseout",e=>{const n=e.target.closest&&e.target.closest(".tg-n");if(n&&!tgState.sel)tgMark(n.closest("svg"),null)});
document.addEventListener("click",e=>{
  const b=e.target.closest&&e.target.closest("[data-tg]");
  if(b){
    const svg=document.querySelector(".tg-wrap svg"),w=svg.parentElement,k=b.dataset.tg;
    tgZoom(svg,k==="in"?tgState.z*1.25:k==="out"?tgState.z/1.25:k==="reset"?1:Math.min(1,(w.clientWidth-14)/svg.dataset.w));
    return;
  }
  const n=e.target.closest&&e.target.closest(".tg-n");
  if(!n){if(e.target.closest&&e.target.closest(".tg-wrap")&&tgState.sel){tgState.sel=null;tgMark(document.querySelector(".tg-wrap svg"),null);const i=document.getElementById("tgInfo");if(i)i.textContent="Hover or click a task to highlight its chain. Ctrl+scroll to zoom, drag to pan."}return}
  const svg=n.closest("svg");tgState.sel=tgState.sel===n.dataset.id?null:n.dataset.id;tgMark(svg,tgState.sel);
  const i=document.getElementById("tgInfo");if(i)i.textContent=tgState.sel?n.querySelector("title").textContent+" — click again or on empty space to clear":"";
});
document.addEventListener("wheel",e=>{
  const w=e.target.closest&&e.target.closest(".tg-wrap");if(!w||!(e.ctrlKey||e.metaKey||e.shiftKey))return;
  e.preventDefault();tgZoom(w.querySelector("svg"),tgState.z*(e.deltaY<0?1.1:1/1.1));
},{passive:false});
let tgDrag=null;
document.addEventListener("mousedown",e=>{const w=e.target.closest&&e.target.closest(".tg-wrap");if(w&&!e.target.closest(".tg-n"))tgDrag={w,x:e.clientX,y:e.clientY,l:w.scrollLeft,t:w.scrollTop}});
document.addEventListener("mousemove",e=>{if(tgDrag){tgDrag.w.scrollLeft=tgDrag.l-(e.clientX-tgDrag.x);tgDrag.w.scrollTop=tgDrag.t-(e.clientY-tgDrag.y)}});
document.addEventListener("mouseup",()=>{tgDrag=null});
