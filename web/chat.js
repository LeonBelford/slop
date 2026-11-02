/* ---- chat: conversations, agent mode with action cards, slash commands (own DOM, never re-rendered by the poll so streaming is not interrupted) ---- */
let chat={list:[],models:[],def:"",cur:null,ctrl:null,q:"",brief:{},loop:0,listOpen:false,poll:null};
const RO=["list_dir","read_docs","status","search_code","list_tasks","show_task","read_file","health","timeline"];
const ICON={list_dir:"📂",read_docs:"📚",edit_file:"✏️",delete_file:"🗑️",status:"📊",search_code:"🔎",list_tasks:"📋",read_file:"📄",show_task:"🔍",reopen_task:"↩",delete_task:"🗑",edit_task:"✏",feedback:"💬",design:"📐",run_command:"💻",health:"❤️",timeline:"🕒",add_task:"➕",start:"▶",stop:"⏹",pause:"⏸",resume:"⏯",step_mode:"👣",run_next:"⏭",set_priority:"⬆",cancel_task:"✖",hint:"📌",generate_tests:"🧪",create_project:"🆕",write_note:"📝",make_folder:"📁",write_file:"💾"};
const CMDS=[
  ["status","","Agent state and task counts",a=>({tool:"status"})],
  ["tasks","[open|doing|done|blocked]","List tasks with their ids",a=>({tool:"list_tasks",filter:a})],
  ["task","<what to build>","Queue a task for the agent",a=>({tool:"add_task",text:a})],
  ["start","","Start the agent",a=>({tool:"start"})],
  ["stop","","Stop the agent after the current task",a=>({tool:"stop"})],
  ["pause","","Pause between tasks",a=>({tool:"pause"})],
  ["resume","","Resume a paused agent",a=>({tool:"resume"})],
  ["step","on|off","Step mode: wait after every task",a=>({tool:"step_mode",on:a!=="off"})],
  ["next","<T0012>","Make this task the next one",a=>({tool:"run_next",task:a})],
  ["top","<T0012>","Move a task to the top of the queue",a=>({tool:"set_priority",task:a,priority:"top"})],
  ["cancel","<T0012>","Cancel a task",a=>({tool:"cancel_task",task:a})],
  ["show","<T0012>","Show one task in detail",a=>({tool:"show_task",task:a})],
  ["reopen","<T0012>","Put a blocked/cancelled task back",a=>({tool:"reopen_task",task:a})],
  ["design","approve|reject","Answer the design waiting for review",a=>({tool:"design",approve:a!=="reject"})],
  ["run","<command>","Run a command in the sandbox copy",a=>({tool:"run_command",command:a})],
  ["note","<text>","Add a remark to the feedback list",a=>({tool:"feedback",text:a})],
  ["hint","<text>","Pin a hint into the current task",a=>({tool:"hint",text:a})],
  ["tests","<file>","Queue a task that writes tests for a file",a=>({tool:"generate_tests",file:a})],
  ["mkdir","<path>","Create an (empty) folder in the repo",a=>({tool:"make_folder",path:a})],
  ["jot","<text>","Append a note to notes.md, kept outside the pipeline",a=>({tool:"write_note",text:a})],
  ["search","<symbol>","Search symbols in the code",a=>({tool:"search_code",query:a})],
  ["file","<path>","Show a file of the repo",a=>({tool:"read_file",path:a})],
  ["health","","Health score",a=>({tool:"health"})],
  ["timeline","","What happened lately",a=>({tool:"timeline"})],
];
function mdInline(t){
  return esc(t).replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g,"<b>$1</b>").replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,"$1<i>$2</i>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function mdLite(t){
  return t.split(/```/).map((p,i)=>{
    if(i%2){const lang=(p.match(/^([\w+-]*)\n/)||[])[1]||"";return `<div class="cb"><div class="cb-h"><span>${esc(lang||"code")}</span><button class="small ghost" onclick="copyCode(this)">Copy</button></div><pre>${esc(p.replace(/^[^\n]*\n/,""))}</pre></div>`}
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
function copyCode(b){navigator.clipboard.writeText(b.closest(".cb").querySelector("pre").textContent).then(()=>toast("Copied"),()=>toast("Copy not allowed here",true))}
const stripActions=t=>t.replace(/```action[ \t]*\n[\s\S]*?```/g,"").replace(/```action[\s\S]*$/,"").trim();
function taskBlocks(t){return[...t.matchAll(/```task[ \t]*\n([\s\S]*?)```/g)].map(m=>m[1].trim()).filter(x=>x.length>=10)}
function actionBlocks(t){return[...t.matchAll(/```action[ \t]*\n([\s\S]*?)```/g)].map(m=>{try{const j=JSON.parse(m[1].trim());return j&&typeof j==="object"?j:null}catch(e){return null}}).filter(Boolean)}
const hhmm=t=>t?new Date(t*1000).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}):"";
const isAgent=c=>!!(c&&c.agent&&c.projects.length&&!c.tool);
const isDirect=c=>isAgent(c)&&c.route==="direct";
const phText=c=>(isDirect(c)?"Tell me what to build or change: I read the docs and edit the project myself":isAgent(c)?"Tell the agent what to do, or type / for commands":"Message CodeFactory…")+"  (Enter sends, Shift+Enter: new line)";

/* ---- loading and the frame ---- */
async function showChat(id,projects){
  closeSb();view="chat";tab="chat";dirty=false;extra=null;lastBody=lastHead=lastTabs="";renderList();
  $("#now").innerHTML="";$("#now").className="";$("#tabs").innerHTML="";$("#head").innerHTML="";
  try{
    const r=await api("/api/chats");chat.list=r.chats;chat.models=r.installed_models;chat.def=r.default_model;
    if(projects){chat.cur=await api("/api/chats",{projects})}
    else if(id){chat.cur=await api("/api/chat/"+id)}
    else if(chat.cur&&chat.list.some(c=>c.id===chat.cur.id)){chat.cur=await api("/api/chat/"+chat.cur.id)}
    else chat.cur=chat.list[0]?await api("/api/chat/"+chat.list[0].id):null;
    if(projects)chat.list=(await api("/api/chats")).chats;
  }catch(e){toast(e.message,true)}
  chat.loop=0;chat.listOpen=false;drawChat();
}
const SUGGEST_LINKED=["What should we build next?","Summarize the current state of the project","Which open tasks are the riskiest, and why?"];
const SUGGEST_TOOL=["How does the self-improve pipeline work, and where could it be better?","Look at web/app.js and suggest the safest small refactor","What failed in the last self-improve run?"];
const SUGGEST_AGENT=["Show me the state and the open tasks","Which task should run next, and why?","Check the project health and queue the most valuable fix"];
const SUGGEST_FREE=["Help me turn a rough idea into a project plan","Compare two tech stacks for a small web app","Review my architecture idea and find weak spots"];
function drawChat(){
  const c=chat.cur,b=$("#body");b.dataset.tab="chat";
  b.innerHTML=`<div class="cx${chat.listOpen?" list-open":""}">
    <div class="cx-list"><div class="cx-list-top"><input id="cxQ" type="search" placeholder="Search chats" value="${esc(chat.q)}" oninput="chat.q=this.value;drawList()"><button class="primary" onclick="newChat()">+ New</button></div><div id="cxItems"></div></div>
    <section class="cx-main">${c?`<div id="cxBar"></div><div id="cxStatus"></div><div class="msgs" id="msgs"></div><div id="cxPal" class="pal" style="display:none"></div>
      <div class="composer"><textarea id="chIn" rows="1" placeholder="${esc(phText(c))}" oninput="grow(this);chatInput()" onkeydown="chatKey(event)"></textarea>
        <button class="primary send" id="chSend" onclick="chatSend()" title="Send (Enter)">↑</button><button class="send" id="chStop" onclick="chat.ctrl&&chat.ctrl.abort()" title="Stop answering" style="display:none">■</button></div>`
      :`<div class="chat-empty"><div class="big">Start a conversation</div><button class="primary" onclick="newChat()">+ New chat</button></div>`}</section></div>`;
  drawList();if(!c)return;
  drawBar();drawMsgs();drawStatus();
  const i=$("#chIn"),d=localStorage.getItem("cf.draft."+c.id);if(d){i.value=d;grow(i)}i.focus();
  clearInterval(chat.poll);chat.poll=setInterval(chatPoll,4000);chatPoll();
}
function drawList(){
  const el=$("#cxItems");if(!el)return;
  const q=chat.q.toLowerCase(),rows=chat.list.filter(x=>!q||x.title.toLowerCase().includes(q)||x.projects.join(" ").toLowerCase().includes(q));
  const kind=x=>x.tool?"Tool":x.project?"Project":x.projects.length?"Linked":"General";
  el.innerHTML=rows.map(x=>`<div class="cx-item ${chat.cur&&x.id===chat.cur.id?"on":""}" onclick="showChat('${x.id}')"><div class="t">${esc(x.title)}</div><div class="s"><span>${kind(x)}${x.projects.length&&!x.project?" · "+esc(x.projects.join(", ")):x.project?" · "+esc(x.project):""}</span><span>${x.messages} msg</span></div></div>`).join("")||'<div class="hint" style="padding:8px 12px">No chats found</div>';
}
function drawBar(){
  const c=chat.cur,el=$("#cxBar");if(!el||!c)return;
  const names=list.projects.map(p=>p.name),models=[...new Set([c.model,...chat.models])];
  const canAgent=!c.tool&&c.projects.length;
  el.innerHTML=`<div class="cx-head">
    <button class="cx-toggle" onclick="chat.listOpen=!chat.listOpen;document.querySelector('.cx').classList.toggle('list-open')" title="Chats">☰</button>
    <div class="cx-title" onclick="renameChat()" title="Rename">${esc(c.title)}</div>
    ${c.tool?"":`<div class="seg" title="Agent mode lets the model act on the linked projects (you confirm changes)"><button class="${isAgent(c)?"":"on"}" onclick="chatUpdate({agent:false})">Chat</button><button class="${isAgent(c)?"on":""}" ${canAgent?"":"disabled"} onclick="chatUpdate({agent:true})">Agent</button></div>`}
    ${isAgent(c)?`<div class="seg" title="Pipeline: tasks go through plan, build, test and merge. Direct: the agent reads the docs and edits the project itself, step by step."><button class="${isDirect(c)?"":"on"}" onclick="chatUpdate({route:'pipeline'})">Pipeline</button><button class="${isDirect(c)?"on":""}" onclick="chatUpdate({route:'direct'})">Direct</button></div>`:""}
    <select id="chModel" class="cx-model" onchange="chatUpdate({model:this.value})" title="Model of this chat">${models.map(m=>`<option ${m===c.model?"selected":""}>${esc(m)}</option>`).join("")}</select>
    <label class="cx-web" title="Searches the web for each question and gives the model the top pages."><input type="checkbox" ${c.web?"checked":""} onchange="chatUpdate({web:this.checked})"> Web</label>
    <button class="ghost" onclick="exportChat()" title="Download as Markdown">⤓</button><button class="ghost danger" onclick="delChat()" title="Delete this chat">🗑</button></div>
  ${c.tool?`<div class="cx-note">Talks about CodeFactory itself. Proposed changes are built and tested on a separate branch; you try them in a preview and merge. <a href="#" onclick="showTool();return false">Open branches / preview / merge</a></div>`
   :c.project?"":`<div class="cx-note"><span class="lbl">Projects</span><div class="chips">${names.map(n=>`<span class="chip ${c.projects.includes(n)?"on":""}" onclick="toggleLink('${esc(n)}')">${esc(n)}</span>`).join("")||'<span class="hint" style="margin:0">no projects yet</span>'}</div>
     ${!isAgent(c)?`<label class="lbl">Answer buttons use <select id="chTarget">${c.projects.map(n=>`<option>${esc(n)}</option>`).join("")||"<option value=''>(link a project)</option>"}</select></label>`:""}</div>`}`;
}
function drawStatus(){
  const c=chat.cur,el=$("#cxStatus");if(!el)return;
  if(!isAgent(c)){el.innerHTML="";return}
  el.innerHTML=`<div class="cx-status">${c.projects.map(n=>{
    const s=chat.brief[n];if(!s)return `<div class="cs"><span class="dot"></span><b>${esc(n)}</b><span class="hint" style="margin:0">loading…</span></div>`;
    const st=!s.running?["","stopped"]:s.paused?["stop","paused"]:["run","running"],b=s.backlog||{},nq=encodeURIComponent(n);
    return `<div class="cs"><span class="dot ${st[0]}"></span><b>${esc(n)}</b><span class="pill">${st[1]}</span>
      <span class="cs-t" title="${esc(s.current||s.last_log||"")}">${esc(s.current||"idle")}</span>
      <span class="cs-n">${b.pending||0} open · ${b.in_progress||0} doing · ${b.done||0} done${b.blocked?` · <span style="color:var(--warn)">${b.blocked} blocked</span>`:""}</span>
      ${s.spec?`<span class="pill warn" title="A design waits for your review in the Control tab">design to review</span>`:""}
      <span class="cs-b">${s.running?`<button class="small" onclick="ctlBar('${esc(n)}',{paused:${!s.paused}})">${s.paused?"Resume":"Pause"}</button><button class="small ${s.step_mode?"primary":""}" onclick="ctlBar('${esc(n)}',{step_mode:${!s.step_mode}})" title="Wait after every task">Step</button><button class="small" onclick="projBar('${esc(n)}','stop')">Stop</button>`:`<button class="small primary" onclick="projBar('${esc(n)}','start')">Start</button>`}<button class="small ghost" onclick="openProject('${esc(n)}')" title="Open the project">↗</button></span></div>`}).join("")}
    <div class="cs-auto"><label>Auto-run<select onchange="chatUpdate({auto:this.value})" title="Which actions run without a click"><option value="safe" ${c.auto==="safe"||!c.auto?"selected":""}>read-only</option><option value="all" ${c.auto==="all"?"selected":""}>everything</option><option value="off" ${c.auto==="off"?"selected":""}>nothing</option></select></label></div></div>`;
}
async function chatPoll(){
  const c=chat.cur;if(view!=="chat"||!c){clearInterval(chat.poll);return}
  if(!isAgent(c))return;
  await Promise.all(c.projects.map(async n=>{try{chat.brief[n]=await api("/api/project/"+encodeURIComponent(n)+"/x/brief")}catch(e){}}));
  if(view==="chat"&&chat.cur===c)drawStatus();
}
async function ctlBar(n,body){try{await api("/api/project/"+encodeURIComponent(n)+"/control",body);chatPoll()}catch(e){toast(e.message,true)}}
async function projBar(n,a){try{await api("/api/project/"+encodeURIComponent(n)+"/"+a,{});setTimeout(chatPoll,800)}catch(e){toast(e.message,true)}}
function openProject(n){clearInterval(chat.poll);select(n)}

/* ---- messages ---- */
function drawMsgs(){
  const box=$("#msgs"),c=chat.cur;if(!box||!c)return;
  const sug=(c.tool?SUGGEST_TOOL:isAgent(c)?SUGGEST_AGENT:c.projects.length?SUGGEST_LINKED:SUGGEST_FREE).map(t=>`<button class="sugg" onclick="chatAsk(this.dataset.t)" data-t="${esc(t)}">${esc(t)}</button>`).join("");
  const last=c.messages.map(m=>m.role).lastIndexOf("assistant");
  box.innerHTML=c.messages.map((m,i)=>msgHtml(m,i,i===last)).join("")||`<div class="chat-empty"><div class="big">${isAgent(c)?"What should the agent do?":"What are we working on?"}</div><div class="hint">${isAgent(c)?"It can look at the project, queue and reorder tasks, pause the agent and more. Type / for direct commands.":"Ask anything. Link a project and the model knows its goal, backlog and recent work."}</div><div class="suggs">${sug}</div></div>`;
  box.classList.toggle("busy",!!chat.ctrl);box.scrollTop=box.scrollHeight;
}
function actCard(i,k,call,a){
  const t=String(call.tool),st=a?a.status:"pending",d=call.text||call.command||call.title||(t==="design"?(call.approve?"approve":"reject"):"")||call.idea||call.query||call.task&&(call.task+(call.priority?" → "+call.priority:""))||call.file||call.path||call.filter||(t==="step_mode"?(call.on===false?"off":"on"):"");
  const ro=RO.includes(t),btns=st==="pending"?`<button class="small primary" onclick="runAction(${i},${k})">${ro?"Run":"Approve"}</button><button class="small ghost" onclick="runAction(${i},${k},true)">Skip</button>`
    :st==="failed"?`<span class="pill bad">failed</span><button class="small" onclick="runAction(${i},${k})">Retry</button>`:`<span class="pill ${st==="done"?"ok":""}">${st}</span>`;
  return `<div class="act st-${st}"><span class="act-ic">${ICON[t]||"⚙"}</span><div class="act-b"><div><b>${esc(t.replace(/_/g," "))}</b>${call.project?` <span class="pill">${esc(call.project)}</span>`:""}${ro?` <span class="act-ro">read-only</span>`:""}</div>${d?`<div class="act-d">${esc(String(d).slice(0,300))}</div>`:""}${a&&a.result?`<details ${st==="failed"||a.result.length<160?"open":""}><summary>Result</summary><pre>${esc(a.result)}</pre></details>`:""}</div><div class="act-r">${btns}</div></div>`;
}
function msgHtml(m,i,isLast){
  const when=hhmm(m.t);
  if(m.cmd&&m.role==="user")return `<div class="msg user cmdmsg">${esc(m.content)}<span class="mt">${when}</span></div>`;
  if(m.cmd)return `<div class="msg res ${m.ok===false?"bad":""}"><div class="res-h">${ICON[m.tool]||"⚙"} ${esc((m.tool||"").replace(/_/g," "))}</div><pre>${esc(m.content)}</pre></div>`;
  if(m.role==="user")return `<div class="msg user">${esc(m.content)}<span class="mt">${when}</span></div>`;
  const c=chat.cur,tool=c.tool,pj=c.project,agent=isAgent(c);
  const calls=agent?actionBlocks(m.content):[],tasks=!agent&&(tool||pj)?taskBlocks(m.content):[];
  const text=agent?stripActions(m.content):m.content.replace(/```task/g,"```");
  const taskBtns=tool||pj?tasks.map((_,k)=>`<button class="small primary" onclick="${tool?"buildFromChat":"doFromChat"}(${i},${k})">${tool?"Build on test branch":"Do this in "+esc(pj)}${tasks.length>1?" #"+(k+1):""}</button>`).join(""):agent?"":`<button class="small ghost" onclick="fromChat(${i},'say')">Add to ideas</button><button class="small ghost" onclick="fromChat(${i},'do')">Run as task</button>`;
  const acts=m.acts||{},pend=calls.filter((_,k)=>!acts[k]||acts[k].status==="failed").length;
  const cards=calls.length?`<div class="acts-box">${calls.map((cl,k)=>actCard(i,k,cl,acts[k])).join("")}${calls.length>1&&pend>1?`<div class="acts-all"><button class="small primary" onclick="runAll(${i})">Run all ${pend}</button></div>`:""}${isLast&&!pend&&calls.some(cl=>!RO.includes(cl.tool))&&!chat.ctrl?`<div class="acts-all"><button class="small" onclick="chatContinue()" title="Send the results to the model so it can continue">Let the model continue ↵</button></div>`:""}</div>`:"";
  return `<div class="msg assistant" data-i="${i}">${mdLite(text)}${cards}${m.sources&&m.sources.length?`<div class="meta" style="margin-top:6px">Sources: ${m.sources.map(x=>`<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title.slice(0,40))}</a>`).join(" · ")}</div>`:""}<div class="acts"><span class="mt">${esc(m.model||"")} ${when}</span><button class="small ghost" onclick="copyMsg(${i})">Copy</button>${isLast?`<button class="small ghost" onclick="chatRegen()">Regenerate</button>`:""}${taskBtns}</div></div>`;
}
async function runAction(i,k,skip){
  if(chat.ctrl)return;
  try{
    const r=await api(`/api/chat/${chat.cur.id}/action`,{msg:i,k,skip:!!skip});chat.cur=r.chat;
    if(!r.ok)toast(r.result,true);
    drawMsgs();chatPoll();
    return r.ok;
  }catch(e){toast(e.message,true)}
}
async function runAll(i){
  const calls=actionBlocks(chat.cur.messages[i].content);
  for(let k=0;k<calls.length;k++){const a=(chat.cur.messages[i].acts||{})[k];if(a&&a.status!=="failed")continue;if(await runAction(i,k)===false)break}
  await afterActions(i);
}
async function afterActions(i){
  const m=chat.cur.messages[i],calls=actionBlocks(m.content),acts=m.acts||{};
  const open=calls.some((_,k)=>!acts[k]||acts[k].status==="failed");
  if(!open&&calls.some(cl=>RO.includes(cl.tool))&&chat.loop<6)chatContinue();
}
async function autoRun(){
  const c=chat.cur;if(!isAgent(c)||isDirect(c)||c.auto==="off")return;
  const i=c.messages.length-1,m=c.messages[i];if(!m||m.role!=="assistant"||m.cmd)return;
  const calls=actionBlocks(m.content);
  for(let k=0;k<calls.length;k++){
    if((m.acts||{})[k])continue;
    if(!RO.includes(calls[k].tool)&&c.auto!=="all")break;
    if(await runAction(i,k)===false)break;
  }
  await afterActions(i);
}
function chatContinue(){chat.loop++;chatStream("","continue")}
function chatRegen(){chatStream("","regen")}

/* ---- slash commands ---- */
function chatInput(){
  const i=$("#chIn"),c=chat.cur;if(!i||!c)return;
  localStorage.setItem("cf.draft."+c.id,i.value);
  const m=i.value.match(/^\/(\w*)$/),pal=$("#cxPal");
  if(!m||!isAgent(c)){pal.style.display="none";return}
  chat.pal=CMDS.filter(x=>x[0].startsWith(m[1].toLowerCase()));chat.palI=0;drawPal();
}
function drawPal(){
  const pal=$("#cxPal");if(!chat.pal||!chat.pal.length){pal.style.display="none";return}
  pal.style.display="";pal.innerHTML=chat.pal.map((x,k)=>`<div class="pal-i ${k===chat.palI?"on":""}" onmousedown="event.preventDefault();palPick(${k})"><b>/${x[0]}</b> <span class="hint" style="margin:0">${esc(x[1])}</span><span>${esc(x[2])}</span></div>`).join("");
}
function palPick(k){const i=$("#chIn"),x=chat.pal[k];i.value="/"+x[0]+(x[1]?" ":"");chat.pal=null;$("#cxPal").style.display="none";i.focus();grow(i);chatInput();if(!x[1])chatSend()}
function chatKey(e){
  const pal=$("#cxPal");
  if(pal&&pal.style.display!=="none"&&chat.pal&&chat.pal.length){
    if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();chat.palI=(chat.palI+(e.key==="ArrowDown"?1:-1)+chat.pal.length)%chat.pal.length;drawPal();return}
    if(e.key==="Tab"||e.key==="Enter"&&!e.shiftKey){e.preventDefault();palPick(chat.palI);return}
    if(e.key==="Escape"){pal.style.display="none";return}
  }
  if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();chatSend()}
}
function parseCmd(text){
  const m=text.match(/^\/(\w+)\s*(.*)$/s);if(!m)return null;
  const def=CMDS.find(x=>x[0]===m[1].toLowerCase());if(!def)return{error:`Unknown command /${m[1]}. Type / to see all commands.`};
  let arg=m[2].trim(),proj="";const at=arg.match(/(^|\s)@([\w.-]+)/);
  if(at){proj=at[2];arg=arg.replace(at[0],"").trim()}
  const c=chat.cur;proj=proj||c.projects[0]||"";
  if(!c.projects.includes(proj))return{error:"Link a project to this chat first (or add @projectname)"};
  if(def[1].startsWith("<")&&!arg)return{error:`/${def[0]} needs an argument: ${def[1]}`};
  return{call:{...def[3](arg),project:proj},text};
}
async function chatCommand(text){
  const p=parseCmd(text);if(!p)return false;
  if(p.error){toast(p.error,true);return true}
  try{const r=await api(`/api/chat/${chat.cur.id}/action`,{call:p.call,text:p.text});chat.cur=r.chat;drawMsgs();chatPoll();if(!r.ok)toast(r.result,true)}catch(e){toast(e.message,true)}
  return true;
}

/* ---- sending ---- */
function grow(t){t.style.height="auto";t.style.height=Math.min(t.scrollHeight,200)+"px"}
function chatAsk(t){$("#chIn").value=t;chatSend()}
async function openProjectChat(name){try{const c=await api("/api/project/"+encodeURIComponent(name)+"/chat");chat.cur=null;await showChat(c.id)}catch(e){toast(e.message,true)}}
async function doFromChat(i,k){
  const t=taskBlocks(chat.cur.messages[i].content)[k],n=chat.cur.project;
  if(!t||!confirm(`Send this task to the agent of "${n}"?\n\n`+t.slice(0,400)))return;
  try{const r=await api(`/api/project/${encodeURIComponent(n)}/do`,{task:t});toast(r.queued?"Queued: the running agent does it next":"Task started")}catch(e){toast(e.message,true)}
}
async function openToolChat(){try{const c=await api("/api/tool/chat");chat.cur=null;await showChat(c.id)}catch(e){toast(e.message,true)}}
async function buildFromChat(i,k){
  const t=taskBlocks(chat.cur.messages[i].content)[k];
  if(!t||!confirm("Build this on a separate test branch (nothing is merged automatically)?\n\n"+t.slice(0,400)))return;
  try{await api("/api/tool/task",{task:t,model:""});toast("Started on a test branch. Follow it in the Plan tab.");planId="";showTool();toolTab("plan")}catch(e){toast(e.message,true)}
}
function copyMsg(i){navigator.clipboard.writeText(chat.cur.messages[i].content).then(()=>toast("Copied"),()=>toast("Copy not allowed here",true))}
async function newChat(){const p=chat.cur?chat.cur.projects:[];chat.cur=null;await showChat(null,p)}
async function delChat(){if(!confirm("Delete this chat?"))return;try{await api(`/api/chat/${chat.cur.id}/delete`,{});localStorage.removeItem("cf.draft."+chat.cur.id);chat.cur=null;await showChat()}catch(e){toast(e.message,true)}}
async function chatUpdate(b){
  try{
    chat.cur=await api(`/api/chat/${chat.cur.id}/update`,b);
    const x=chat.list.find(v=>v.id===chat.cur.id);if(x){x.title=chat.cur.title;x.projects=chat.cur.projects}
    drawBar();drawList();drawStatus();drawMsgs();chatPoll();
    const ci=$("#chIn");if(ci)ci.placeholder=phText(chat.cur);
  }catch(e){toast(e.message,true)}
}
function renameChat(){const t=prompt("Chat name",chat.cur.title);if(t&&t.trim())chatUpdate({title:t.trim()})}
function toggleLink(n){const p=chat.cur.projects.slice(),i=p.indexOf(n);i<0?p.push(n):p.splice(i,1);chatUpdate({projects:p})}
function exportChat(){
  const c=chat.cur,md=`# ${c.title}\n\n`+c.messages.map(m=>`**${m.role==="user"?"You":"CodeFactory"}**${m.t?` (${new Date(m.t*1000).toLocaleString()})`:""}\n\n${m.content}\n`).join("\n");
  const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([md],{type:"text/markdown"}));a.download=c.title.replace(/[^\w.-]+/g,"_")+".md";a.click();URL.revokeObjectURL(a.href);
}
async function fromChat(i,kind){
  const t=$("#chTarget").value,m=chat.cur.messages[i];
  if(!t){toast("Link a project to this chat first",true);return}
  if(kind==="do"&&!confirm(`Send this answer to the agent of "${t}" as a task?`))return;
  try{await api(`/api/project/${t}/${kind}`,kind==="do"?{task:m.content}:{msg:m.content});toast(kind==="do"?"Task sent":"Added to ideas")}catch(e){toast(e.message,true)}
}
function setBusy(on){const s=$("#chSend"),p=$("#chStop"),m=$("#msgs");if(s)s.style.display=on?"none":"";if(p)p.style.display=on?"":"none";if(m)m.classList.toggle("busy",on)}
async function chatSend(){
  const inp=$("#chIn"),text=inp.value.trim();if(!text||chat.ctrl)return;
  inp.value="";grow(inp);localStorage.removeItem("cf.draft."+chat.cur.id);$("#cxPal").style.display="none";
  if(isAgent(chat.cur)&&text.startsWith("/")&&await chatCommand(text))return;
  chat.loop=0;chatStream(text,"");
}
async function chatStream(text,mode){
  if(chat.ctrl)return;
  const box=$("#msgs"),em=box.querySelector(".chat-empty");if(em)em.remove();
  if(mode==="regen"){const l=box.querySelectorAll(".msg.assistant");if(l.length)l[l.length-1].remove()}
  box.insertAdjacentHTML("beforeend",(text?`<div class="msg user">${esc(text)}</div>`:"")+`<div class="msg assistant" id="live"><div class="think"><span class="dots"><span></span><span></span><span></span></span><span id="thinkT">Thinking…</span></div></div>`);
  let live=$("#live");box.scrollTop=box.scrollHeight;
  const t0=Date.now(),timer=setInterval(()=>{const e=$("#thinkT");if(!e){clearInterval(timer);return}const s=Math.round((Date.now()-t0)/1000);e.textContent=`Thinking… ${s}s`+(s>=8?" (the model may still be loading)":"")},500);
  chat.ctrl=new AbortController();setBusy(true);
  const id=chat.cur.id;let acc="";
  try{
    const r=await fetch(`/api/chat/${id}/send`,{method:"POST",headers:{"X-UI":"1","Content-Type":"application/json"},body:JSON.stringify({content:text,mode}),signal:chat.ctrl.signal});
    if(!r.ok){let j={};try{j=await r.json()}catch(e){}throw new Error(j.error||r.statusText)}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf="";
    for(;;){
      const {done,value}=await rd.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      let k;while((k=buf.indexOf("\n\n"))>=0){
        const ev=buf.slice(0,k).replace(/^data: /,"");buf=buf.slice(k+2);
        if(ev==="[DONE]")continue;
        const j=JSON.parse(ev);
        if(j.delta){acc+=j.delta;live.classList.add("streaming");live.innerHTML=mdLite(isAgent(chat.cur)?stripActions(acc):acc)||'<span class="hint">…</span>';if(box.scrollHeight-box.scrollTop-box.clientHeight<140)box.scrollTop=box.scrollHeight}
        if(j.error)toast(j.error,true);
        if(j.reload){
          try{chat.cur=await api("/api/chat/"+id)}catch(e){}
          drawMsgs();acc="";
          box.insertAdjacentHTML("beforeend",`<div class="msg assistant" id="live"><div class="think"><span class="dots"><span></span><span></span><span></span></span><span id="thinkT">Working…</span></div></div>`);
          live=$("#live");box.scrollTop=box.scrollHeight;
        }
      }
    }
  }catch(e){if(e.name!=="AbortError")toast(e.message,true)}
  clearInterval(timer);chat.ctrl=null;setBusy(false);
  if(view==="chat"&&chat.cur&&chat.cur.id===id){
    try{chat.cur=await api("/api/chat/"+id);chat.list=(await api("/api/chats")).chats}catch(e){}
    drawList();drawMsgs();
    await autoRun();
  }
}
