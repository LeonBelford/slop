/* Collapsible chat docked next to the project view, so the user can talk to the agent while looking at tasks, code or logs. */
async function sideOpen(){
  if(view!=="project"||!sel)return;
  try{
    const c=await api("/api/project/"+encodeURIComponent(sel)+"/chat"),r=await api("/api/chats");
    chat.dock=true;chat.list=r.chats;chat.models=r.installed_models;chat.def=r.default_model;
    chat.cur=await api("/api/chat/"+c.id);chat.loop=0;chat.listOpen=false;
    document.body.classList.add("dock-on");localStorage.setItem("cf.dock","1");
    drawChat();
  }catch(e){toast(e.message,true)}
}
function sideClose(){
  chat.dock=false;clearInterval(chat.poll);clearInterval(chat.mpoll);chat.ctrl&&chat.ctrl.abort();
  document.body.classList.remove("dock-on");$("#dock").innerHTML="";localStorage.removeItem("cf.dock");
}
const DOCK_MIN=320;
function dockWidth(w){
  const max=Math.round(innerWidth*0.8);w=Math.max(DOCK_MIN,Math.min(max,Math.round(w)));
  document.documentElement.style.setProperty("--dock-w",w+"px");return w;
}
(()=>{const w=+localStorage.getItem("cf.dockw");if(w)dockWidth(w)})();
function dockResize(e){
  e.preventDefault();const h=e.currentTarget;h.classList.add("on");document.body.style.userSelect="none";
  const mv=ev=>dockWidth(innerWidth-ev.clientX),up=()=>{
    removeEventListener("pointermove",mv);removeEventListener("pointerup",up);h.classList.remove("on");document.body.style.userSelect="";
    localStorage.setItem("cf.dockw",String(Math.round($("#dock").getBoundingClientRect().width)));
  };
  addEventListener("pointermove",mv);addEventListener("pointerup",up);
}
function sideToggle(){chat.dock?sideClose():sideOpen()}
async function sideNew(){
  try{chat.cur=await api("/api/chats",{projects:[sel]});chat.list=(await api("/api/chats")).chats;chat.loop=0;drawChat()}catch(e){toast(e.message,true)}
}
document.addEventListener("keydown",e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==="j"&&view==="project"){e.preventDefault();sideToggle()}
});
