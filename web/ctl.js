/* Control / Code / Insight tabs: live control panel, role editor, context tuner, code search, test generator,
   refactor runs, terminal/playground, ADR log, health score, timeline. Built on the /api/project/<n>/x/<what>
   and /api/ext/* endpoints (webapi/ext.py). */
GROUPS.push(["control", "Control", ["control", "roles"]], ["codex", "Code search", ["search", "refactor"]], ["insight", "Insight", ["adrs", "insighttl"]]);
Object.assign(LEAF, {control: "Control panel", roles: "Roles", search: "Search & tests", refactor: "Refactor",
  adrs: "Decisions & health", insighttl: "Timeline"});
const CTL_TABS = new Set(["control", "roles", "search", "refactor", "adrs", "insighttl"]);
const CTL = {}; // per-tab cache: {kind, ...payload}

async function ctlFetch(tab) {
  const n = encodeURIComponent(sel);
  if (tab === "control") { CTL.control = await api(`/api/project/${n}/x/control`); CTL.queue = await api(`/api/project/${n}/x/queue`); }
  else if (tab === "roles") CTL.roles = await api(`/api/project/${n}/x/roles`);
  else if (tab === "search") {
    CTL.untested = await api(`/api/project/${n}/x/untested`);
    if (CTL.q) CTL.search = await api(`/api/project/${n}/x/search?q=${encodeURIComponent(CTL.q)}`);
  } else if (tab === "refactor") CTL.dead = await api(`/api/project/${n}/x/dead`);
  else if (tab === "adrs") { CTL.health = await api(`/api/project/${n}/x/health`); CTL.adrs = await api(`/api/project/${n}/x/adrs?q=${encodeURIComponent(CTL.adrq || "")}`); }
  else if (tab === "insighttl") CTL.tl = await api(`/api/project/${n}/x/timeline`);
}
const _fetchExtra0 = fetchExtra;
fetchExtra = async function () {
  if (view === "project" && sel && CTL_TABS.has(tab)) { try { await ctlFetch(tab); } catch (e) {} return; }
  return _fetchExtra0();
};
const _projectBody0 = projectBody;
projectBody = function (d) {
  if (tab === "control") return ctlControlBody(d);
  if (tab === "roles") return ctlRolesBody(d);
  if (tab === "search") return ctlSearchBody(d);
  if (tab === "refactor") return ctlRefactorBody(d);
  if (tab === "adrs") return ctlAdrsBody(d);
  if (tab === "insighttl") return ctlTimelineBody(d);
  return _projectBody0(d);
};

async function ctlAct(action, body) {
  try { const r = await api(`/api/project/${encodeURIComponent(sel)}/${action}`, body || {}); lastBody = ""; await ctlFetch(tab); renderProject(); return r; }
  catch (e) { toast(e.message, true); throw e; }
}
function ctlControlBody(d) {
  const c = CTL.control; if (!c) return `<div class="hint">Loading…</div>`;
  const hints = (c.hints || []).map(h => `<li>${esc(h)}</li>`).join("");
  const now = ctlNowBody(d);
  const spec = c.spec ? `<div class="panel" style="margin-top:16px"><h3>Spec waiting for your OK: ${esc(c.spec.title || "")}</h3>
    <pre>${esc(JSON.stringify(c.spec.files || c.spec, null, 2))}</pre>
    <div class="row" style="justify-content:flex-end;gap:6px"><button class="danger" onclick="ctlAct('spec-decide',{approve:false})">Reject</button><button class="primary" onclick="ctlAct('spec-decide',{approve:true})">Approve</button></div></div>` : "";
  return now + ctlQueueBody(c) + `<div class="panel" style="margin-top:16px"><h3>Live control ${c.running ? '<span class="pill ok">running</span>' : '<span class="pill">stopped</span>'}</h3>
  <p class="hint">Pause waits after the current task; step mode stops after every task until you resume. Changes apply on the agent's next check (within a few seconds).</p>
  <div class="row" style="gap:8px;flex-wrap:wrap">
    <button class="${c.paused ? "primary" : ""}" onclick="ctlAct('control',{paused:${!c.paused}})" ${c.running ? "" : "disabled"}>${c.paused ? "Resume" : "Pause"}</button>
    <label><input type="checkbox" ${c.step_mode ? "checked" : ""} onchange="ctlAct('control',{step_mode:this.checked})"> Step mode (wait after every task)</label>
  </div>
  <div class="row" style="margin-top:10px;gap:6px"><input id="ctl-next" placeholder="task id, e.g. T0012" value="${esc(c.next || "")}" style="max-width:160px"><button onclick="ctlAct('control',{next:$('#ctl-next').value})">Run this one next</button>${c.next ? `<button class="ghost" onclick="ctlAct('control',{next:''})">Clear</button>` : ""}</div>
  <div class="row" style="margin-top:10px;gap:6px"><input id="ctl-hint" placeholder="hint for the current/next attempt, pinned into every prompt" onkeydown="if(event.key==='Enter')ctlHint()"><button onclick="ctlHint()">Pin hint</button></div>
  <div class="chips" style="margin-top:6px">${HINT_PRESETS.map(h => `<span class="chip" onclick="ctlHint('${esc(h).replace(/'/g, "\\'")}')">${esc(h)}</span>`).join("")}</div>
  ${hints ? `<ul style="margin-top:8px">${hints}</ul><button class="small ghost" onclick="ctlAct('control',{clear_hints:true})">Clear hints</button>` : ""}</div>
  ${spec}
  <div class="panel" style="margin-top:16px"><h3>Context window tuner</h3><p class="hint">Reads what the running model server reports and proposes num_ctx for the Modelfile. Your biggest bottleneck if answers get cut off or re-planning fails.</p>
  <button onclick="ctlTune()">Check now</button><div id="ctl-tune"></div></div>
  <div class="panel" style="margin-top:16px"><h3>Terminal</h3><p class="hint">Runs a command in a throwaway copy of the repo (sandbox), output capped. Does not touch the real project.</p>
  <div class="row" style="gap:6px"><input id="ctl-cmd" placeholder="e.g. python3 -m pytest -q" style="flex:1"><button onclick="ctlSandbox()">Run</button></div><pre id="ctl-cmd-out"></pre></div>
  <div class="panel" style="margin-top:16px"><h3>Playground</h3><p class="hint">Starts the app for real (same command as "Test now"), with a live log, so you can click around while it runs.</p>
  <div class="row" style="gap:6px"><input id="ctl-play-cmd" placeholder="run command (optional, detected by default)" style="flex:1"><button onclick="ctlPlay()">Start</button><button class="ghost" onclick="ctlAct('play-stop')">Stop</button></div><div id="ctl-play"></div></div>`;
}
async function ctlHint(preset) { const v = preset || $("#ctl-hint").value.trim(); if (!v) return; await ctlAct("control", { hint: v }); if (!preset) $("#ctl-hint").value = ""; }
const HINT_PRESETS = ["Be conservative with refactors", "No new dependencies", "Keep changes small and reviewable", "Write a test before touching this", "Prefer clarity over cleverness"];
async function ctlTune() {
  try { const r = await api("/api/ext/ctx"); $("#ctl-tune").innerHTML = r.models && r.models.length ? `<table><tr><th>Model</th><th>Current num_ctx</th><th>Recommended</th><th></th></tr>${r.models.map(m => `<tr><td>${esc(m.name)}</td><td>${m.current_ctx || "–"}</td><td title="${esc(m.why)}">${m.recommended}</td><td>${m.recommended > (m.current_ctx || 4096) ? `<button class="small" onclick="ctlTuneWrite('${esc(m.name)}',${m.recommended})">Write Modelfile</button>` : "already big enough"}</td></tr>`).join("")}</table>` : `<div class="hint">${esc(r.error || "No model server reachable.")}</div>`; }
  catch (e) { toast(e.message, true); }
}
async function ctlTuneWrite(model, numCtx) { try { const r = await api("/api/ext/ctx-write", { model, num_ctx: numCtx }); toast(`Wrote ${r.path}. Run: ollama create ${model} -f ${r.path}`); } catch (e) { toast(e.message, true); } }
async function ctlSandbox() {
  const cmd = $("#ctl-cmd").value.trim(); if (!cmd) return;
  try { await ctlAct("sandbox-run", { cmd }); ctlPoll("terminal", "#ctl-cmd-out", r => (r.out || "") + (r.rc != null ? `\n[exit ${r.rc}]` : "")); } catch (e) {}
}
async function ctlPlay() {
  try { await ctlAct("play-start", { cmd: $("#ctl-play-cmd").value.trim() }); ctlPlayPoll(); } catch (e) {}
}
async function ctlPlayPoll() {
  try { const s = await api(`/api/project/${encodeURIComponent(sel)}/x/play`); $("#ctl-play").innerHTML = `<div class="meta">${s.running ? "running" : "stopped"}${s.url ? ` · <a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.url)}</a>` : ""}</div><pre>${esc(s.log || "")}</pre>`; if (s.running && tab === "control") setTimeout(ctlPlayPoll, 2000); } catch (e) {}
}
async function ctlPoll(kind, sel_, render) {
  try { const r = await api(`/api/project/${encodeURIComponent(sel)}/x/job?kind=${kind}`);
    if (r.state === "running") { setTimeout(() => ctlPoll(kind, sel_, render), 1500); return; }
    const box = $(sel_); if (box) box.innerHTML = esc(r.state === "error" ? "Error: " + r.error : render(r.result || {})); }
  catch (e) {}
}
function ctlRolesBody(d) {
  const r = CTL.roles; if (!r) return `<div class="hint">Loading…</div>`;
  return `<div class="panel"><h3>Roles</h3><p class="hint">Per-role overrides for temperature, reasoning and an extra system prompt. The model of a role stays set under "Models & limits".</p>
  ${r.roles.map(role => { const o = r.custom[role] || {}; return `<div class="panel" style="margin-top:10px"><h4>${esc(role)}</h4>
    <label class="fld">Temperature<input id="role-${role}-t" type="number" step="0.05" min="0" max="2" value="${o.temperature != null ? o.temperature : ""}" placeholder="default"></label>
    <label class="fld">Reasoning<select id="role-${role}-r"><option value="">default</option><option value="off" ${o.think === "off" ? "selected" : ""}>off</option></select></label>
    <label class="fld">Extra system prompt<textarea id="role-${role}-p" rows="3">${esc(o.prompt || "")}</textarea></label>
    <div class="row" style="justify-content:flex-end;gap:6px"><button class="ghost" onclick="ctlRoleReset('${role}')">Reset</button><button class="primary" onclick="ctlRoleSave('${role}')">Save</button></div></div>`; }).join("")}</div>`;
}
async function ctlRoleSave(role) {
  const body = { role, temperature: $(`#role-${role}-t`).value || undefined, think: $(`#role-${role}-r`).value || undefined, prompt: $(`#role-${role}-p`).value };
  await ctlAct("role-save", body);
}
async function ctlRoleReset(role) { await ctlAct("role-reset", { role }); }
function ctlSearchBody(d) {
  const u = CTL.untested || { files: [] }, s = CTL.search || { hits: [] };
  return `<div class="panel"><h3>Code search</h3><div class="row" style="gap:6px"><input id="ctl-q" placeholder="symbol or text" value="${esc(CTL.q || "")}" onkeydown="if(event.key==='Enter')ctlSearch()"><button onclick="ctlSearch()">Search</button></div>
  ${(s.hits || []).length ? `<table style="margin-top:8px"><tr><th>Kind</th><th>Name</th><th>File</th><th></th></tr>${s.hits.map(h => `<tr><td>${esc(h.kind)}</td><td>${esc(h.name)}</td><td>${esc(h.file)}:${h.line}</td><td><button class="small" onclick="ctlUses('${esc(h.name)}')">Who uses this</button></td></tr>`).join("")}</table>` : CTL.q ? '<div class="hint">No hits.</div>' : ""}
  <div id="ctl-uses"></div></div>
  <div class="panel" style="margin-top:16px"><h3>Files without tests</h3>
  ${u.files.length ? `<table><tr><th>File</th><th></th></tr>${u.files.map(f => `<tr><td>${esc(f.file)}</td><td><button class="small" onclick="ctlAct('testgen',{file:'${esc(f.file)}'}).then(r=>toast('Task '+r.task+' queued'))">Generate tests</button> <button class="small ghost" onclick="ctlMutation('${esc(f.file)}')">Mutation check</button></td></tr>`).join("")}</table>` : '<div class="hint">Everything has at least one test file.</div>'}
  <div id="ctl-mut"></div></div>`;
}
function ctlSearch() { CTL.q = $("#ctl-q").value.trim(); lastBody = ""; ctlFetch("search").then(renderProject); }
async function ctlUses(name) {
  try { const r = await api(`/api/project/${encodeURIComponent(sel)}/x/uses?symbol=${encodeURIComponent(name)}`);
    $("#ctl-uses").innerHTML = `<h4 style="margin-top:10px">Uses of ${esc(name)}</h4>` + (r.uses.length ? `<ul>${r.uses.map(u => `<li>${esc(u.file)}:${u.line}</li>`).join("")}</ul>` : '<div class="hint">No other file references it.</div>'); }
  catch (e) { toast(e.message, true); }
}
async function ctlMutation(file) { await ctlAct("mutation", { file }); $("#ctl-mut").innerHTML = "Running…"; ctlPoll("mutation", "#ctl-mut", r => r.note ? r.note : (r.survivors || []).length ? `${r.survivors.length} of ${r.total} surviving mutant(s):\n` + r.survivors.map(s => `${s.line}: ${s.change}`).join("\n") : `All ${r.total} mutant(s) caught.`); }
function ctlRefactorBody(d) {
  const x = CTL.dead || { dead: [], big: [] };
  return `<div class="panel"><h3>Dead code</h3>${x.dead.length ? `<ul>${x.dead.map(f => `<li>${esc(f)}</li>`).join("")}</ul><button onclick="ctlAct('refactor-dead',{}).then(r=>toast('Task '+r.task+' queued'))">Queue a cleanup task</button>` : '<div class="hint">None found.</div>'}</div>
  <div class="panel" style="margin-top:16px"><h3>Oversized modules</h3>${x.big.length ? `<table><tr><th>File</th><th>Lines</th><th></th></tr>${x.big.map(b => `<tr><td>${esc(b.file)}</td><td>${b.lines}</td><td><button class="small" onclick="ctlAct('refactor-split',{file:'${esc(b.file)}'}).then(r=>toast('Task '+r.task+' queued'))">Queue a split</button></td></tr>`).join("")}</table>` : '<div class="hint">None over the limit.</div>'}</div>
  <div class="panel" style="margin-top:16px"><h3>Rename a symbol</h3><p class="hint">Stop the agent first: this touches many files in one pass.</p>
  <div class="row" style="gap:6px"><input id="ren-old" placeholder="old name"><input id="ren-new" placeholder="new name"><button onclick="ctlRenamePlan()">Preview</button></div>
  <div id="ctl-ren"></div></div>`;
}
async function ctlRenamePlan() {
  const old = $("#ren-old").value.trim(), nw = $("#ren-new").value.trim(); if (!old || !nw) return;
  try { const r = await api(`/api/project/${encodeURIComponent(sel)}/refactor-rename`, { old, new: nw });
    const n = Object.keys(r.plan.files).length, hits = Object.values(r.plan.files).reduce((a, b) => a + b, 0);
    $("#ctl-ren").innerHTML = `<p class="hint">${n} file(s), ${hits} occurrence(s).</p>` + (hits ? `<button class="danger" onclick="ctlRenameApply('${esc(old)}','${esc(nw)}')">Apply now</button>` : ""); }
  catch (e) { toast(e.message, true); }
}
async function ctlRenameApply(old, nw) {
  if (!confirm(`Rename "${old}" to "${nw}" in the repo now? This is not reversible from the UI.`)) return;
  try { const r = await api(`/api/project/${encodeURIComponent(sel)}/refactor-rename`, { old, new: nw, apply: true }); toast(`Renamed in ${r.files} file(s)`); lastBody = ""; } catch (e) { toast(e.message, true); }
}
function ctlAdrsBody(d) {
  const h = CTL.health || {}, a = CTL.adrs || { adrs: [] };
  return `<div class="panel"><h3>Health score${h.score != null ? `: <span class="pill ${h.score >= 70 ? "ok" : h.score >= 40 ? "warn" : "bad"}">${h.score}/100</span>` : ""}</h3>
  ${h.parts ? `<ul>${Object.entries(h.parts).map(([k, p]) => `<li>${esc(k)} (weight ${p.weight}): ${esc(p.hint)}</li>`).join("")}</ul>` : '<div class="hint">No evidence yet.</div>'}</div>
  <div class="panel" style="margin-top:16px"><h3>Decisions (ADRs)</h3><div class="row" style="gap:6px"><input id="adr-q" placeholder="search" value="${esc(CTL.adrq || "")}" onkeydown="if(event.key==='Enter')ctlAdrSearch()"><button onclick="ctlAdrSearch()">Search</button></div>
  ${a.adrs.length ? a.adrs.map(r => `<details style="margin-top:6px"><summary>${esc(r.title || r.item || "")} <span class="meta">${esc((r.time || "").slice(0, 16))}</span></summary><div class="inner">${esc(r.context || "")}<br><b>Decision:</b> ${esc(r.decision || "")}<br><b>Consequences:</b> ${esc(r.consequences || "")}</div></details>`).join("") : '<div class="hint">No decisions recorded yet.</div>'}</div>`;
}
function ctlAdrSearch() { CTL.adrq = $("#adr-q").value.trim(); lastBody = ""; ctlFetch("adrs").then(renderProject); }
function ctlTimelineBody(d) {
  const ev = (CTL.tl || {}).events || [];
  return `<div class="panel"><h3>What happened</h3><p class="hint">Every agent step, control change and rollback in one timeline, newest first.</p>
  ${ev.length ? `<table><tr><th>Time</th><th>Kind</th><th>What</th></tr>${ev.slice().reverse().map(e => `<tr><td>${esc((e.time || "").slice(0, 16).replace("T", " "))}</td><td><span class="pill info">${esc(e.kind || "")}</span></td><td>${esc(e.text || e.detail || "")}</td></tr>`).join("")}</table>` : '<div class="hint">Nothing recorded yet.</div>'}</div>`;
}

/* what the agent is doing right now, inline in the Control tab: phase, current call, and the live reasoning/Aider/shell stream */
function ctlNowBody(d) {
  const a = (d && d.activity) || {}, n = a.now, t = Date.now() / 1000;
  if (!a.running) return `<div class="panel"><h3>What it's doing</h3><div class="hint">Agent is not running.</div></div>`;
  const call = a.cmd ? `Running <b class="lg-warn">${esc(a.cmd.name)}</b>: <code>${esc(a.cmd.cmd)}</code> · ${Math.round(t - a.cmd.since)}s`
    : a.coder ? `Aider (<code>${esc(a.coder.model)}</code>) is editing ${esc(a.coder.files.join(", ") || "files")} for <b>${esc(a.coder.tag)}</b> · ${Math.round(t - a.coder.since)}s`
    : a.llm ? `Thinking as <b>${esc(a.llm.role)}</b> (<code>${esc(a.llm.model)}</code>) on <b>${esc(a.llm.tag)}</b> · ${Math.round(t - a.llm.since)}s` : "Starting…";
  return `<div class="panel"><h3>What it's doing now</h3>
    <div class="sub">${n ? `${esc(n.phase)}${n.task ? ` · task ${esc(n.task)}` : ""}` : ""}</div>
    <div class="call">${call}</div>
    <div id="reasonlive"></div>
    <p class="hint">Live reasoning, Aider output and shell output appear above while a call runs. <a href="#" onclick="setTab('reasoning');return false">Full history →</a></p></div>`;
}
/* task queue: every task with the actions that make sense for its status */
const QSTAT = {pending: ["open", "info"], in_progress: ["doing", "ok"], blocked: ["blocked", "warn"], done: ["done", "ok"], cancelled: ["cancelled", ""]};
function ctlQueueBody(c) {
  const q = CTL.queue; if (!q) return "";
  const f = CTL.qf || "active", tasks = q.tasks.filter(t => f === "all" || (f === "active" ? ["pending", "in_progress", "blocked"].includes(t.status) : t.status === f));
  const cnt = k => q.tasks.filter(t => k === "all" || (k === "active" ? ["pending", "in_progress", "blocked"].includes(t.status) : t.status === k)).length;
  const chips = [["active", "Active"], ["pending", "Open"], ["blocked", "Blocked"], ["done", "Done"], ["cancelled", "Cancelled"], ["all", "All"]].map(([k, l]) => `<span class="chip ${f === k ? "on" : ""}" onclick="CTL.qf='${k}';renderProject()">${l} ${cnt(k)}</span>`).join("");
  const rows = tasks.slice().sort((a, b) => (a.status === "in_progress" ? -1 : 0) - (b.status === "in_progress" ? -1 : 0) || (b.priority || 0) - (a.priority || 0)).map(t => {
    const st = QSTAT[t.status] || [t.status, ""], open = t.status === "pending", btn = (l, a, extra = "") => `<button class="small ${extra}" onclick="ctlTask('${a}','${esc(t.id)}')">${l}</button>`;
    const acts = (open ? btn(q.next === t.id ? "★ next" : "Next", "next", q.next === t.id ? "primary" : "") + btn("Top", "top") + btn("Edit", "edit") + btn("Cancel", "cancel") : "")
      + (t.status === "blocked" ? btn("Edit", "edit") + btn("Reopen", "reopen") + btn("Cancel", "cancel") + btn("Delete", "delete", "danger") : "")
      + (t.status === "cancelled" ? btn("Reopen", "reopen") + btn("Delete", "delete", "danger") : "");
    return `<tr><td class="mono">${esc(t.id)}</td><td><div>${esc(t.title)}</div>${t.reason && t.status !== "pending" ? `<div class="hint" style="margin:2px 0 0">${esc(String(t.reason).slice(0, 160))}</div>` : ""}</td><td><span class="pill ${st[1]}">${st[0]}</span></td>
      <td>${open ? `<input class="qprio" type="number" step="0.5" value="${t.priority || 0}" onchange="ctlTask('prio','${esc(t.id)}',this.value)">` : t.layer != null ? "L" + t.layer : ""}</td><td class="qa">${acts}</td></tr>`;
  }).join("");
  return `<div class="panel"><h3>Task queue</h3><div class="chips" style="margin:6px 0 10px">${chips}</div>
    <div class="row" style="gap:6px;margin-bottom:10px"><input id="ctl-newtask" placeholder="Queue a new task: what to build and how to check it" style="flex:1" onkeydown="if(event.key==='Enter')ctlAddTask()"><button class="primary" onclick="ctlAddTask()">Add task</button></div>
    ${rows ? `<table class="qtable"><tr><th>ID</th><th>Task</th><th>Status</th><th>Prio / Layer</th><th></th></tr>${rows}</table>` : '<div class="hint">No tasks in this view.</div>'}</div>`;
}
async function ctlTask(a, id, v) {
  const t = CTL.queue.tasks.find(x => x.id === id) || {};
  try {
    if (a === "next") await ctlAct("control", {next: CTL.queue.next === id ? "" : id});
    else if (a === "top") await ctlAct("task-top", {id});
    else if (a === "prio") await ctlAct("task-prio", {id, priority: v});
    else if (a === "cancel") await ctlAct("task-cancel", {id});
    else if (a === "reopen") await ctlAct("task-reopen", {id});
    else if (a === "delete") { if (confirm(`Delete ${id} for good?`)) await ctlAct("task-delete", {id}); }
    else if (a === "edit") {
      const title = prompt("Title", t.title); if (title === null) return;
      const description = prompt("Description (what to do, how to check it)", t.description || ""); if (description === null) return;
      await ctlAct("task-edit", {id, title, description});
    }
  } catch (e) {}
}
async function ctlAddTask() { const i = $("#ctl-newtask"), v = i.value.trim(); if (!v) return; try { await ctlAct("do", {task: v}); toast("Task queued"); } catch (e) {} }
