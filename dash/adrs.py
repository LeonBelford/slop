"""Decision log (ADRs) with search and the project health score."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from core.cfutil import read_json
from dash import codesearch, refactor, testgen
from infra import knowledge

WEIGHTS = {"tests": 35, "modules": 20, "drift": 15, "blockers": 30}


def decisions(agent_dir: Path, query: str = "", limit: int = 60) -> list[dict]:
    """ADRs, newest first. With a query the project's knowledge index (the same search the chat uses) ranks them; without an index
    the query must match all of its words in any field."""
    rows = []
    try:
        for line in (agent_dir / "memory" / "decisions.jsonl").read_text("utf-8", "replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        return []
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    if not words:
        return rows[::-1][:limit]
    kb = knowledge.open_readonly(agent_dir / "memory" / "knowledge.db")
    if kb:
        try:
            refs = [h["ref"] for h in kb.search(query, ("decision",), limit)]
            by_ref = {f"{r.get('item', '')}:{r.get('title', '')}"[:120]: r for r in rows}
            found = [by_ref[x] for x in refs if x in by_ref]
            if found:
                return found
        except Exception:  # noqa: BLE001  (a broken index falls back to the plain filter)
            pass
    return [r for r in rows[::-1] if all(w in json.dumps(r, ensure_ascii=False).lower() for w in words)][:limit]


def health(agent_dir: Path, repo: Path) -> dict:
    """0-100 score from four parts with a hint each: tests exist for the files, modules stay small, no open drift, few blocked tasks."""
    items = read_json(agent_dir / "backlog.json", [])
    live = [i for i in items if i.get("status") != "cancelled"]
    blocked = sum(1 for i in live if i.get("status") == "blocked")
    drift = sum(1 for i in live if i.get("source") == "drift" and i.get("status") != "done")
    parts: dict[str, dict] = {}
    try:
        src = [f for f in codesearch.files(repo) if not testgen.is_test(f)]
        bad = testgen.untested(repo, 10_000)
        big = refactor.big_modules(repo)
    except (OSError, ValueError, subprocess.SubprocessError):
        src, bad, big = [], [], []
    parts["tests"] = {"value": 1 - len(bad) / len(src) if src else 1.0, "hint": f"{len(bad)} of {len(src)} source files have no test"}
    parts["modules"] = {"value": 1 - len(big) / len(src) if src else 1.0, "hint": f"{len(big)} module(s) over 500 lines"}
    parts["drift"] = {"value": max(0.0, 1 - drift / 5), "hint": f"{drift} open drift fix(es)"}
    parts["blockers"] = {"value": 1 - blocked / len(live) if live else 1.0, "hint": f"{blocked} of {len(live)} tasks blocked"}
    score = round(sum(WEIGHTS[k] * max(0.0, min(1.0, p["value"])) for k, p in parts.items()))
    for k, p in parts.items():
        p["value"], p["weight"] = round(p["value"], 2), WEIGHTS[k]
    return {"score": score, "parts": parts, "untested": bad[:8], "big": big[:5]}
