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
  const hide=pref().hideDone,all=d.backlog_items||[];
  const items=all.filter(i=>!hide||i.status!=="done");
  if(!items.length)return `<div class="panel"><h3>Task graph</h3><div class="hint">No tasks yet.</div></div>`;
  const L=tgLayout(items),ids=new Set(items.map(i=>i.id));
  const edges=items.flatMap(i=>(i.depends_on||[]).filter(x=>ids.has(x)).map(x=>{
    const a=L.pos[x],b=L.pos[i.id],x1=a.x+L.W,y1=a.y+L.H/2,x2=b.x,y2=b.y+L.H/2,m=(x1+x2)/2;
    return `<path d="M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}" fill="none" stroke="#555" stroke-width="1.5" marker-end="url(#tgA)"/>`;
  })).join("");
  const nodes=items.map(i=>{
    const p=L.pos[i.id],c=TG_COLORS[i.status]||"#888";
    return `<g transform="translate(${p.x},${p.y})"><title>${esc(i.id+" · "+i.status+" · "+i.title)}</title><rect width="${L.W}" height="${L.H}" rx="8" fill="#1c1c1c" stroke="${c}" stroke-width="1.5"/><rect width="5" height="${L.H}" rx="2" fill="${c}"/><text x="14" y="19" fill="#aaa" font-size="11">${esc(i.id)} · ${esc(i.status.replace("_"," "))}</text><text x="14" y="36" fill="#eee" font-size="12.5">${esc(i.title.length>26?i.title.slice(0,25)+"…":i.title)}</text></g>`;
  }).join("");
  const n=edges?edges.split("<path").length-1:0;
  return `<div class="panel"><h3>Task graph</h3><p class="hint">Arrows point from a task to the tasks that build on it. ${items.length} tasks, ${n} dependencies.${n?"":" None of the tasks depends on another one yet."}</p>
  <div class="tg-wrap"><svg width="${L.width}" height="${L.height}"><defs><marker id="tgA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#777"/></marker></defs>${edges}${nodes}</svg></div></div>`;
}
