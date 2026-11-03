"""Periodic status digest for the notification service: instead of one push per task the user gets a short summary every N minutes
(key figures of the period), quiet hours are respected, and one alert fires when nothing has progressed for a long time.
Urgent events (blocked, stalled, stopped) are sent at once by notify.send. Pure functions plus tick(agent)."""
from __future__ import annotations

import time
from collections import Counter

from infra import notify

DEFAULTS = {"digest_minutes": 60, "quiet": "", "idle_minutes": 90}


def in_quiet(spec: str, hhmm: str) -> bool:
    """spec like '23:00-07:00' (may wrap over midnight); empty = never quiet."""
    try:
        a, b = (x.strip() for x in spec.split("-"))
    except ValueError:
        return False
    return (a <= hhmm < b) if a <= b else (hhmm >= a or hhmm < b)


def _fmt_min(s: float) -> str:
    return f"{round(s / 60)} min" if s < 5400 else f"{s / 3600:.1f} h"


def build(done: list[dict], fails: list[dict], backlog: list[dict], current: str, period_s: float, merged: int, running: bool) -> str:
    """The message text: what happened in this period, what is running, what is left."""
    lines = []
    secs = sum(float(i.get("runtime_s") or 0) for i in done)
    lines.append(f"{len(done)} task(s) done in the last {_fmt_min(period_s)}" + (f", {_fmt_min(secs)} of work" if secs else ""))
    for i in done[-3:]:
        lines.append(f"  + {i['id']} {str(i.get('title', ''))[:60]}")
    if fails:
        causes = ", ".join(f"{k} {n}x" for k, n in Counter(f.get("cls", "other") for f in fails).most_common(3))
        lines.append(f"{len(fails)} failed attempt(s): {causes}")
    open_n = sum(1 for i in backlog if i.get("status") in ("pending", "in_progress"))
    blocked = sum(1 for i in backlog if i.get("status") == "blocked")
    lines.append(f"{open_n} open" + (f", {blocked} blocked" if blocked else "") + f" - {merged} merged in total")
    lines.append(f"now: {current}" if current else ("running" if running else "agent is stopped"))
    return "\n".join(lines)


def tick(agent, now: float | None = None) -> None:
    """Called from the agent loop; sends at most one digest per period and one idle alert per stall. Never raises."""
    try:
        _tick(agent, time.time() if now is None else now)
    except Exception:  # noqa: BLE001  # notifications must never disturb the agent
        pass


def _tick(agent, now: float) -> None:
    n = {**DEFAULTS, **(agent.cfg.get("notify") or {})}
    if not n.get("webhook"):
        return
    st = agent.state.setdefault("notify", {})
    done_ids = {i["id"] for i in agent.backlog.items if i.get("status") == "done"}
    first = "at" not in st
    new = sorted(done_ids - set(st.get("done", [])))
    if first or done_ids - set(st.get("seen", st.get("done", []))):
        st["last_progress"], st["idle_sent"] = now, False
    st["seen"] = sorted(done_ids)
    if first:
        st.update(at=now, done=sorted(done_ids), since=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))
        return
    idle_s = float(n["idle_minutes"]) * 60
    if idle_s and now - st.get("last_progress", now) > idle_s and not st.get("idle_sent"):
        st["idle_sent"] = True
        notify.send(agent.cfg, "idle", agent.ws.name, f"nothing finished for {_fmt_min(now - st['last_progress'])}. Now: {agent.cur_task or 'no task'}")
    every = float(n["digest_minutes"]) * 60
    if not every or now - st["at"] < every or in_quiet(str(n.get("quiet") or ""), time.strftime("%H:%M", time.localtime(now))):
        return
    from dash import history
    fails = [e for e in history.events(agent.dir) if e.get("kind") == "attempt_failed" and e.get("time", "") > st.get("since", "")]
    done = [i for i in agent.backlog.items if i["id"] in new]
    text = build(done, fails, agent.backlog.items, agent.cur_task, now - st["at"], int(agent.state.get("merged", 0)), True)
    notify.send(agent.cfg, "digest", agent.ws.name, text)
    st.update(at=now, done=sorted(done_ids), since=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))
