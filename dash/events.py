"""Project event log and the "what happened" timeline: UI actions (agent/events.jsonl) merged with pipeline metrics and finished tasks."""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.cfutil import clip, read_json
from dash import history

ICONS = {"layer_done": "layer", "layer_gate_red": "gate", "split": "split", "spike": "spike", "replan": "plan", "reevaluate": "plan",
         "not_essential": "cancel", "attempt_failed": "fail", "stalled": "stall", "llm_outage": "fail", "milestone": "layer",
         "security_audit": "security", "rollback": "rollback", "control": "control", "review": "review", "export": "export", "task_done": "done"}


def add(agent_dir: Path, kind: str, text: str, **extra) -> None:
    """Records something the user or the UI did. Never raises: bookkeeping must not break an action."""
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        with open(agent_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "text": clip(text, 300), **extra}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _rows(path: Path, limit: int) -> list[dict]:
    out = []
    try:
        lines = path.read_text("utf-8", "replace").splitlines()[-limit:]
    except OSError:
        return out
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _metric_text(m: dict) -> str:
    parts = [f"{k}={v}" for k, v in m.items() if k not in ("time", "kind") and isinstance(v, (str, int, float)) and v != ""]
    return clip(", ".join(parts), 200)


def timeline(agent_dir: Path, limit: int = 150) -> list[dict]:
    """Newest first: events, metrics of the interesting kinds, and finished tasks (from durations.jsonl)."""
    rows = [{"time": e.get("time", ""), "kind": e.get("kind", "event"), "text": e.get("text", "")} for e in _rows(agent_dir / "events.jsonl", 400)]
    for m in history.events(agent_dir, 1500):
        if m.get("kind") in ICONS:
            rows.append({"time": m.get("time", ""), "kind": m["kind"], "text": _metric_text(m)})
    for d in _rows(agent_dir / "durations.jsonl", 400):
        if d.get("kind", "task") == "task":
            rows.append({"time": d.get("time", ""), "kind": "task_done" if d.get("ok") else "attempt_failed",
                         "text": f"{d.get('title', '')} ({round(float(d.get('seconds', 0)) / 60, 1)} min, {d.get('attempts', 1)} attempt(s))"})
    for r in rows:
        r["icon"] = ICONS.get(r["kind"], "event")
    rows.sort(key=lambda r: str(r["time"]), reverse=True)
    return rows[:limit]


def backlog(agent_dir: Path) -> list[dict]:
    return read_json(agent_dir / "backlog.json", [])
