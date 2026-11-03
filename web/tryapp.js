// "Try your app": starts what the project really is. A CLI tool gets an argument box with examples, a web app gets Start/Stop and a link.
let TRY = { name: null, plan: null, args: "", out: "", running: false, play: null };
const tryBase = () => `/api/project/${encodeURIComponent(sel)}`;
function tryUrl(p) { const m = /https?:\/\/[^\s)]+/.exec((p && p.url) || ""); return m ? m[0] : ""; }
async function tryLoad() {
  const n = sel; TRY.name = n;
  try { TRY.plan = await api(`${tryBase()}/x/runplan`); } catch (e) { TRY.plan = { mode: "other", hint: e.message, examples: [], run: "" }; }
  if (sel === n && tab === "testing") { lastBody = ""; renderProject(); }
}
function tryPanel() {
  if (TRY.name !== sel) { TRY.plan = null; TRY.out = ""; TRY.args = ""; tryLoad(); }
  const p = TRY.plan;
  const head = `<h3>Try your app${p && p.kind ? ` <span class="pill">${esc(p.mode)}</span>` : ""}</h3>`;
  if (!p) return `<div class="panel">${head}<div class="hint">Looking at the project…</div></div>`;
  if (p.mode === "cli") {
    if (!p.entry) return `<div class="panel">${head}<div class="hint">${esc(p.hint)}</div></div>`;
    return `<div class="panel">${head}<p class="hint">${esc(p.hint)} It runs on the last merged state in a throwaway copy.</p>
    <div class="row" style="gap:6px"><code>${esc(p.entry)}</code><input id="try-args" value="${esc(TRY.args)}" placeholder="arguments, e.g. --help" oninput="TRY.args=this.value;dirty=true" onkeydown="if(event.key==='Enter')tryRun()"><button class="primary" onclick="tryRun()" ${TRY.running ? "disabled" : ""}>${TRY.running ? "Running…" : "Run"}</button></div>
    <div class="chips" style="margin-top:6px">${(p.examples || []).map((x, i) => `<span class="chip" onclick="tryRun(${i})">${esc(x)}</span>`).join("")}</div>
    ${TRY.out ? `<pre style="margin-top:8px">${esc(TRY.out)}</pre>` : ""}</div>`;
  }
  if (p.mode === "library") return `<div class="panel">${head}<p class="hint">${esc(p.hint)}</p><code>${esc(p.run)}</code></div>`;
  if (!p.run) return `<div class="panel">${head}<div class="hint">No start command known yet.</div></div>`;
  const s = TRY.play, url = tryUrl(p);
  return `<div class="panel">${head}<p class="hint">Starts the app on the last merged state in a throwaway copy. <code>${esc(p.run)}</code></p>
  <div class="row" style="gap:6px"><button class="primary" onclick="tryStart()" ${s && s.running ? "disabled" : ""}>Start</button><button class="ghost" onclick="tryStop()">Stop</button>
  ${s && s.running && url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>` : ""}<span class="meta">${s && s.running ? "running" : "stopped"}</span></div>
  ${s && s.log ? `<pre style="margin-top:8px">${esc(s.log)}</pre>` : ""}</div>`;
}
async function tryRun(i) {
  const p = TRY.plan; if (!p || TRY.running) return;
  if (i !== undefined) TRY.args = p.examples[i];
  const cmd = `${p.entry} ${TRY.args}`.trim();
  if (document.activeElement) document.activeElement.blur();
  TRY.running = true; TRY.out = `$ ${cmd}`; dirty = false; lastBody = ""; renderProject();
  try {
    await api(`${tryBase()}/sandbox-run`, { cmd, timeout: 60 });
    for (let k = 0; k < 130; k++) {
      await new Promise(r => setTimeout(r, 1000));
      const r = await api(`${tryBase()}/x/job?kind=terminal`);
      if (r.state === "running") continue;
      const res = r.result || {};
      TRY.out = `$ ${cmd}\n${r.state === "error" ? "Error: " + r.error : (res.out || "(no output)")}\n[exit ${res.rc != null ? res.rc : "?"}, ${res.seconds || 0}s]`;
      break;
    }
  } catch (e) { TRY.out = e.message; }
  TRY.running = false; dirty = false; lastBody = ""; if (tab === "testing") renderProject();
}
async function tryStart() {
  try { await api(`${tryBase()}/play-start`, {}); } catch (e) { toast(e.message, true); return; }
  tryPoll();
}
async function tryStop() { try { await api(`${tryBase()}/play-stop`, {}); } catch (e) { toast(e.message, true); } TRY.play = { running: false, log: (TRY.play || {}).log }; lastBody = ""; renderProject(); }
async function tryPoll() {
  try { TRY.play = await api(`${tryBase()}/x/play`); } catch (e) { return; }
  if (tab === "testing") { lastBody = ""; renderProject(); }
  if (TRY.play.running && tab === "testing") setTimeout(tryPoll, 2000);
}
