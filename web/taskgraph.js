/* Shared graph helpers (links, chains, band layout for the history replay). The project graph itself is in projgraph.js. */
const TG_COLORS={done:"#5fb36b",in_progress:"#6fa3d8",pending:"#d6a23c",blocked:"#e06c75"};
const tgState={sel:null,z:1,mode:null,edge:null};
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
    bands.push({y:y0,h:h-GY/2,w:12+cols.length*(W+GX)-GX+12,n:n+1,done,total:comp.length,ids:comp});
    y0+=h+16;wmax=Math.max(wmax,12+cols.length*(W+GX));
  });
  return {pos,bands,W,H,width:Math.max(wmax,200),height:y0};
}
