"""Duration estimates from comparable past tasks, plus write-access checks (standard library only).

Every finished task (project agent) and every self-improve step appends one row to a per-project `durations.jsonl`:
{time, kind, title, type, words, seconds, attempts, ok}. Nothing is hard-coded: the task categories come from the
keywords of each project's own task titles, so a project that mostly does UI work learns UI timings and a CLI project
learns its own. An estimate is the weighted median of the most similar past tasks (keyword overlap, same type counts
extra); it falls back to the type median, then to the median of everything, and to "no data" for a fresh project.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

STOP = {"with", "that", "this", "from", "into", "when", "have", "will", "should", "would", "make", "also", "than", "then",
        "each", "more", "them", "they", "their", "some", "using", "used", "add", "adds", "for", "the", "and", "new"}
MIN_SIMILAR = 0.2


def words(*texts: str) -> list[str]:
    out = {w for t in texts for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", (t or "").lower()) if w not in STOP}
    return sorted(out)[:40]


def record(path: Path, title: str, seconds: float, type_: str = "feature", attempts: int = 1, ok: bool = True,
           kind: str = "task", desc: str = "") -> None:
    """Appends one finished task. Never raises: bookkeeping must not break the work."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "title": title[:160], "type": type_,
               "words": words(title, desc), "seconds": round(float(seconds), 1), "attempts": attempts, "ok": bool(ok)}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        pass


def load(path: Path, limit: int = 500) -> list[dict]:
    rows = []
    try:
        lines = path.read_text("utf-8").splitlines()[-limit:]
    except OSError:
        return rows
    for line in lines:
        try:
            r = json.loads(line)
            if isinstance(r.get("seconds"), (int, float)) and r["seconds"] > 0:
                rows.append(r)
        except ValueError:
            continue
    return rows


def _q(vals: list[float], q: float) -> float:
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(q * (len(vals) - 1) + 0.5))]


def _wmedian(pairs: list[tuple[float, float]]) -> float:
    pairs = sorted(pairs)
    half, acc = sum(w for _, w in pairs) / 2, 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


def estimate(rows: list[dict], title: str, type_: str = "feature", desc: str = "") -> dict:
    """{seconds, low, high, n, basis: similar|type|all|none, similar: [titles]} for a task that is about to start."""
    mine = set(words(title, desc))
    scored = []
    for r in rows:
        theirs = set(r.get("words") or [])
        jac = len(mine & theirs) / len(mine | theirs) if mine | theirs else 0.0
        scored.append((jac + (0.15 if r.get("type") == type_ else 0.0), jac, r))
    close = sorted((s for s in scored if s[1] >= MIN_SIMILAR), key=lambda s: -s[0])[:7]
    if len(close) >= 2:
        basis, pool = "similar", [(r["seconds"], w) for w, _, r in close]
        names = [r["title"] for _, _, r in close[:3]]
    else:
        same = [r["seconds"] for r in rows if r.get("type") == type_]
        if len(same) >= 2:
            basis, pool, names = "type", [(v, 1.0) for v in same], []
        elif len(rows) >= 3:
            basis, pool, names = "all", [(r["seconds"], 1.0) for r in rows], []
        else:
            return {"seconds": None, "low": None, "high": None, "n": len(rows), "basis": "none", "similar": []}
    vals = [v for v, _ in pool]
    return {"seconds": round(_wmedian(pool)), "low": round(_q(vals, 0.25)), "high": round(_q(vals, 0.75)), "n": len(pool),
            "basis": basis, "similar": names}


def forecast(rows: list[dict], backlog: list[dict], current: dict | None = None) -> dict:
    """Estimates for every open task, the queue total and the remaining time of the running task.
    `current` is {"task": "<id> <title>", "since": epoch} from activity.json."""
    items, total, known = [], 0, 0
    for it in backlog:
        if it.get("status") not in ("pending", "in_progress"):
            continue
        e = estimate(rows, it.get("title", ""), it.get("type", "feature"), it.get("description", ""))
        items.append({"id": it.get("id"), "title": it.get("title", ""), "type": it.get("type", "feature"), "status": it["status"], **e})
        if e["seconds"]:
            total, known = total + e["seconds"], known + 1
    running = None
    if current and current.get("task"):
        tid = str(current["task"]).split(" ", 1)[0]
        cur = next((i for i in items if str(i["id"]) == tid), None)
        if cur:
            el = max(0.0, time.time() - float(current.get("since") or time.time()))
            running = {"title": cur["title"], "elapsed_s": round(el), "estimate_s": cur["seconds"],
                       "left_s": max(0, cur["seconds"] - round(el)) if cur["seconds"] else None, "overdue": bool(cur["seconds"] and el > (cur["high"] or cur["seconds"]) * 1.5)}
    return {"items": items[:30], "open": len(items), "known": known, "total_s": total, "running": running}


def ensure_writable(path: Path, fix: bool = True) -> tuple[bool, str]:
    """Proves the agent can create and delete a file in `path` (a directory). With `fix`, restores the owner's rwx bits once."""
    probe = Path(path) / f".cf-write-test-{os.getpid()}"
    for attempt in (0, 1):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", "utf-8")
            probe.unlink()
            return True, "ok" if attempt == 0 else "fixed permissions"
        except OSError as e:
            if attempt or not fix:
                return False, f"cannot write to {path}: {e.strerror or e}"
            try:
                os.chmod(path, (os.stat(path).st_mode | 0o700))
            except OSError:
                return False, f"cannot write to {path}: {e.strerror or e}"
    return False, f"cannot write to {path}"


def write_problems(paths: list[Path], fix: bool = True) -> list[str]:
    return [m for ok, m in (ensure_writable(p, fix) for p in paths) if not ok]
