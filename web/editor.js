/* Code editor of the project view: file tree, tabs, syntax highlighting, search, find/replace, git and tests. No dependencies. */
const ED={proj:"",files:[],open:[],active:"",docs:{},side:"files",dirs:new Set(),hits:null,git:null,out:null,panel:"",auto:false,find:false,qo:null};
const EDLANG={py:"py",js:"js",mjs:"js",ts:"js",tsx:"js",jsx:"js",json:"json",css:"css",html:"html",md:"md",sh:"sh",yml:"sh",yaml:"sh",toml:"sh",go:"js",rs:"js",java:"js",c:"js",h:"js",cpp:"js",txt:"",makefile:"sh"};
const KW={py:"def class return if elif else for while import from as with try except finally raise in is not and or None True False lambda yield pass break continue global async await self",
  js:"function const let var return if else for while import export from as class new this async await try catch finally throw switch case break continue default typeof null true false undefined fn func package type struct interface impl pub use mod def",
  css:"important",sh:"if then else fi for do done case esac function export true false"};
const EDRE={
  py:/(#.*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?\b)|(\b(?:KW)\b)/g,
  js:/(\/\/.*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|(\b(?:KW)\b)/g,
  css:/(\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(#[0-9a-fA-F]{3,8}\b|\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|s)?\b)|(--[\w-]+|[a-z-]+(?=\s*:))/g,
  sh:/(#.*)|("(?:\\.|[^"\\\n])*"|'[^'\n]*')|(\b\d+\b)|(\b(?:KW)\b)/g,
  json:/(?!)()|("(?:\\.|[^"\\\n])*")|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\b(?:true|false|null)\b)/g,
  html:/(<!--[\s\S]*?-->)|("[^"\n]*"|'[^'\n]*')|(?!)()|(<\/?[a-zA-Z][\w-]*|\/?>)/g,
  md:/(<!--[\s\S]*?-->)|(`[^`\n]*`)|(?!)()|(^#{1,6}\s.*$|^\s*(?:[-*]|\d+\.)\s|\*\*[^*\n]+\*\*)/gm};
for(const k in EDRE){const kw=(KW[k]||"x").split(" ").join("|");EDRE[k]=new RegExp(EDRE[k].source.replace("KW",kw),EDRE[k].flags)}
const edLang=p=>{const n=p.split("/").pop().toLowerCase();return EDLANG[n]??EDLANG[n.split(".").pop()]??""};
function edHl(t,lang){
  const re=EDRE[lang];if(!re)return esc(t)+"\n";
  let out="",last=0,m;re.lastIndex=0;
  while((m=re.exec(t))){
    if(!m[0]){re.lastIndex++;continue}
    out+=esc(t.slice(last,m.index))+`<i class="t${m[1]?"c":m[2]?"s":m[3]?"n":"k"}">${esc(m[0])}</i>`;last=m.index+m[0].length}
  return out+esc(t.slice(last))+"\n";
}
const edProj=()=>encodeURIComponent(ED.proj);
const edDoc=()=>ED.docs[ED.active];
const edDirty=d=>d&&d.text!==d.orig;
function edSave(){if(ED.proj)localStorage.setItem("cf_ed_"+ED.proj,JSON.stringify({open:ED.open,active:ED.active}))}

function edMount(){
  const root=$("#ed-root");if(!root)return;
  if(ED.proj!==sel){
    Object.assign(ED,{proj:sel,files:[],open:[],active:"",docs:{},hits:null,git:null,out:null,panel:"",find:false});
    try{const s=JSON.parse(localStorage.getItem("cf_ed_"+sel)||"{}");ED.open=s.open||[];ED.active=s.active||ED.open[0]||""}catch(e){}
  }
  root.innerHTML=`<div class="ed">
    <div class="ed-side"><div class="ed-act">${[["files","Files"],["search","Search"],["git","Git"]].map(([k,l])=>`<button class="${ED.side===k?"on":""}" onclick="edSide('${k}')">${l}</button>`).join("")}</div><div class="ed-sbody" id="ed-sbody"></div></div>
    <div class="ed-main"><div class="ed-tabs" id="ed-tabs"></div><div class="ed-bar" id="ed-bar"></div><div class="ed-find" id="ed-find" hidden></div>
      <div class="ed-area" id="ed-area"></div><div class="ed-panel" id="ed-panel" hidden></div><div class="ed-status" id="ed-status"></div></div>
    <div class="ed-qo" id="ed-qo" hidden></div></div>`;
  edDraw();edLoadTree();[50,400].forEach(t=>setTimeout(()=>{const r=$("#ed-root");if(r&&tab==="editor"&&!ED.scrolled)r.scrollIntoView({block:"start"})},t));ED.scrolled=false;
  ED.open.filter(p=>!ED.docs[p]).forEach(p=>edLoad(p,false));
}
async function edLoadTree(){
  try{const t=await api(`/api/project/${edProj()}/tree?base=head`);ED.files=t.files||[];edSideDraw()}catch(e){}
  try{ED.git=await api(`/api/project/${edProj()}/editor/git`);edSideDraw();edBar()}catch(e){}
}
function edDraw(){edSideDraw();edTabs();edBar();edArea();edStatus()}
function edSide(k){ED.side=k;document.querySelectorAll(".ed-act button").forEach(b=>b.classList.toggle("on",b.textContent.toLowerCase()===k));edSideDraw();if(k==="search")setTimeout(()=>$("#ed-q")?.focus(),0);if(k==="git")edLoadTree()}

/* sidebar */
function edTree(files){
  const root={d:{},f:[]};
  files.forEach(f=>{let n=root;const parts=f.path.split("/");parts.slice(0,-1).forEach(s=>{n=n.d[s]??={d:{},f:[]}});n.f.push({...f,name:parts.at(-1)})});
  const row=(n,pre,depth)=>Object.keys(n.d).sort().map(k=>{const p=pre+k,open=ED.dirs.has(p);
      return `<div class="edt dir" style="padding-left:${depth*12+8}px" onclick="edDir('${jsq(p)}')" oncontextmenu="edCtx(event,'${jsq(p)}',true)"><span class="car">${open?"▾":"▸"}</span>${esc(k)}</div>${open?row(n.d[k],p+"/",depth+1):""}`}).join("")+
    n.f.sort((a,b)=>a.name.localeCompare(b.name)).map(f=>`<div class="edt file ${f.path===ED.active?"cur":""}" style="padding-left:${depth*12+22}px" onclick="edOpen('${jsq(f.path)}')" oncontextmenu="edCtx(event,'${jsq(f.path)}',false)" title="${esc(f.path)}">${esc(f.name)}${f.status?`<b class="edb ${f.status}">${f.status==="A"?"U":f.status}</b>`:""}</div>`).join("");
  return row(root,"",0);
}
function edSideDraw(){
  const b=$("#ed-sbody");if(!b)return;
  if(ED.side==="files"){
    b.innerHTML=`<div class="ed-tools"><button class="small" onclick="edNew(false)">+ File</button><button class="small" onclick="edNew(true)">+ Folder</button><button class="small" onclick="ED.dirs=new Set(ED.files.flatMap(f=>f.path.split('/').slice(0,-1).map((_,i,a)=>a.slice(0,i+1).join('/'))));edSideDraw()">Expand</button><button class="small" onclick="ED.dirs=new Set();edSideDraw()">Collapse</button></div><div class="ed-tree">${ED.files.length?edTree(ED.files):'<div class="hint" style="padding:10px">No files yet.</div>'}</div>`;
  }else if(ED.side==="search"){
    const keep=$("#ed-q")?.value||"";
    b.innerHTML=`<div class="ed-tools"><input id="ed-q" placeholder="Search in project (Enter)" value="${esc(keep)}" onkeydown="if(event.key==='Enter')edSearch()"></div><div class="ed-tools"><label><input id="ed-qc" type="checkbox"> Case</label><label><input id="ed-qr" type="checkbox"> Regex</label></div>
      <div class="ed-hits">${ED.hits===null?'<div class="hint" style="padding:10px">Type at least 2 characters.</div>':ED.hits.hits.length?Object.entries(ED.hits.hits.reduce((a,h)=>((a[h.path]??=[]).push(h),a),{})).map(([p,hs])=>`<div class="edh-f">${esc(p)} <span class="meta">${hs.length}</span></div>${hs.map(h=>`<div class="edh" onclick="edOpen('${jsq(p)}',${h.line})"><span class="ln">${h.line}</span>${esc(h.text.trim())}</div>`).join("")}`).join("")+(ED.hits.truncated?'<div class="hint" style="padding:8px">More results exist, refine the search.</div>':""):'<div class="hint" style="padding:10px">No results.</div>'}</div>`;
  }else{
    const g=ED.git;
    b.innerHTML=!g?'<div class="hint" style="padding:10px">Loading…</div>':`<div class="ed-tools"><span class="meta">Branch <b>${esc(g.branch||"–")}</b></span><button class="small" onclick="edLoadTree()">Refresh</button></div>
      <div class="ed-tools"><input id="ed-cm" placeholder="Commit message"><button class="small primary" onclick="edCommit()" ${g.files.length?"":"disabled"}>Commit all</button></div>
      <div class="ed-hits">${g.files.length?g.files.map(f=>`<div class="edt file" onclick="edOpen('${jsq(f.path)}')"><span class="edt-n">${esc(f.path)}</span><b class="edb ${f.status[0]}">${esc(f.status==="??"?"U":f.status)}</b><button class="small x" title="Discard changes" onclick="event.stopPropagation();edRevert('${jsq(f.path)}')">Undo</button></div>`).join(""):'<div class="hint" style="padding:10px">No uncommitted changes.</div>'}
      <div class="edh-f" style="margin-top:8px">Recent commits</div>${g.log.map(c=>`<div class="edh"><code>${esc(c.sha)}</code> ${esc(c.subject)}</div>`).join("")}</div>`;
  }
}
function edDir(p){ED.dirs.has(p)?ED.dirs.delete(p):ED.dirs.add(p);edSideDraw()}

/* documents and tabs */
async function edLoad(path,focus=true){
  try{const r=await api(`/api/project/${edProj()}/file?path=${encodeURIComponent(path)}&base=head`);
    ED.docs[path]=r.binary?{binary:true,size:r.size,text:"",orig:""}:{text:r.text,orig:r.text,truncated:r.truncated,sel:[0,0],top:0,left:0,head:undefined};
    if(!r.binary&&!r.new_file)api(`/api/project/${edProj()}/editor/head?path=${encodeURIComponent(path)}`).then(h=>{if(ED.docs[path]){ED.docs[path].head=h.text;edGutter()}}).catch(()=>{});
    else if(ED.docs[path])ED.docs[path].head=null;
  }catch(e){ED.open=ED.open.filter(p=>p!==path);if(ED.active===path)ED.active=ED.open[0]||"";toast(e.message,true);edSave()}
  if(focus||path===ED.active)edDraw()
}
async function edOpen(path,line){
  edKeep();
  if(!ED.open.includes(path))ED.open.push(path);
  ED.active=path;edSave();
  if(!ED.docs[path])await edLoad(path);else edDraw();
  if(line)edGoto(line);else $("#ed-ta")?.focus();
}
function edKeep(){const d=edDoc(),ta=$("#ed-ta");if(d&&ta&&!d.binary){d.sel=[ta.selectionStart,ta.selectionEnd];d.top=ta.scrollTop;d.left=ta.scrollLeft}}
function edClose(path,ev){
  ev&&ev.stopPropagation();
  if(edDirty(ED.docs[path])&&!confirm(`${path} has unsaved changes. Close anyway?`))return;
  const i=ED.open.indexOf(path);ED.open.splice(i,1);delete ED.docs[path];
  if(ED.active===path)ED.active=ED.open[Math.min(i,ED.open.length-1)]||"";
  if(ED.active&&!ED.docs[ED.active])edLoad(ED.active);
  edSave();edDraw();
}
function edTabs(){
  const t=$("#ed-tabs");if(!t)return;
  t.innerHTML=ED.open.map(p=>`<div class="edtab ${p===ED.active?"on":""}" onclick="edOpen('${jsq(p)}')" title="${esc(p)}">${esc(p.split("/").pop())}${edDirty(ED.docs[p])?'<span class="dot"></span>':""}<span class="x" onclick="edClose('${jsq(p)}',event)">×</span></div>`).join("")||'<div class="edtab empty">No file open</div>';
}
function edBar(){
  const b=$("#ed-bar");if(!b)return;
  const d=edDoc(),g=ED.git,n=g?g.files.length:0;
  b.innerHTML=`<div class="ed-crumb">${ED.active?ED.active.split("/").map(esc).join(' <span class="meta">›</span> '):'<span class="meta">Ctrl+P opens a file</span>'}</div><div class="spacer"></div>
    <label class="meta" title="Saves 1.5 s after you stop typing"><input type="checkbox" ${ED.auto?"checked":""} onchange="ED.auto=this.checked"> Auto-save</label>
    <button class="small" onclick="edQuick()">Open (Ctrl+P)</button>
    <button class="small" onclick="edFind(true)" ${d&&!d.binary?"":"disabled"}>Find (Ctrl+F)</button>
    <button class="small" onclick="edPanel('diff')" ${d&&!d.binary?"":"disabled"}>Diff</button>
    <button class="small" onclick="edRunTests()">Run tests</button>
    <button class="small" onclick="edPanel('term')" title="Run a command in the project repo (Ctrl+\`)">Terminal</button>
    <button class="small primary" onclick="edSaveFile()" ${edDirty(d)?"":"disabled"}>Save (Ctrl+S)</button>
    <button class="small" onclick="edSide('git')" title="Uncommitted files">${n?`Changes <b>${n}</b>`:"Changes"}</button>`;
}
function edArea(){
  const a=$("#ed-area");if(!a)return;
  const d=edDoc();
  if(!d){a.innerHTML=`<div class="ed-empty"><h3>Code editor</h3><p class="hint">Pick a file on the left or press <kbd>Ctrl</kbd>+<kbd>P</kbd>.</p>
    <p class="hint"><kbd>Ctrl</kbd>+<kbd>S</kbd> save · <kbd>Ctrl</kbd>+<kbd>F</kbd> find &amp; replace · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>F</kbd> search project · <kbd>Ctrl</kbd>+<kbd>G</kbd> go to line · <kbd>Ctrl</kbd>+<kbd>/</kbd> comment · <kbd>Alt</kbd>+<kbd>↑↓</kbd> move line · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>D</kbd> duplicate line</p></div>`;return}
  if(d.binary){a.innerHTML=`<div class="ed-empty"><p class="hint">${esc(ED.active)} is a binary file (${d.size} bytes).</p></div>`;return}
  a.innerHTML=`<div class="ed-gut" id="ed-gut"></div><div class="ed-wrap"><pre class="ed-hl" id="ed-hl" aria-hidden="true"></pre><textarea id="ed-ta" class="ed-ta" spellcheck="false" autocapitalize="off" autocomplete="off" wrap="off"></textarea></div>`;
  const ta=$("#ed-ta");ta.value=d.text;
  ta.addEventListener("input",edInput);ta.addEventListener("keydown",edKey);ta.addEventListener("scroll",edScroll);
  ta.addEventListener("keyup",edStatus);ta.addEventListener("click",edStatus);
  edRender();ta.scrollTop=d.top||0;ta.scrollLeft=d.left||0;edScroll();ta.setSelectionRange(...(d.sel||[0,0]));
}
function edRender(){
  const d=edDoc(),hl=$("#ed-hl");if(!d||!hl)return;
  hl.innerHTML=edHl(d.text,edLang(ED.active));edGutter();
}
function edGutter(){
  const d=edDoc(),g=$("#ed-gut");if(!d||!g)return;
  const lines=d.text.split("\n"),mod=edChanged(d,lines);
  g.innerHTML=`<div class="ed-gin" id="ed-gin">${lines.map((_,i)=>`<div class="${mod.has(i+1)?"chg":""}">${i+1}</div>`).join("")}</div>`;edScroll();
}
function edChanged(d,lines){
  const s=new Set();if(d.head===undefined)return s;
  if(d.head===null){lines.forEach((_,i)=>s.add(i+1));return s}
  const o=d.head.split("\n");let a=0;while(a<o.length&&a<lines.length&&o[a]===lines[a])a++;
  let b=0;while(b<o.length-a&&b<lines.length-a&&o[o.length-1-b]===lines[lines.length-1-b])b++;
  for(let i=a;i<lines.length-b;i++)s.add(i+1);
  if(a===lines.length-b&&o.length-b>a)s.add(Math.max(1,a));
  return s;
}
function edScroll(){const ta=$("#ed-ta"),hl=$("#ed-hl"),g=$("#ed-gin");if(!ta)return;if(hl)hl.style.transform=`translate(${-ta.scrollLeft}px,${-ta.scrollTop}px)`;if(g)g.style.transform=`translateY(${-ta.scrollTop}px)`}
let edT=0,edAuto=0;
function edInput(){
  const d=edDoc(),ta=$("#ed-ta");d.text=ta.value;
  cancelAnimationFrame(edT);edT=requestAnimationFrame(()=>{edRender();edTabs();edBar();edStatus()});
  clearTimeout(edAuto);if(ED.auto&&edDirty(d))edAuto=setTimeout(edSaveFile,1500);
}
function edStatus(){
  const s=$("#ed-status"),d=edDoc(),ta=$("#ed-ta");if(!s)return;
  if(!d||d.binary||!ta){s.innerHTML=`<span>${ED.git?esc(ED.git.branch||""):""}</span>`;return}
  const pre=d.text.slice(0,ta.selectionStart).split("\n"),sel=ta.selectionEnd-ta.selectionStart;
  s.innerHTML=`<span>Ln ${pre.length}, Col ${pre.at(-1).length+1}${sel?` (${sel} selected)`:""}</span><span>${d.text.split("\n").length} lines</span><span>${edLang(ED.active)||"plain text"}</span><span>${d.text.includes("\r\n")?"CRLF":"LF"}</span><span class="${edDirty(d)?"lg-warn":"lg-ok"}">${edDirty(d)?"unsaved":"saved"}</span><span class="spacer"></span><span>${ED.git?esc(ED.git.branch||""):""}</span>`;
}

/* editing helpers */
function edIns(t){const ta=$("#ed-ta");ta.focus();if(!document.execCommand("insertText",false,t)){ta.setRangeText(t,ta.selectionStart,ta.selectionEnd,"end");edInput()}}
function edLines(ta){
  const v=ta.value,a=v.lastIndexOf("\n",ta.selectionStart-1)+1;let b=v.indexOf("\n",ta.selectionEnd>ta.selectionStart?ta.selectionEnd-1:ta.selectionEnd);if(b<0)b=v.length;
  return {a,b,text:v.slice(a,b)};
}
function edReplaceLines(ta,fn,keep=true){
  const {a,b,text}=edLines(ta),s0=ta.selectionStart,e0=ta.selectionEnd,nt=fn(text);
  ta.setSelectionRange(a,b);edIns(nt);
  if(keep)ta.setSelectionRange(a,a+nt.length);else ta.setSelectionRange(s0,e0);
}
const edCmt=()=>({py:"# ",sh:"# ",js:"// ",css:"/* ",md:"",json:"",html:""})[edLang(ED.active)]??"";
const PAIRS={"(":")","[":"]","{":"}",'"':'"',"'":"'","`":"`"};
function edKey(e){
  const ta=e.target,v=ta.value,s=ta.selectionStart,en=ta.selectionEnd,mod=e.ctrlKey||e.metaKey,k=e.key;
  if(mod&&k==="s"){e.preventDefault();return edSaveFile()}
  if(mod&&k==="p"){e.preventDefault();return edQuick()}
  if(mod&&e.shiftKey&&k.toLowerCase()==="f"){e.preventDefault();return edSide("search")}
  if(mod&&k==="f"){e.preventDefault();return edFind(true)}
  if(mod&&k==="g"){e.preventDefault();const n=parseInt(prompt("Go to line"),10);if(n)edGoto(n);return}
  if(mod&&k==="/"){e.preventDefault();const c=edCmt();if(!c)return;
    edReplaceLines(ta,t=>{const ls=t.split("\n"),all=ls.every(l=>!l.trim()||l.trimStart().startsWith(c.trim()));
      return ls.map(l=>!l.trim()?l:all?l.replace(c.trim()+" ","").replace(c.trim(),""):l.replace(/^(\s*)/,"$1"+c)).join("\n")});return}
  if(mod&&e.shiftKey&&k.toLowerCase()==="d"){e.preventDefault();const {b,text}=edLines(ta);ta.setSelectionRange(b,b);edIns("\n"+text);return}
  if(e.altKey&&(k==="ArrowUp"||k==="ArrowDown")){e.preventDefault();
    const {a,b}=edLines(ta),ls=v.split("\n"),i=v.slice(0,a).split("\n").length-1,n=v.slice(a,b).split("\n").length,j=k==="ArrowUp"?i-1:i+1;
    if(j<0||(k==="ArrowDown"&&i+n>=ls.length))return;
    const blk=ls.splice(i,n);ls.splice(k==="ArrowUp"?i-1:i+1,0,...blk);
    ta.setSelectionRange(0,v.length);edIns(ls.join("\n"));
    const off=ls.slice(0,k==="ArrowUp"?i-1:i+1).join("\n").length+(i+(k==="ArrowUp"?-1:1)>0?1:0);ta.setSelectionRange(off,off+blk.join("\n").length);return}
  if(k==="Tab"){e.preventDefault();const tab=/^(makefile|.*\.go)$/i.test(ED.active.split("/").pop())?"\t":"    ";
    if(s===en&&!e.shiftKey)return edIns(tab);
    return edReplaceLines(ta,t=>t.split("\n").map(l=>e.shiftKey?l.replace(/^(\t|    |  | )/,""):tab+l).join("\n"))}
  if(k==="Enter"&&!mod){e.preventDefault();const ln=v.slice(v.lastIndexOf("\n",s-1)+1,s),ind=ln.match(/^\s*/)[0],pv=v[s-1],nx=v[en];
    if(PAIRS[pv]&&PAIRS[pv]===nx&&"([{".includes(pv))return edIns("\n"+ind+"    "+"\n"+ind),ta.setSelectionRange(ta.selectionStart-ind.length-1,ta.selectionStart-ind.length-1);
    return edIns("\n"+ind+(/[{(\[:]\s*$/.test(ln)?"    ":""))}
  if(k==="Backspace"&&s===en&&PAIRS[v[s-1]]&&PAIRS[v[s-1]]===v[s]){e.preventDefault();ta.setSelectionRange(s-1,s+1);return edIns("")}
  if(PAIRS[k]&&!mod){
    if(s!==en){e.preventDefault();return edIns(k+v.slice(s,en)+PAIRS[k])}
    if(k==='"'||k==="'"||k==="`"){if(v[s]===k){e.preventDefault();return ta.setSelectionRange(s+1,s+1)}if(/\w/.test(v[s-1]||""))return}
    e.preventDefault();edIns(k+PAIRS[k]);ta.setSelectionRange(ta.selectionStart-1,ta.selectionStart-1);return}
  if(")]}".includes(k)&&v[s]===k&&s===en){e.preventDefault();ta.setSelectionRange(s+1,s+1)}
}
function edGoto(line){
  const ta=$("#ed-ta");if(!ta)return;const ls=ta.value.split("\n"),n=Math.min(Math.max(line,1),ls.length);
  const pos=ls.slice(0,n-1).reduce((a,l)=>a+l.length+1,0);ta.focus();ta.setSelectionRange(pos,pos+ls[n-1].length);
  ta.scrollTop=Math.max(0,(n-5)*19.5);edScroll();edStatus();
}

/* save / file operations */
async function edSaveFile(){
  const d=edDoc();if(!d||d.binary||!edDirty(d))return;
  const path=ED.active,text=d.text;
  try{await api(`/api/project/${edProj()}/editor`,{op:"save",path,text});d.orig=text;edTabs();edBar();edStatus();edLoadTree();
    api(`/api/project/${edProj()}/editor/head?path=${encodeURIComponent(path)}`).then(h=>{d.head=h.text;edGutter()}).catch(()=>{});toast("Saved "+path)}
  catch(e){toast(e.message,true)}
}
async function edOp(body,msg){try{const r=await api(`/api/project/${edProj()}/editor`,body);if(msg)toast(msg);await edLoadTree();return r}catch(e){toast(e.message,true)}}
async function edNew(folder){
  const base=ED.active?ED.active.split("/").slice(0,-1).join("/"):"",p=prompt(folder?"New folder path":"New file path",base?base+"/":"");if(!p||p.endsWith("/")&&!folder)return;
  const r=await edOp({op:folder?"mkdir":"create",path:p.replace(/\/$/,"")},folder?"Folder created":"File created");if(r&&!folder)edOpen(r.path);
}
function edCtx(ev,path,isDir){
  ev.preventDefault();const a=prompt(`${path}\n\nType: rename, delete`,"rename");if(!a)return;
  if(a.startsWith("d")){if(!confirm(`Delete ${path}?`))return;ED.open.filter(p=>p===path||p.startsWith(path+"/")).forEach(p=>{ED.open=ED.open.filter(x=>x!==p);delete ED.docs[p]});if(!ED.docs[ED.active])ED.active=ED.open[0]||"";edOp({op:"delete",path},"Deleted").then(edDraw)}
  else if(a.startsWith("r")){const to=prompt("New path",path);if(to&&to!==path)edOp({op:"rename",path,to},"Renamed").then(r=>{if(!r)return;ED.open=ED.open.map(p=>p===path?to:p.startsWith(path+"/")?to+p.slice(path.length):p);if(ED.docs[path]){ED.docs[to]=ED.docs[path];delete ED.docs[path]}if(ED.active===path)ED.active=to;edSave();edDraw()})}
}
async function edCommit(){const m=$("#ed-cm").value.trim();if(ED.open.some(p=>edDirty(ED.docs[p]))&&!confirm("Some open files have unsaved changes that will not be committed. Continue?"))return;
  if(await edOp({op:"commit",message:m},"Committed"))edSideDraw()}
async function edRevert(path){if(!confirm(`Discard all uncommitted changes in ${path}?`))return;
  if(await edOp({op:"revert",path},"Reverted")){delete ED.docs[path];if(ED.open.includes(path))await edLoad(path);else edDraw()}}

/* find / replace */
function edFind(open){
  const f=$("#ed-find");if(!f||!edDoc())return;f.hidden=!open;ED.find=open;
  if(!open)return $("#ed-ta")?.focus();
  const ta=$("#ed-ta"),pre=ta.value.slice(ta.selectionStart,ta.selectionEnd);
  f.innerHTML=`<input id="ed-f" placeholder="Find" onkeydown="if(event.key==='Enter'){event.preventDefault();edNext(event.shiftKey?-1:1)}if(event.key==='Escape')edFind(false)" oninput="edCount()">
    <input id="ed-r" placeholder="Replace" onkeydown="if(event.key==='Enter')edRepl(false);if(event.key==='Escape')edFind(false)">
    <label class="meta"><input id="ed-fc" type="checkbox" onchange="edCount()"> Aa</label><span class="meta" id="ed-fn"></span>
    <button class="small" onclick="edNext(-1)">↑</button><button class="small" onclick="edNext(1)">↓</button><button class="small" onclick="edRepl(false)">Replace</button><button class="small" onclick="edRepl(true)">All</button><button class="small" onclick="edFind(false)">×</button>`;
  const i=$("#ed-f");if(pre&&!pre.includes("\n"))i.value=pre;i.focus();i.select();edCount();
}
const edNeedle=()=>({q:$("#ed-f")?.value||"",c:$("#ed-fc")?.checked});
function edIdx(t,q,c){const out=[];if(!q)return out;const h=c?t:t.toLowerCase(),n=c?q:q.toLowerCase();for(let i=h.indexOf(n);i>=0;i=h.indexOf(n,i+n.length))out.push(i);return out}
function edCount(){const {q,c}=edNeedle(),el=$("#ed-fn"),d=edDoc();if(el&&d)el.textContent=q?`${edIdx(d.text,q,c).length} found`:""}
function edNext(dir){
  const {q,c}=edNeedle(),ta=$("#ed-ta");if(!q||!ta)return;const all=edIdx(ta.value,q,c);if(!all.length)return toast("Not found",true);
  const at=dir>0?all.find(i=>i>=ta.selectionEnd)??all[0]:[...all].reverse().find(i=>i<ta.selectionStart)??all.at(-1);
  ta.setSelectionRange(at,at+q.length);const line=ta.value.slice(0,at).split("\n").length;ta.scrollTop=Math.max(0,(line-6)*19.5);edScroll();edStatus();
}
function edRepl(all){
  const {q,c}=edNeedle(),ta=$("#ed-ta"),r=$("#ed-r").value;if(!q||!ta)return;
  if(all){const idx=edIdx(ta.value,q,c);if(!idx.length)return;let out="",last=0;idx.forEach(i=>{out+=ta.value.slice(last,i)+r;last=i+q.length});out+=ta.value.slice(last);ta.focus();ta.setSelectionRange(0,ta.value.length);edIns(out);toast(`${idx.length} replaced`);return edCount()}
  const cur=ta.value.slice(ta.selectionStart,ta.selectionEnd);
  if((c?cur===q:cur.toLowerCase()===q.toLowerCase())){ta.focus();edIns(r)}
  edNext(1);edCount();
}

/* quick open */
function edScore(q,p){let i=0,sc=0,last=-2;const l=p.toLowerCase(),b=l.lastIndexOf("/")+1;
  for(const ch of q){const j=l.indexOf(ch,i);if(j<0)return -1;sc+=(j===last+1?3:1)+(j>=b?2:0);last=j;i=j+1}return sc-p.length/100}
function edQuick(){
  const o=$("#ed-qo");if(!o)return;o.hidden=false;ED.qo={sel:0,list:[]};
  o.innerHTML=`<div class="qo-box"><input id="ed-qi" placeholder="Open file by name…" oninput="edQuickDraw()" onkeydown="edQuickKey(event)"><div id="ed-ql"></div></div>`;
  o.onclick=e=>{if(e.target===o)o.hidden=true};$("#ed-qi").focus();edQuickDraw();
}
function edQuickDraw(){
  const q=$("#ed-qi").value.toLowerCase().replace(/\s+/g,""),recent=new Set(ED.open);
  ED.qo.list=ED.files.map(f=>({p:f.path,s:q?edScore(q,f.path):recent.has(f.path)?5:0})).filter(x=>x.s>=0).sort((a,b)=>b.s-a.s).slice(0,12);
  ED.qo.sel=Math.min(ED.qo.sel,Math.max(ED.qo.list.length-1,0));
  $("#ed-ql").innerHTML=ED.qo.list.map((x,i)=>`<div class="qo-i ${i===ED.qo.sel?"on":""}" onclick="edQuickPick(${i})">${esc(x.p.split("/").pop())} <span class="meta">${esc(x.p)}</span></div>`).join("")||'<div class="hint" style="padding:10px">No match.</div>';
}
function edQuickKey(e){
  if(e.key==="Escape"){$("#ed-qo").hidden=true;return $("#ed-ta")?.focus()}
  if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();ED.qo.sel=(ED.qo.sel+(e.key==="ArrowDown"?1:-1)+ED.qo.list.length)%Math.max(ED.qo.list.length,1);edQuickDraw()}
  if(e.key==="Enter")edQuickPick(ED.qo.sel);
}
function edQuickPick(i){const x=ED.qo.list[i];$("#ed-qo").hidden=true;if(x)edOpen(x.p)}

/* search, tests, diff panel */
async function edSearch(){
  const q=$("#ed-q").value;if(q.length<2)return;
  try{ED.hits=await api(`/api/project/${edProj()}/editor/search?q=${encodeURIComponent(q)}&case=${$("#ed-qc").checked?1:0}&regex=${$("#ed-qr").checked?1:0}`);edSideDraw()}catch(e){toast(e.message,true)}
}
function edPanel(kind){
  const p=$("#ed-panel");if(!p)return;if(ED.panel===kind&&kind!=="tests"){ED.panel="";p.hidden=true;return}
  ED.panel=kind;p.hidden=false;
  if(kind==="term"){edTermDraw();return}
  if(kind==="tests"){const o=ED.out;p.innerHTML=`<div class="ed-ph"><b>Tests</b>${o?`<span class="meta"><code>${esc(o.cmd)}</code></span><span class="${o.rc===0?"lg-ok":"lg-err"}">${o.rc===0?"passed":"failed (exit "+o.rc+")"}</span>`:""}<span class="spacer"></span><button class="small" onclick="edRunTests()">Run again</button><button class="small" onclick="edPanel('')">×</button></div><pre>${o?colorLog(o.out||"(no output)"):"Running…"}</pre>`;return}
  const d=edDoc();if(!d)return;
  const n=d.text.split("\n"),o=d.head==null?[]:d.head.split("\n");let a=0;while(a<o.length&&a<n.length&&o[a]===n[a])a++;
  let b=0;while(b<o.length-a&&b<n.length-a&&o[o.length-1-b]===n[n.length-1-b])b++;
  const del=o.slice(a,o.length-b),add=n.slice(a,n.length-b),pre=n.slice(Math.max(0,a-3),a),post=n.slice(n.length-b,n.length-b+3);
  const body=!del.length&&!add.length?"No changes compared with the last commit.":[...pre.map(l=>"  "+l),...del.map(l=>"-"+l),...add.map(l=>"+"+l),...post.map(l=>"  "+l)].map(l=>`<div class="${l[0]==="+"?"df-a":l[0]==="-"?"df-d":""}">${esc(l)||" "}</div>`).join("");
  p.innerHTML=`<div class="ed-ph"><b>Diff vs. last commit</b><span class="meta">${esc(ED.active)} · line ${a+1} · +${add.length} −${del.length}</span><span class="spacer"></span><button class="small" onclick="edPanel('')">×</button></div><pre class="df">${body}</pre>`;
}
function edTermDraw(){
  const p=$("#ed-panel"),h=ED.term||(ED.term=[]);
  p.innerHTML=`<div class="ed-ph"><b>Terminal</b><span class="meta">runs in the repo root, one command at a time</span><span class="spacer"></span><button class="small" onclick="ED.term=[];edTermDraw()">Clear</button><button class="small" onclick="edPanel('')">×</button></div>
  <pre id="ed-termout">${h.map(r=>`<span class="lg-info">$ ${esc(r.cmd)}</span>\n${colorLog(r.out||"")}${r.rc?`<span class="lg-err">[exit ${r.rc}]</span>\n`:""}`).join("")}</pre>
  <div class="ed-term-in"><span>$</span><input id="ed-termin" autocomplete="off" spellcheck="false" placeholder="e.g. python app.py, npm run build, git log --oneline" onkeydown="edTermKey(event)"></div>`;
  const o=$("#ed-termout");o.scrollTop=o.scrollHeight;$("#ed-termin").focus();
}
async function edTermKey(e){
  const i=e.target,h=ED.term;
  if(e.key==="ArrowUp"&&h.length){ED.ti=Math.max(0,(ED.ti??h.length)-1);i.value=h[ED.ti].cmd;e.preventDefault();return}
  if(e.key!=="Enter"||!i.value.trim())return;
  const cmd=i.value;i.value="";i.disabled=true;ED.ti=null;
  try{h.push(await api(`/api/project/${edProj()}/editor`,{op:"exec",cmd}))}catch(er){h.push({cmd,rc:-1,out:er.message})}
  if(ED.panel==="term")edTermDraw();
  edLoadTree();
}
async function edRunTests(){
  ED.out=null;edPanel("tests");
  try{ED.out=await api(`/api/project/${edProj()}/editor`,{op:"run-tests"})}catch(e){ED.out={cmd:"",rc:-1,out:e.message}}
  if(ED.panel==="tests")edPanel("tests");
}
window.addEventListener("beforeunload",e=>{if(ED.open.some(p=>edDirty(ED.docs[p]))){e.preventDefault();e.returnValue=""}});
document.addEventListener("keydown",e=>{
  if(tab!=="editor"||view!=="project"||!(e.ctrlKey||e.metaKey))return;
  if(e.key==="p"&&e.target.id!=="ed-ta"){e.preventDefault();edQuick()}
  if(e.key==="s"){e.preventDefault();edSaveFile()}
});
