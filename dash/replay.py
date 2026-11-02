"""Run replay: play a failed (or any) task again in a sandbox copy of the project, with another model or an extra hint.

The copy lives in `<projects>/.replay/<project>-<task>-<stamp>/` (hidden from the project list), so the original project and its
git history stay untouched. The result is logged to `agent/replays.jsonl` of the original so the UI can show how it went.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from core.cfutil import write_json

ID_RE = re.compile(r"T\d{4,}")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,99}$")
SKIP = shutil.ignore_patterns("node_modules", "__pycache__", ".venv", "target", "STOP", "agent.pid", "*.lock")


def task_text(item: dict, hint: str = "") -> str:
    """The task as a directed instruction: title, description, acceptance criteria and (optionally) the user's hint."""
    parts = [item.get("title", ""), item.get("description", "")]
    if item.get("acceptance"):
        parts.append("Acceptance:\n" + "\n".join(f"- {a}" for a in item["acceptance"]))
    if item.get("reason"):
        parts.append(f"It was blocked before: {item['reason']}")
    if hint.strip():
        parts.append(f"Hint for this replay: {hint.strip()}")
    return "\n\n".join(p for p in parts if p)


def validate(ws: Path, task_id: str, model: str = "") -> dict:
    """The backlog item to replay. Raises ValueError on a bad id, bad model name or unknown task."""
    if not ID_RE.fullmatch(task_id or ""):
        raise ValueError("Invalid task id")
    if model and not MODEL_RE.fullmatch(model):
        raise ValueError("Invalid model name")
    try:
        items = json.loads((ws / "agent" / "backlog.json").read_text("utf-8"))
    except (OSError, ValueError):
        raise ValueError("The project has no backlog") from None
    item = next((i for i in items if i.get("id") == task_id), None)
    if not item:
        raise ValueError(f"Task {task_id} not found")
    return item


def prepare(ws: Path, task_id: str, model: str = "", hint: str = "") -> dict:
    """Copies the project, applies the model override and returns {"dest", "text", ...} for the replay run."""
    item = validate(ws, task_id, model)
    dest = ws.parent / ".replay" / f"{ws.name}-{task_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ws, dest, ignore=SKIP)
    cfg_path = dest / "agent" / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text("utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if model:
        cfg["coder_model"] = f"ollama_chat/{model}"
        cfg.setdefault("models", {})["planner"] = model
    write_json(cfg_path, cfg)
    return {"dest": dest, "text": task_text(item, hint), "model": model, "hint": hint}


def record(ws: Path, task_id: str, info: dict, status: str, seconds: float) -> None:
    row = {"task": task_id, "model": info.get("model", ""), "hint": info.get("hint", ""), "dest": str(info["dest"]), "status": status,
           "seconds": round(seconds), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(ws / "agent" / "replays.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def history(agent: Path, limit: int = 10) -> list[dict]:
    try:
        rows = [json.loads(line) for line in (agent / "replays.jsonl").read_text("utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:][::-1]


def outcome(dest: Path) -> str:
    """done/blocked/... of the replayed (user) task in the sandbox copy."""
    try:
        items = json.loads((dest / "agent" / "backlog.json").read_text("utf-8"))
    except (OSError, ValueError):
        return "unknown"
    mine = [i for i in items if i.get("source") == "user"]
    return mine[-1]["status"] if mine else "unknown"


__all__ = ["prepare", "record", "history", "outcome", "task_text"]
