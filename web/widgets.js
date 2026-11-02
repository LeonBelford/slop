/* Widget board: tiles with drag-and-drop order, hide, size (1 or 2 columns). The layout is kept per board in localStorage and can be
   shared as a link (?lb=<board>&lay=<id>.<size>,!<hidden id>,...). */
const WBS={};
function wbParse(s,defs){
  const st={order:[],hide:{},size:{}};
  s.split(",").forEach(t=>{const hid=t[0]==="!",[id,sz]=(hid?t.slice(1):t).split(".");if(!defs[id])return;st.order.push(id);if(hid)st.hide[id]=true;if(sz==="2")st.size[id]=2});
  return st;
}
function wbInit(key,defs,repaint){
  let st=null;try{st=JSON.parse(localStorage.getItem("wb-"+key)||"null")}catch(e){}
  const u=new URLSearchParams(location.search);
  if(!WBS[key]&&u.get("lb")===key&&u.get("lay"))st=wbParse(u.get("lay"),defs);
  if(WBS[key])st=WBS[key].st;
  st=st||{order:[],hide:{},size:{}};
  st.order=[...st.order.filter(k=>defs[k]),...Object.keys(defs).filter(k=>!st.order.includes(k))];
  WBS[key]={defs,st,repaint};return WBS[key];
}
function wbCode(b){return b.st.order.map(id=>(b.st.hide[id]?"!":"")+id+(b.st.size[id]===2?".2":"")).join(",")}
function wbSave(key){const b=WBS[key];localStorage.setItem("wb-"+key,JSON.stringify(b.st));b.repaint()}
function wbHide(key,id,on){WBS[key].st.hide[id]=on;wbSave(key)}
function wbSize(key,id){const s=WBS[key].st.size;s[id]=s[id]===2?1:2;wbSave(key)}
function wbReset(key){WBS[key].st={order:Object.keys(WBS[key].defs),hide:{},size:{}};wbSave(key)}
function wbLink(key){
  const u=new URL(location.href);u.searchParams.set("lb",key);u.searchParams.set("lay",wbCode(WBS[key]));
  const t=u.toString();(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast("Layout link copied")).catch(()=>prompt("Copy this link",t));
}
let WBDRAG=null;
function wbDrag(e,key,id){WBDRAG={key,id};e.dataTransfer.effectAllowed="move";e.dataTransfer.setData("text/plain",id)}
function wbDrop(e,key,id){
  e.preventDefault();if(!WBDRAG||WBDRAG.key!==key||WBDRAG.id===id)return;
  const o=WBS[key].st.order;o.splice(o.indexOf(WBDRAG.id),1);o.splice(o.indexOf(id),0,WBDRAG.id);WBDRAG=null;wbSave(key);
}
function wbMenu(key){
  const b=WBS[key];
  return `<details class="dmenu" data-k="wbmenu"><summary>Customize ▾</summary><div class="dpop"><b>Widgets (drag a tile by its ⠿ to reorder)</b>${Object.entries(b.defs).map(([id,d])=>
    `<label><input type="checkbox" ${b.st.hide[id]?"":"checked"} onchange="wbHide('${key}','${id}',!this.checked)"> ${esc(d.title)}</label>`).join("")}
    <button class="small" onclick="wbReset('${key}')">Reset layout</button> <button class="small" onclick="wbLink('${key}')">Copy layout link</button></div></details>`;
}
/* Tiles whose body is empty are left out, so a quiet project does not show empty boxes. */
function wbTiles(key,ctx){
  const b=WBS[key];
  return `<div class="wb">${b.st.order.filter(id=>!b.st.hide[id]).map(id=>{
    const d=b.defs[id],body=d.body(ctx);if(!body)return "";
    const wide=(b.st.size[id]||d.size||1)===2;
    return `<div class="panel wt ${wide?"wide":""}" draggable="true" ondragstart="wbDrag(event,'${key}','${id}')" ondragover="event.preventDefault()" ondrop="wbDrop(event,'${key}','${id}')">
      <h3><span class="grip" title="Drag to reorder">⠿</span> ${esc(d.title)}<span class="grow"></span><button class="small ghost" title="Wider / narrower" onclick="wbSize('${key}','${id}')">${wide?"⇤":"⇥"}</button><button class="small ghost" title="Hide" onclick="wbHide('${key}','${id}',true)">✕</button></h3>${body}</div>`}).join("")}</div>`;
}
