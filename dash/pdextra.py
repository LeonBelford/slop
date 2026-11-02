"""Extra data for the project dashboard widgets: task timeline, failure causes, time per step, daily tokens/GPU, models per role,
and the detail of one task (journal, notes, plan, failures)."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from dash import diagnostics, history, recommend

TIMELINE_TASKS = 30
MAX_GAP_S = 1800  # a pause longer than this between two events is not time spent in a step


def timeline(backlog: list[dict]) -> list[dict]:
    """Finished and running tasks with their total runtime, laid out one after another (the agent works sequentially)."""
    rows, t = [], 0.0
    for i in backlog:
        if i.get("status") in ("done", "in_progress") and i.get("runtime_s"):
            rows.append({"id": i["id"], "title": i["title"], "start": round(t), "seconds": round(i["runtime_s"]), "attempts": i.get("attempts", 0),
                         "estimate_s": i.get("estimate_s"), "status": i["status"]})
            t += i["runtime_s"]
    return rows[-TIMELINE_TASKS:]


def causes(agent: Path) -> list[dict]:
    """Failure classes with the tasks that hit them (click a task to open it)."""
    by: dict[str, dict] = {}
    for e in history.events(agent):
        if e.get("kind") == "attempt_failed":
            c = by.setdefault(e.get("cls", "other"), {"cls": e.get("cls", "other"), "n": 0, "tasks": {}})
            c["n"] += 1
            c["tasks"][e.get("item", "?")] = e.get("title", "")
    return [{"cls": c["cls"], "n": c["n"], "tasks": [{"id": k, "title": v} for k, v in list(c["tasks"].items())[:6]]}
            for c in sorted(by.values(), key=lambda c: -c["n"])[:8]]


def _ts(v: str) -> float | None:
    try:
        return datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def step_time(agent: Path) -> dict[str, int]:
    """Seconds per pipeline step from the event timeline (time between an event and the next one counts for the earlier step)."""
    ev = [e for e in diagnostics._jsonl(agent / "events.jsonl", 1_500_000) if "phase" in e]
    out: dict[str, float] = {}
    for a, b in zip(ev, ev[1:], strict=False):
        ta, tb = _ts(a.get("time", "")), _ts(b.get("time", ""))
        if ta and tb and a.get("step") and 0 <= tb - ta <= MAX_GAP_S:
            out[a["step"]] = out.get(a["step"], 0) + tb - ta
    return {k: round(v) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def models(agent: Path) -> dict:
    try:
        cfg = json.loads((agent / "config.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    roles = dict(cfg.get("models", {}))
    roles["coder"] = cfg.get("coder_model", "")
    for k in ("light", "heavy", "final_reviewer"):
        if (cfg.get("routing") or {}).get(k):
            roles[k] = cfg["routing"][k]
    return {k: v for k, v in roles.items() if v}


def advice(agent: Path) -> list[dict]:
    try:
        cfg = json.loads((agent / "config.json").read_text("utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return recommend.coder_hint(cfg.get("coder_model", "")) + recommend.recommend(diagnostics._jsonl(agent / "thoughts.jsonl"))


def regressions(agent: Path) -> list[dict]:
    return diagnostics._jsonl(agent / "regressions.jsonl")[-20:]


def extras(ws: Path, backlog: list[dict], state: dict) -> dict:
    a = ws / "agent"
    gates = state.get("layer_gates", {})
    return {"timeline": timeline(backlog), "causes": causes(a), "step_time": step_time(a), "daily": history.daily(a, 14),
            "role_models": models(a), "gates": [{"layer": int(k), **v} for k, v in sorted(gates.items(), key=lambda kv: int(kv[0]))],
            "drift": state.get("drift", [])[:10], "advice": advice(a), "regressions": regressions(a)}


def task_detail(ws: Path, task_id: str) -> dict:
    """Everything known about one task: fields, notes, journal, plan of the last run and the failed attempts."""
    a = ws / "agent"
    if not re.fullmatch(r"T\d{4,}", task_id):
        return {"found": False}
    try:
        items = json.loads((a / "backlog.json").read_text("utf-8"))
    except (OSError, ValueError):
        items = []
    item = next((i for i in items if i.get("id") == task_id), None)
    if item is None:
        return {"found": False}
    runs = sorted((p for p in (a / "runs").glob(f"*-{task_id}") if p.is_dir()), key=lambda p: p.name) if (a / "runs").is_dir() else []
    journal, plan = [], {}
    if runs:
        try:
            journal = [ln for ln in (runs[-1] / "journal.md").read_text("utf-8").splitlines() if ln.startswith("- [")][-40:]
        except OSError:
            pass
        try:
            plan = json.loads((runs[-1] / "plan.json").read_text("utf-8"))
        except (OSError, ValueError):
            pass
    fails = [{"attempt": e.get("attempt"), "cls": e.get("cls"), "why": e.get("why", "")[:300]} for e in history.events(a)
             if e.get("kind") == "attempt_failed" and e.get("item") == task_id][-8:]
    keep = ("id", "title", "type", "status", "priority", "layer", "attempts", "reason", "runtime_s", "estimate_s", "description", "acceptance", "depends_on", "risk")
    return {"found": True, "item": {k: item.get(k) for k in keep}, "notes": [str(n)[:300] for n in item.get("notes", [])][-12:], "journal": journal,
            "plan": {k: plan.get(k) for k in ("understanding", "files_to_change", "new_files", "steps", "spec") if plan.get(k)}, "failures": fails}
