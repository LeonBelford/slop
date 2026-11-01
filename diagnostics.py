"""Project and system diagnostics for the dashboard (standard library only, read-only, no model calls).

Key figures and why they matter:
  flow      done / open / blocked tasks, first-try rate and attempts per task (does the agent get it right?), tasks per hour
  quality   review score, failed attempts and their most common causes, consecutive failures (is it stuck?)
  models    time per call and per tag, slowest tags, reasoning share (is time lost in endless thinking?), tokens per call
  health    time since the last activity, stalled agent, errors in the log
  repo      files, lines, tests, commits, last commit age
  system    CPU load, RAM, free disk, Ollama findings (CPU offload, context mismatch)
`findings` turns the figures into a short list of things to act on.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import estimate
from collections import Counter, defaultdict
from pathlib import Path

STALL_SECONDS = 600


def _jsonl(path: Path, max_bytes: int = 3_000_000) -> list[dict]:
    """Last rows of a JSONL file (bounded read; a cut first line is skipped)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in data[1:] if size > max_bytes else data:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _ts(text: str) -> float | None:
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def flow(backlog: list[dict], metrics: list[dict], hours: float) -> dict:
    status = Counter(i.get("status", "?") for i in backlog)
    done = [i for i in backlog if i.get("status") == "done"]
    merged = [m for m in metrics if "kind" not in m and m.get("item")]
    failed = [m for m in metrics if m.get("kind") == "attempt_failed"]
    scores = [m["review_score"] for m in merged if isinstance(m.get("review_score"), (int, float))]
    attempts = sum(max(1, int(i.get("attempts") or 1)) for i in done)
    causes: Counter = Counter()
    for m in failed:
        why = str(m.get("why", ""))
        causes[("tests" if re.search(r"test|assert", why, re.I) else "build" if re.search(r"build|compile|syntax|import", why, re.I)
                else "review" if re.search(r"review", why, re.I) else "security" if re.search(r"secur", why, re.I) else "other")] += 1
    return {"done": len(done), "pending": status.get("pending", 0), "blocked": status.get("blocked", 0), "total": len(backlog),
            "first_try_rate": round(sum(1 for i in done if int(i.get("attempts") or 1) <= 1) / len(done), 2) if done else None,
            "avg_attempts": round(attempts / len(done), 2) if done else None,
            "failed_attempts": len(failed), "fail_causes": dict(causes.most_common(4)),
            "avg_review_score": round(sum(scores) / len(scores), 1) if scores else None,
            "tasks_per_hour": round(len(merged) / hours, 2) if hours and hours > 0.05 else None}


def models(thoughts: list[dict]) -> dict:
    by_tag: dict[str, list[float]] = defaultdict(list)
    by_model: dict[str, list[float]] = defaultdict(list)
    think = answer = 0
    for r in thoughts:
        s = float(r.get("seconds") or 0)
        by_tag[f"{r.get('role', '?')}/{r.get('tag', '?')}"].append(s)
        by_model[r.get("model") or "?"].append(s)
        think += len(r.get("thinking") or "")
        answer += len(r.get("answer") or "")
    total = sum(sum(v) for v in by_tag.values())
    slow = sorted(({"tag": t, "calls": len(v), "avg_s": round(sum(v) / len(v), 1), "max_s": round(max(v), 1), "total_s": round(sum(v))}
                   for t, v in by_tag.items() if v), key=lambda x: -x["total_s"])[:5]
    return {"calls": len(thoughts), "total_s": round(total), "avg_call_s": round(total / len(thoughts), 1) if thoughts else None,
            "reasoning_share": round(think / (think + answer), 2) if think + answer else None, "slowest": slow,
            "by_model": {m: {"calls": len(v), "avg_s": round(sum(v) / len(v), 1)} for m, v in by_model.items()}}


def health(agent_dir: Path, running: bool, now: float | None = None) -> dict:
    now = now or time.time()
    stamps = [p.stat().st_mtime for n in ("agent.log", "llm_now.json", "llm_stream.json", "coder_live.txt") if (p := agent_dir / n).exists()]
    idle = round(now - max(stamps)) if stamps else None
    errors = 0
    try:
        log = (agent_dir / "agent.log").read_text("utf-8", "replace")[-60_000:].splitlines()[-400:]
        errors = sum(1 for l in log if re.search(r"\b(error|failed|traceback|unreachable)\b", l, re.I))
    except OSError:
        pass
    return {"idle_s": idle, "stalled": bool(running and idle is not None and idle > STALL_SECONDS), "log_errors": errors}


def repo_stats(repo: Path, now: float | None = None) -> dict:
    if not (repo / ".git").exists():
        return {}

    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, timeout=8).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    files = [f for f in git("ls-files").splitlines() if f]
    loc = 0
    for f in files[:400]:
        try:
            if (repo / f).stat().st_size < 400_000:
                loc += (repo / f).read_bytes().count(b"\n")
        except OSError:
            pass
    last = git("log", "-1", "--format=%ct").strip()
    return {"files": len(files), "loc": loc, "tests": sum(1 for f in files if re.search(r"(^|/)(tests?/|test_|.*\.(test|spec)\.)", f)),
            "commits": int(git("rev-list", "--count", "HEAD").strip() or 0),
            "last_commit_age_s": round((now or time.time()) - int(last)) if last.isdigit() else None}


def project(ws: Path, running: bool, hours: float, with_repo: bool = True) -> dict:
    a = ws / "agent"
    backlog = []
    try:
        backlog = json.loads((a / "backlog.json").read_text("utf-8"))
    except (OSError, ValueError):
        pass
    out = {"name": ws.name, "flow": flow(backlog, _jsonl(a / "metrics.jsonl"), hours), "models": models(_jsonl(a / "thoughts.jsonl")),
           "health": health(a, running)}
    try:
        cur = json.loads((a / "activity.json").read_text("utf-8")) if running else None
    except (OSError, ValueError):
        cur = None
    out["forecast"] = estimate.forecast(estimate.load(a / "durations.jsonl"), backlog, cur)
    out["durations"] = len(estimate.load(a / "durations.jsonl"))
    out["write_problems"] = estimate.write_problems([a, ws / "repo"], fix=False) if (ws / "repo").exists() else []
    if with_repo:
        out["repo"] = repo_stats(ws / "repo")
    return out


def system(ollama: list[dict] | None = None) -> dict:
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("MemTotal", "MemAvailable"):
                mem[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    disk = shutil.disk_usage(Path(__file__).resolve().parent)
    return {"cpus": os.cpu_count(), "load1": round(load, 2) if load is not None else None, "ram_total_mb": mem.get("MemTotal"),
            "ram_free_mb": mem.get("MemAvailable"), "disk_free_gb": round(disk.free / 1e9, 1), "ollama": ollama or []}


def dur(s: float | None) -> str:
    return "?" if s is None else f"{round(s)}s" if s < 90 else f"{round(s / 60)} min" if s < 5400 else f"{s / 3600:.1f} h"


def tool_durations(out_dir: Path) -> dict:
    """Self-improve step timings from its own durations file (median, spread, count)."""
    rows = estimate.load(Path(out_dir) / "durations.jsonl")
    if not rows:
        return {"n": 0, "median_s": None, "low": None, "high": None}
    v = [r["seconds"] for r in rows]
    return {"n": len(v), "median_s": round(estimate._q(v, 0.5)), "low": round(estimate._q(v, 0.25)), "high": round(estimate._q(v, 0.75))}


def findings(projects: list[dict], sysinfo: dict, running: set[str] | None = None) -> list[dict]:
    """What to act on, most important first. Each item: level (bad|warn|info), text."""
    out: list[dict] = []
    for p in projects:
        f, m, h, n = p["flow"], p["models"], p["health"], p["name"]
        if h["stalled"]:
            out.append({"level": "bad", "text": f"{n}: no activity for {h['idle_s'] // 60} min while running. Check `ollama ps` and the live log."})
        for msg in p.get("write_problems") or []:
            out.append({"level": "bad", "text": f"{n}: {msg}. The agent cannot write files there; fix the permissions."})
        run = (p.get("forecast") or {}).get("running")
        if run and run["overdue"]:
            out.append({"level": "warn", "text": f"{n}: the running task is taking much longer than estimated ({dur(run['elapsed_s'])} vs ~{dur(run['estimate_s'])})."})
        if f["blocked"]:
            out.append({"level": "warn", "text": f"{n}: {f['blocked']} blocked task(s) failed repeatedly. Reformulate them or lower the scope."})
        if f["first_try_rate"] is not None and f["done"] >= 4 and f["first_try_rate"] < 0.4:
            out.append({"level": "warn", "text": f"{n}: only {int(f['first_try_rate'] * 100)}% of tasks pass on the first try. Smaller tasks or a stronger coder help."})
        if m["reasoning_share"] is not None and m["reasoning_share"] > 0.7 and m["calls"] >= 5:
            out.append({"level": "warn", "text": f"{n}: {int(m['reasoning_share'] * 100)}% of the model output is reasoning. Use a non-thinking model for planning or lower limits.think_chars."})
        if m["slowest"] and m["slowest"][0]["avg_s"] > 120:
            s = m["slowest"][0]
            out.append({"level": "info", "text": f"{n}: slowest step is {s['tag']} (avg {s['avg_s']}s over {s['calls']} calls)."})
    if sysinfo.get("disk_free_gb") is not None and sysinfo["disk_free_gb"] < 5:
        out.append({"level": "bad", "text": f"Only {sysinfo['disk_free_gb']} GB disk space left."})
    if sysinfo.get("ram_free_mb") is not None and sysinfo["ram_free_mb"] < 1500:
        out.append({"level": "warn", "text": f"Only {sysinfo['ram_free_mb']} MB RAM free: Ollama offloading will swap."})
    out += [f for f in sysinfo.get("ollama", []) if f.get("level") in ("bad", "warn")]
    order = {"bad": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda x: order.get(x["level"], 3))
