"""Per-project dashboard snapshot (`/api/project/<name>/dashboard`): everything needed to follow one project's agent at a glance.
Read-only, standard library only, no model calls. Sections: status, current task and step, this iteration, queue (in pick order),
backlog by layer, outcomes, quality, model time, triage, blocked tasks, task journal and findings."""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.cfgit import UNLAYERED
from dash import diagnostics, history

QUEUE_SIZE = 6
EVENTS_SHOWN = 14
JOURNAL_LINES = 14
STEPS = ["plan", "implement", "build", "e2e", "security", "review", "cleanup", "docs", "merge"]


def _json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def queue(backlog: list[dict], limit: int = QUEUE_SIZE) -> list[dict]:
    """The pending tasks in the order the agent will pick them (same key as Backlog.next, without the model-load bonus)."""
    closed = {i["id"] for i in backlog if i.get("status") in ("done", "cancelled")}
    pend = [i for i in backlog if i.get("status") == "pending"]
    ready = [i for i in pend if all(d in closed for d in i.get("depends_on", []))]
    waiting = [i for i in pend if i not in ready]

    def key(i):
        layer = i["layer"] if i.get("layer") is not None else UNLAYERED
        return (i.get("source") == "user", i.get("type") == "bug", -layer, i.get("priority", 0) + i.get("dyn", 0))
    rows = []
    for n, i in enumerate(sorted(ready, key=key, reverse=True) + waiting):
        rows.append({"id": i["id"], "title": i["title"], "type": i.get("type"), "layer": i.get("layer"), "priority": i.get("priority"),
                     "dyn": i.get("dyn", 0), "attempts": i.get("attempts", 0), "source": i.get("source"),
                     "waiting_for": [d for d in i.get("depends_on", []) if d not in closed] if i not in ready else []})
        if n + 1 >= limit:
            break
    return rows


def layers(backlog: list[dict], blueprint: dict | None) -> list[dict]:
    """Done / total tasks per build layer."""
    names = {n: ly.get("name", "") for n, ly in enumerate((blueprint or {}).get("layers", []))}
    out: dict[int | None, dict] = {}
    for i in backlog:
        if i.get("status") == "cancelled":
            continue
        row = out.setdefault(i.get("layer"), {"layer": i.get("layer"), "name": names.get(i.get("layer"), ""), "done": 0, "total": 0, "blocked": 0})
        row["total"] += 1
        row["done"] += i.get("status") == "done"
        row["blocked"] += i.get("status") == "blocked"
    return sorted(out.values(), key=lambda r: (r["layer"] is None, r["layer"] or 0))


def last_run_dir(agent: Path) -> Path | None:
    runs = sorted((p for p in (agent / "runs").glob("*") if p.is_dir()), key=lambda p: p.stat().st_mtime) if (agent / "runs").is_dir() else []
    return runs[-1] if runs else None


def journal(agent: Path) -> list[str]:
    run = last_run_dir(agent)
    try:
        lines = (run / "journal.md").read_text("utf-8").splitlines() if run else []
    except OSError:
        lines = []
    return [l for l in lines if l.startswith("- [")][-JOURNAL_LINES:]


def findings(snap: dict) -> list[dict]:
    """What needs attention, most urgent first."""
    out = []
    st, h = snap["status"], snap["health"]
    if h.get("stalled"):
        out.append({"level": "bad", "text": f"No activity for {round((h.get('idle_s') or 0) / 60)} min while the agent is running: check Ollama / the live log."})
    if st["llm_outages"]:
        out.append({"level": "bad", "text": f"The model server failed {st['llm_outages']}x in a row; the agent waits and retries."})
    if st["failures"] >= 3:
        out.append({"level": "bad", "text": f"{st['failures']} consecutive failures: the plan is re-evaluated automatically."})
    if snap["backlog"].get("blocked", 0):
        out.append({"level": "warn", "text": f"{snap['backlog']['blocked']} blocked task(s): see the list below."})
    fr = snap["quality"].get("fail_rate")
    if fr is not None and fr >= 0.5:
        out.append({"level": "warn", "text": f"{round(fr * 100)}% of all attempts fail: a smaller task size or a stronger coder model may help."})
    if snap["status"]["mode"] == "careful":
        out.append({"level": "info", "text": "Careful mode is on: " + (snap["status"]["mode_reason"] or "recent tasks failed")})
    if not st["running"] and snap["backlog"].get("pending", 0):
        out.append({"level": "info", "text": f"The agent is stopped with {snap['backlog']['pending']} open task(s)."})
    return out


def build(ws: Path, running: bool, now: float | None = None) -> dict:
    now = now or time.time()
    a = ws / "agent"
    state, backlog = _json(a / "state.json", {}), _json(a / "backlog.json", [])
    act = _json(a / "activity.json", {}) if running else {}
    counts: dict[str, int] = {}
    for i in backlog:
        counts[i.get("status", "?")] = counts.get(i.get("status", "?"), 0) + 1
    diag = diagnostics.project(ws, running, state.get("elapsed", 0) / 3600, with_repo=True)
    outcomes = state.get("outcomes", [])[-10:]
    events = [e for e in diagnostics._jsonl(a / "events.jsonl", 200_000) if "phase" in e][-EVENTS_SHOWN:]
    task = act.get("task", "") if act else ""
    cur = next((i for i in backlog if task and task.startswith(i["id"])), None)
    merged = state.get("merged", 0)
    snap = {
        "name": ws.name, "time": now,
        "status": {"running": running, "iteration": state.get("iteration", 0), "merged": merged, "hours": round(state.get("elapsed", 0) / 3600, 2),
                   "failures": state.get("consecutive_failures", 0), "llm_outages": state.get("llm_outages", 0),
                   "recent_ok": sum(1 for o in outcomes if o), "recent_total": len(outcomes),
                   "mode": state.get("profile_mode", ""), "mode_reason": state.get("profile_reason", ""),
                   "tasks_per_hour": round(merged / (state["elapsed"] / 3600), 2) if state.get("elapsed", 0) > 600 else None},
        "current": {"task": task, "title": cur["title"] if cur else "", "phase": act.get("phase", "") if act else "", "step": act.get("step") if act else None,
                    "attempt": act.get("attempt", 0) if act else 0, "pct": act.get("pct") if act else None,
                    "since_s": round(now - act["since"]) if act and act.get("since") else None},
        "steps": STEPS, "events": events,
        "backlog": counts, "queue": queue(backlog), "layers": layers(backlog, _json(a / "blueprint.json", None)),
        "blocked": [{"id": i["id"], "title": i["title"], "attempts": i.get("attempts", 0), "note": (i.get("notes") or [""])[-1][:200]}
                    for i in backlog if i.get("status") == "blocked"][:8],
        "triage": {"adjusted": [{"id": i["id"], "title": i["title"], "dyn": i["dyn"]} for i in backlog if i.get("status") == "pending" and i.get("dyn")][:6],
                   "last_layout_at_merge": state.get("triage_at"), "merges_since_layout": merged - state.get("triage_at", 0),
                   "last_replan_at_merge": state.get("auto_replan_at")},
        "outcomes": outcomes, "flow": diag["flow"], "models": diag["models"], "health": diag["health"], "repo": diag.get("repo", {}),
        "forecast": diag.get("forecast"), "quality": history.quality(a), "journal": journal(a),
    }
    snap["findings"] = findings(snap)
    return snap
