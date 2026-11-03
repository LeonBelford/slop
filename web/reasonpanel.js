// Reasoning on top of the task graph: the current step open, older steps (a chat-like history) folded underneath.
let RP = null;
const RPO = { now: true, old: false };
async function rpFetch() {
  const n = sel;
  try { RP = { ...(await api(`/api/project/${encodeURIComponent(n)}/thoughts?limit=24&brief=1`)), name: n }; } catch (e) { /* keep the last data */ }
}
const rpOpen = k => (RPO[k] ? "open" : "");
const rpTog = el => { RPO[el.dataset.rk] = el.open; };
function rpStep(e) {
  const [cls, label] = tagInfo(e.tag);
  return `<details class="rp-step" data-rk="s${e.n}" ${rpOpen("s" + e.n)} ontoggle="rpTog(this)"><summary><span class="pill ${pillCls(cls)}">${esc(label)}</span> <span class="meta">#${e.n} · ${esc(e.role)}${e.task ? ` · task ${esc(e.task)}` : ""} · ${esc(e.time)}</span></summary>${thoughtCard(e, 0)}</details>`;
}
function rpPanel() {
  const es = RP.entries || [], cur = es[0], old = es.slice(1);
  const live = detail && detail.activity && detail.activity.running;
  return `<details class="panel rp" data-rk="now" ${rpOpen("now")} ontoggle="rpTog(this)"><summary><b>Reasoning: current step</b> <span class="meta">${live ? "live" : "agent idle"}${cur ? ` · last call #${cur.n}` : ""}</span></summary>
    <div id="reasonlive"></div>
    ${cur ? `<div class="reason-list">${thoughtCard(cur, 0)}</div>` : '<div class="hint">Nothing recorded yet. Every thought appears here as soon as the agent works.</div>'}</details>
  <details class="panel rp" data-rk="old" ${rpOpen("old")} ontoggle="rpTog(this)"><summary><b>Old reasoning</b> <span class="meta">${old.length} earlier step(s)${RP.archived ? ` · ${RP.archived} more archived` : ""}</span></summary>
    <div class="reason-list">${old.length ? old.map(rpStep).join("") : '<div class="hint">No earlier steps yet.</div>'}</div></details>`;
}
// Always visible above the tabs, so the reasoning can be followed on every page of the project (not on the Reasoning tab itself: same ids).
function rpRender() {
  const box = $("#rp"); if (!box) return;
  if (view !== "project" || !sel || !RP || RP.name !== sel || tab === "reasoning") { if (box.innerHTML) { box.innerHTML = ""; box.dataset.sig = ""; } return; }
  const live = !!(detail && detail.activity && detail.activity.running);
  const sig = [sel, live, RP.archived, (RP.entries || []).map(e => e.n).slice(0, 24).join(",")].join("|");
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig; liveHtml = "";
  const top = $("#main").scrollTop;
  box.innerHTML = rpPanel();
  $("#main").scrollTop = top;
}
