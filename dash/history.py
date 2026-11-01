"""Per-day history and quality figures for the dashboard: tokens, GPU hours, finished and failed tasks, retry and failure rates.
Reads agent/metrics.json (per-day totals written by core.cfllm) and agent/metrics.jsonl (one event per merged task or failed attempt)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path


def events(agent: Path, limit: int = 4000) -> list[dict]:
    f = agent / "metrics.jsonl"
    out: list[dict] = []
    try:
        for line in f.read_text("utf-8").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def daily(agent: Path, days: int = 30, today: date | None = None) -> list[dict]:
    """One row per day for the last `days` days (oldest first), days without activity included as zeros."""
    today = today or date.today()
    try:
        per = json.loads((agent / "metrics.json").read_text("utf-8")).get("days", {})
    except (OSError, ValueError):
        per = {}
    rows = {(today - timedelta(n)).isoformat(): {"day": (today - timedelta(n)).isoformat(), "tokens": 0, "calls": 0, "gpu_hours": 0.0,
                                                 "done": 0, "failed": 0, "flaky": 0, "stalled": 0} for n in range(days)}
    for d, v in per.items():
        if d in rows:
            rows[d].update(tokens=v.get("tokens", 0), calls=v.get("calls", 0), gpu_hours=round(v.get("gen_seconds", 0) / 3600, 2))
    for e in events(agent):
        d = str(e.get("time", ""))[:10]
        if d not in rows:
            continue
        kind = e.get("kind")
        if kind is None and e.get("item"):
            rows[d]["done"] += 1
        elif kind == "attempt_failed":
            rows[d]["failed"] += 1
        elif kind == "flaky_test":
            rows[d]["flaky"] += 1
        elif kind == "stalled":
            rows[d]["stalled"] += 1
    return [rows[k] for k in sorted(rows)]


def quality(agent: Path) -> dict:
    """Failure and retry rates over all recorded events plus the GPU hours spent (None where there is no data yet)."""
    ev = events(agent)
    done = [e for e in ev if e.get("kind") is None and e.get("item")]
    failed = [e for e in ev if e.get("kind") == "attempt_failed"]
    try:
        gen = json.loads((agent / "metrics.json").read_text("utf-8")).get("gen_seconds", 0)
    except (OSError, ValueError):
        gen = 0
    attempts = len(done) + len(failed)
    by_class: dict[str, int] = {}
    for e in failed:
        by_class[e.get("cls", "other")] = by_class.get(e.get("cls", "other"), 0) + 1
    return {"done": len(done), "failed": len(failed),
            "fail_rate": round(len(failed) / attempts, 2) if attempts else None,
            "retry_rate": round(sum(1 for e in done if (e.get("attempts") or 1) > 1) / len(done), 2) if done else None,
            "gpu_hours": round(gen / 3600, 2), "flaky": sum(1 for e in ev if e.get("kind") == "flaky_test"),
            "stalled": sum(1 for e in ev if e.get("kind") == "stalled"), "by_class": by_class}


def merge(series: list[list[dict]]) -> list[dict]:
    """Sums several projects' daily series into one."""
    if not series:
        return []
    out = [dict(r) for r in series[0]]
    for s in series[1:]:
        for a, b in zip(out, s, strict=False):
            for k in a:
                if k != "day":
                    a[k] = round(a[k] + b[k], 2) if isinstance(a[k], float) or isinstance(b[k], float) else a[k] + b[k]
    return out
