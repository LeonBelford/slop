"""Daily report: what was built, what is blocked and what needs the user, per project, sent through the notify webhook."""
from __future__ import annotations

import time
from pathlib import Path

from core.cfgit import Backlog
from infra import notify


def project_lines(name: str, backlog: list[dict], since: float) -> list[str]:
    done = [i for i in backlog if i.get("status") == "done" and (i.get("done_at") or i.get("updated") or 0) >= since]
    blocked = [i for i in backlog if i.get("status") in ("blocked", "cancelled")]
    open_ = [i for i in backlog if i.get("status") not in ("done", "blocked", "cancelled")]
    out = [f"{name}: {len(done)} built, {len(blocked)} blocked, {len(open_)} open"]
    out += [f"  built: {i['id']} {i['title']}" for i in done[:5]]
    out += [f"  needs you: {i['id']} {i['title']} ({str(i.get('reason') or 'no reason recorded')[:120]})" for i in blocked[:5]]
    return out


def build(projects: Path, since_hours: float = 24.0, now: float | None = None) -> str:
    since = (now or time.time()) - since_hours * 3600
    lines: list[str] = []
    for d in sorted(p for p in projects.iterdir() if (p / "agent" / "backlog.json").exists()):
        lines += project_lines(d.name, Backlog(d / "agent" / "backlog.json").items, since)
    return "\n".join(lines) or "No projects."


def send(cfg: dict, projects: Path) -> bool:
    return notify.send(cfg, "report", "CodeFactory", "daily report\n" + build(projects), wait=True)
