/* File graph tab: every file as a bubble, a line = "imports", fill = top folder, size = importers + tasks. Hover card shows the tasks that
   touched the file (the 4 newest written out, then only a count). Small force layout, drag / pan / zoom, search. */
const fg={pos:{},view:{x:0,y:0,k:1},sel:null,hover:null,find:"",sig:"",alpha:0,raf:0,drag:null,pan:null,m:null,proj:null,fitted:false,off:{}};
const FG_PAL=["#6fa3d8","#b48ead","#d6a23c","#5fb3a8","#e06c75","#9ccc65","#f0a1a8","#8fbcbb","#f0a45d","#7aa2f7"];
const fgDir=p=>p.includes("/")?p.split("/")[0]:"(root)";
const fgCol=p=>{const k=fgDir(p);return FG_PAL[pgHash(k)%FG_PAL.length]};
const fgR=n=>6+Math.min(14,n.used_by*2+Math.min(n.total,6));
const fgName=p=>p.split("/").pop();
const FG_K={import:{l:"imports",c:"#7fb2ff",d:"",w:1.5,f:.05},test:{l:"test ↔ code",c:"#5fd08c",d:"5 3",w:1.4,f:.05},mention:{l:"mentions",c:"#e0b050",d:"2 3",w:1,f:.015},module:{l:"same module",c:"#b48ead",d:"",w:1,f:.025},task:{l:"changed together",c:"#f07aa8",d:"6 3",w:1.2,f:.025}};
const fgShown=k=>!fg.off[k];
const fgEdge=e=>({a:e[0],b:e[1],k:e[2]||"import",w:e[3]||1});
function fgLinks(p,k){const nb=fg.m.nb[p]||{};return nb[k]||{out:[],in:[]}}

function fgBody(d){
  const x=extra&&extra.kind==="files"?extra:null;
  if(!x)return `<div class="hint">Loading…</div>`;
  if(fg.proj!==sel){fg.proj=sel;fg.pos={};fg.fitted=false;fg.sel=null;fg.find=""}
  const by=Object.fromEntries(x.nodes.map(n=>[n.path,n])),nb={},edges=x.edges.map(fgEdge).filter(e=>by[e.a]&&by[e.b]);
  edges.forEach(e=>{const o=nb[e.a]=nb[e.a]||{},i=nb[e.b]=nb[e.b]||{};(o[e.k]=o[e.k]||{out:[],in:[]}).out.push(e.b);(i[e.k]=i[e.k]||{out:[],in:[]}).in.push(e.a)});
  fg.m={nodes:x.nodes,edges,by,nb,count:edges.reduce((c,e)=>(c[e.k]=(c[e.k]||0)+1,c),{})};
  fg.sig=x.nodes.length+"|"+x.edges.length;
  setTimeout(fgStart,0);
  const dirs=[...new Set(x.nodes.map(n=>fgDir(n.path)))].sort();
  return `<div class="panel"><h3>File graph <span class="meta">${x.nodes.length} file(s), ${fg.m.edges.length} link(s)</span></h3>
  <p class="hint">What belongs together and why. Every line has a kind (click a kind to hide or show it). Big bubble = used by many files or touched by many tasks. Hover for the newest tasks, click a bubble for all its connections.</p>
  <div class="tg-bar"><button class="btn sm" onclick="fgZoom(1.25)">+</button><button class="btn sm" onclick="fgZoom(.8)">−</button><button class="btn sm" onclick="fgFit()">Fit</button><button class="btn sm" onclick="fgKick()">Relax</button>
  <input class="small" style="width:210px" placeholder="Search file or task…" value="${esc(fg.find)}" oninput="fg.find=this.value;fgMark()">
  <span class="pg-stages" id="fgKinds">${fgKindChips()}</span>
  <span class="pg-stages">${dirs.map(k=>`<span class="pg-chip" style="--c:${fgCol(k+"/x")}"><i></i>${esc(k)}</span>`).join("")}</span></div>
  <div class="pg-wrap"><svg id="fgSvg" class="z-mid"><defs><marker id="fgA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#7fb2ff"/></marker></defs><g id="fgRoot">
  ${fg.m.edges.map(e=>{const K=FG_K[e.k]||FG_K.import;return `<path class="pg-e k-${e.k}" data-a="${esc(e.a)}" data-b="${esc(e.b)}" data-k="${e.k}" style="stroke:${K.c};stroke-width:${(K.w+(e.k==="task"?Math.min(e.w,4)*.6:0)).toFixed(1)};${K.d?"stroke-dasharray:"+K.d:""}"${e.k==="import"?' marker-end="url(#fgA)"':""}><title>${esc(e.a)} — ${esc(e.b)}: ${K.l}${e.k==="task"?" ("+e.w+" task(s))":""}</title></path>`}).join("")}
  ${x.nodes.map(n=>`<g class="pg-n" data-id="${esc(n.path)}"><circle r="${fgR(n)}" fill="${fgCol(n.path)}" fill-opacity=".8" stroke="${n.total?"#ffffffa0":"#ffffff30"}" stroke-width="${n.total?2:1}"/><text class="pg-l" y="${fgR(n)+13}" text-anchor="middle">${esc(fgName(n.path))}</text></g>`).join("")}
  </g></svg></div><div id="fgInfo">${fgInfoText()}</div></div>`;
}
function fgKindChips(){return Object.keys(FG_K).filter(k=>fg.m.count[k]).map(k=>`<span class="pg-chip${fgShown(k)?"":" off"}" style="--c:${FG_K[k].c}" onclick="fgToggle('${k}')" title="Show or hide this kind of link"><i></i>${FG_K[k].l} ${fg.m.count[k]}</span>`).join("")}
function fgToggle(k){fg.off[k]=!fg.off[k];const c=document.getElementById("fgKinds");if(c)c.innerHTML=fgKindChips();fgMark();fg.alpha=Math.max(fg.alpha,.4);if(!fg.raf)fg.raf=requestAnimationFrame(fgLoop)}
function fgTaskLine(n){
  if(!n.total)return '<span class="meta">no task touched it yet</span>';
  return n.tasks.map(t=>`<span class="pg-ch" title="${esc(t.title)} · ${esc(t.status)}">${esc(t.id)} ${esc(t.title.length>34?t.title.slice(0,33)+"…":t.title)}</span>`).join("")+(n.more?` <span class="pill info" title="older tasks that touched this file">+${n.more} more</span>`:"");
}
const FG_ROWS=[["import","out","Imports"],["import","in","Used by"],["test","out","Tests for"],["test","in","Tested by"],["module","out","Contains"],["module","in","Part of module"],["mention","out","Mentions"],["mention","in","Mentioned in"],["task","both","Changed together with"]];
function fgNeigh(p){const nb=fg.m.nb[p]||{},out=new Set();Object.keys(nb).forEach(k=>{if(fgShown(k)){nb[k].out.forEach(x=>out.add(x));nb[k].in.forEach(x=>out.add(x))}});return out}
function fgInfoText(){
  const n=fg.m&&fg.sel&&fg.m.by[fg.sel];
  if(!n)return `<span class="hint">Click a bubble: what it imports, what uses it, its tests, mentions, folder and the tasks that touched it.</span>`;
  const chip=p=>`<span class="pg-ch file" onclick="fgSelect('${esc(p)}')">${esc(p)}</span>`;
  const rows=FG_ROWS.map(([k,dir,label])=>{const l=fgLinks(n.path,k),list=dir==="both"?[...l.out,...l.in]:l[dir];return list.length?`<div><span class="meta" style="color:${FG_K[k].c}">${label}</span> ${list.slice(0,12).map(chip).join("")}${list.length>12?` <span class="pill info">+${list.length-12}</span>`:""}</div>`:""}).join("");
  return `<div class="pg-info"><div><b>${esc(n.path)}</b> <span class="pill info">${n.total} task(s)</span> <span class="pill info">used by ${n.used_by}</span> <span class="pill info">${n.lines==null?"?":n.lines} lines</span></div>
  <div class="pg-rows"><div><span class="meta">Tasks</span> ${fgTaskLine(n)}</div>${rows||'<div class="meta">No connection found for this file yet.</div>'}</div>
  <div class="tg-row" style="margin-top:6px"><button class="small" onclick="fgOpen('${esc(n.path)}')">Open in editor</button></div></div>`;
}
function fgCounts(p){const nb=fg.m.nb[p]||{},t=Object.keys(nb).map(k=>[k,nb[k].out.length+nb[k].in.length]).filter(x=>x[1]);return t.length?t.map(([k,c])=>`<span style="color:${FG_K[k].c}">${c} ${FG_K[k].l}</span>`).join(" · "):"no connection found"}
function fgOpen(p){setTab("changes");setTimeout(()=>{if(typeof openFile==="function")openFile(p)},600)}
function fgSelect(p){fg.sel=fg.sel===p?null:p;const i=document.getElementById("fgInfo");if(i)i.innerHTML=fgInfoText();fgMark()}
function fgSeed(){fg.m.nodes.forEach(n=>{if(!fg.pos[n.path]){const h=pgHash(n.path);fg.pos[n.path]={x:(h%700)-350,y:((h>>6)%500)-250,vx:0,vy:0}}})}
function fgTick(){
  const N=fg.m.nodes,P=fg.pos,a=fg.alpha;
  N.forEach(n=>{const p=P[n.path];p.fx=-p.x*.006;p.fy=-p.y*.006});
  for(let i=0;i<N.length;i++)for(let j=i+1;j<N.length;j++){
    const p=P[N[i].path],q=P[N[j].path];let dx=p.x-q.x,dy=p.y-q.y,d2=dx*dx+dy*dy;if(d2>250000)continue;if(d2<1){dx=Math.random()-.5;dy=Math.random()-.5;d2=1}
    const d=Math.sqrt(d2),f=5200/d2;p.fx+=dx/d*f;p.fy+=dy/d*f;q.fx-=dx/d*f;q.fy-=dy/d*f;
  }
  fg.m.edges.forEach(e=>{if(!fgShown(e.k))return;const p=P[e.a],q=P[e.b],dx=q.x-p.x,dy=q.y-p.y,d=Math.sqrt(dx*dx+dy*dy)||1,f=(d-110)*FG_K[e.k].f*(e.k==="task"?1+Math.min(e.w,4)*.3:1);p.fx+=dx/d*f;p.fy+=dy/d*f;q.fx-=dx/d*f;q.fy-=dy/d*f});
  N.forEach(n=>{const p=P[n.path];if(p.pin)return;p.vx=(p.vx+p.fx*a)*.6;p.vy=(p.vy+p.fy*a)*.6;p.x+=p.vx;p.y+=p.vy});
  fg.alpha*=.985;
}
function fgPlace(){
  const svg=document.getElementById("fgSvg");if(!svg||!fg.m)return;
  svg.querySelectorAll(".pg-n").forEach(g=>{const p=fg.pos[g.dataset.id];if(p)g.setAttribute("transform",`translate(${p.x.toFixed(1)},${p.y.toFixed(1)})`)});
  svg.querySelectorAll(".pg-e").forEach(e=>{
    const a=fg.pos[e.dataset.a],b=fg.pos[e.dataset.b];if(!a||!b)return;
    const ra=fgR(fg.m.by[e.dataset.a]),rb=fgR(fg.m.by[e.dataset.b])+3,dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1;
    e.setAttribute("d",`M${(a.x+dx/d*ra).toFixed(1)},${(a.y+dy/d*ra).toFixed(1)} L${(b.x-dx/d*rb).toFixed(1)},${(b.y-dy/d*rb).toFixed(1)}`);
  });
  fgApply(svg);
}
function fgApply(svg){const v=fg.view;svg=svg||document.getElementById("fgSvg");if(!svg)return;svg.querySelector("#fgRoot").setAttribute("transform",`translate(${v.x.toFixed(1)},${v.y.toFixed(1)}) scale(${v.k.toFixed(3)})`);svg.setAttribute("class",v.k<.7?"z-lo":v.k>1.3?"z-hi":"z-mid")}
function fgLoop(){fg.raf=0;if(!document.getElementById("fgSvg"))return;fgTick();fgTick();fgPlace();if(fg.alpha>.03||fg.drag)fg.raf=requestAnimationFrame(fgLoop)}
function fgKick(){fg.alpha=Math.max(fg.alpha,.4);if(!fg.raf)fg.raf=requestAnimationFrame(fgLoop)}
function fgFit(){
  const svg=document.getElementById("fgSvg");if(!svg||!fg.m||!fg.m.nodes.length)return;
  const cut=a=>{const v=a.slice().sort((x,y)=>x-y),n=v.length,lo=v[Math.floor(n*.04)],hi=v[Math.ceil(n*.96)-1];return [lo,hi]};  // a few stray files must not shrink the picture
  const ps=fg.m.nodes.map(n=>fg.pos[n.path]),[xa,xb]=cut(ps.map(p=>p.x)),[ya,yb]=cut(ps.map(p=>p.y)),x0=xa-50,x1=xb+50,y0=ya-50,y1=yb+60;
  const w=svg.clientWidth||900,h=svg.clientHeight||560,k=Math.max(.25,Math.min(1.4,w/(x1-x0),h/(y1-y0)));
  fg.view={k,x:(w-(x1-x0)*k)/2-x0*k,y:(h-(y1-y0)*k)/2-y0*k};fgApply(svg);
}
function fgStart(){
  const svg=document.getElementById("fgSvg");if(!svg||!fg.m)return;
  fgSeed();
  if(!fg.fitted){fg.alpha=1;for(let k=0;k<200;k++)fgTick();fg.alpha=.1}
  fgPlace();if(!fg.fitted){fgFit();fg.fitted=true}
  fgMark();
}
function fgZoom(f){const svg=document.getElementById("fgSvg");if(!svg)return;const v=fg.view,w=svg.clientWidth/2,h=svg.clientHeight/2,k=Math.min(3,Math.max(.15,v.k*f));fg.view={k,x:w-(w-v.x)/v.k*k,y:h-(h-v.y)/v.k*k};fgApply(svg)}
function fgMark(){
  const svg=document.getElementById("fgSvg");if(!svg||!fg.m)return;
  const f=fg.find.toLowerCase().trim(),focus=fg.sel||fg.hover,keep=focus?new Set([focus,...fgNeigh(focus)]):null;
  const hit=f?new Set(fg.m.nodes.filter(n=>(n.path+" "+n.tasks.map(t=>t.title).join(" ")).toLowerCase().includes(f)).map(n=>n.path)):null;
  const on=p=>(!keep||keep.has(p))&&(!hit||hit.has(p));
  svg.querySelectorAll(".pg-n").forEach(g=>{g.classList.toggle("dim",!on(g.dataset.id));g.classList.toggle("sel",g.dataset.id===fg.sel)});
  svg.querySelectorAll(".pg-e").forEach(e=>{const lit=on(e.dataset.a)&&on(e.dataset.b);e.classList.toggle("off",!fgShown(e.dataset.k));e.classList.toggle("dim",!lit);e.classList.toggle("lit",!!focus&&lit)});
}
function fgCard(id,e){
  const n=fg.m&&fg.m.by[id],wrap=document.querySelector("#fgSvg")&&document.querySelector("#fgSvg").parentNode;if(!n||!wrap)return;
  let c=document.getElementById("fgCard");if(!c){c=document.createElement("div");c.id="fgCard";c.className="pgcard";wrap.appendChild(c)}
  if(c.dataset.id!==id){c.dataset.id=id;c.innerHTML=`<div class="pc-h"><i style="background:${fgCol(id)}"></i><b>${esc(id)}</b></div><div class="meta">${n.lines==null?"":n.lines+" lines · "}Used by ${n.used_by} file(s) · ${n.total} task(s)</div><div class="meta">${fgCounts(id)}</div><div style="margin-top:4px">${fgTaskLine(n)}</div>`}
  const r=wrap.getBoundingClientRect(),x=e.clientX-r.left+16,y=e.clientY-r.top+16;
  c.style.display="block";c.style.left=Math.max(4,Math.min(x,r.width-c.offsetWidth-4))+"px";c.style.top=Math.max(4,Math.min(y,r.height-c.offsetHeight-4))+"px";
}
function fgCardHide(){const c=document.getElementById("fgCard");if(c){c.style.display="none";c.dataset.id=""}}
const fgWorld=(svg,e)=>{const r=svg.getBoundingClientRect(),v=fg.view;return{x:(e.clientX-r.left-v.x)/v.k,y:(e.clientY-r.top-v.y)/v.k}};
document.addEventListener("mousedown",e=>{
  const svg=e.target.closest&&e.target.closest("#fgSvg");if(!svg)return;const n=e.target.closest(".pg-n");
  if(n)fg.drag={id:n.dataset.id,sx:e.clientX,sy:e.clientY,moved:false};else fg.pan={sx:e.clientX,sy:e.clientY,x:fg.view.x,y:fg.view.y,moved:false};
});
document.addEventListener("mousemove",e=>{
  const svg=document.getElementById("fgSvg");if(!svg)return;
  if(fg.drag){const d=fg.drag;if(!d.moved&&Math.hypot(e.clientX-d.sx,e.clientY-d.sy)<4)return;d.moved=true;const p=fg.pos[d.id],w=fgWorld(svg,e);if(!p)return;p.x=w.x;p.y=w.y;p.vx=p.vy=0;p.pin=true;fgCardHide();fg.alpha=Math.max(fg.alpha,.25);if(!fg.raf)fg.raf=requestAnimationFrame(fgLoop)}
  else if(fg.pan){const a=fg.pan;if(Math.hypot(e.clientX-a.sx,e.clientY-a.sy)>3)a.moved=true;fg.view.x=a.x+e.clientX-a.sx;fg.view.y=a.y+e.clientY-a.sy;fgApply(svg)}
  else if(fg.hover)fgCard(fg.hover,e);
});
document.addEventListener("mouseup",()=>{
  const d=fg.drag,a=fg.pan;fg.drag=fg.pan=null;
  if(d){const p=fg.pos[d.id];if(p)p.pin=false;if(!d.moved)fgSelect(d.id)}else if(a&&!a.moved&&fg.sel)fgSelect(fg.sel);
});
document.addEventListener("mouseover",e=>{const n=e.target.closest&&e.target.closest("#fgSvg .pg-n");if(n&&!fg.drag&&fg.hover!==n.dataset.id){fg.hover=n.dataset.id;fgMark();fgCard(n.dataset.id,e)}});
document.addEventListener("mouseout",e=>{const n=e.target.closest&&e.target.closest("#fgSvg .pg-n");if(n&&fg.hover){fg.hover=null;fgMark();fgCardHide()}});
document.addEventListener("wheel",e=>{const svg=e.target.closest&&e.target.closest("#fgSvg");if(!svg)return;e.preventDefault();
  const r=svg.getBoundingClientRect(),v=fg.view,k=Math.min(3,Math.max(.15,v.k*(e.deltaY<0?1.1:1/1.1))),mx=e.clientX-r.left,my=e.clientY-r.top;fg.view={k,x:mx-(mx-v.x)/v.k*k,y:my-(my-v.y)/v.k*k};fgApply(svg)},{passive:false});
