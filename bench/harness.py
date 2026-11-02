"""Benchmark harness: fixed mini tasks that make model, prompt and pipeline changes comparable (standard library only).

A run executes every task of `bench/tasks.json` in a fresh workspace through a `runner` and checks the result with the task's
own check (a Python snippet run inside the repository). The report records success, time and attempts per task; `compare`
turns two reports into an A/B verdict, which is the evidence for merging a change to the pipeline or to self-improve.
`agent_runner` is the real runner (needs Ollama and Aider); tests use fake runners.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CHECK_TIMEOUT = 60


def load_tasks(path: Path | None = None, only: list[str] | None = None) -> list[dict]:
    tasks = json.loads((path or HERE / "tasks.json").read_text("utf-8"))
    return [t for t in tasks if not only or t["id"] in only]


def check(task: dict, repo: Path) -> tuple[bool, str]:
    """Runs the task's acceptance snippet against the repository the runner produced."""
    try:
        p = subprocess.run([sys.executable, "-c", task["check"]], cwd=repo, capture_output=True, text=True, timeout=CHECK_TIMEOUT,
                           env={"PYTHONPATH": str(repo), "PATH": "/usr/bin:/bin:/usr/local/bin"})
    except subprocess.TimeoutExpired:
        return False, "check timed out"
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-300:]


def run_task(task: dict, runner, root: Path) -> dict:
    ws = Path(tempfile.mkdtemp(prefix=f"{task['id']}-", dir=root))
    t0 = time.time()
    try:
        info = runner(task, ws) or {}
        ok, why = check(task, ws / "repo")
    except Exception as e:  # a crashing pipeline is a failed task, not a crashed benchmark
        info, ok, why = {}, False, f"runner crashed: {e}"
    return {"id": task["id"], "ok": ok, "seconds": round(time.time() - t0, 1), "attempts": info.get("attempts"), "why": "" if ok else why}


def run_suite(tasks: list[dict], runner, label: str, meta: dict | None = None, results_dir: Path | None = None) -> dict:
    out = Path(results_dir or RESULTS)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bench-") as tmp:
        rows = [run_task(t, runner, Path(tmp)) for t in tasks]
    report = {"label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "meta": meta or {}, "results": rows, **summarize(rows)}
    (out / f"{label}.json").write_text(json.dumps(report, indent=1), "utf-8")
    return report


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    att = [r["attempts"] for r in rows if isinstance(r.get("attempts"), (int, float))]
    return {"tasks": n, "passed": sum(1 for r in rows if r["ok"]), "success_rate": round(sum(1 for r in rows if r["ok"]) / n, 2) if n else None,
            "seconds": round(sum(r["seconds"] for r in rows), 1), "avg_attempts": round(sum(att) / len(att), 2) if att else None}


def compare(a: dict, b: dict) -> dict:
    """A/B verdict of report b against baseline a on the tasks both ran: success first, then time (more than 10% faster/slower)."""
    ra, rb = {r["id"]: r for r in a["results"]}, {r["id"]: r for r in b["results"]}
    ids = [i for i in ra if i in rb]
    fixed = [i for i in ids if not ra[i]["ok"] and rb[i]["ok"]]
    broke = [i for i in ids if ra[i]["ok"] and not rb[i]["ok"]]
    sa, sb = summarize([ra[i] for i in ids]), summarize([rb[i] for i in ids])
    if broke or sb["passed"] < sa["passed"]:
        verdict = "worse"
    elif fixed or sb["passed"] > sa["passed"]:
        verdict = "better"
    elif sa["seconds"] and sb["seconds"] < sa["seconds"] * 0.9:
        verdict = "better"
    elif sa["seconds"] and sb["seconds"] > sa["seconds"] * 1.1:
        verdict = "worse"
    else:
        verdict = "same"
    return {"verdict": verdict, "tasks": len(ids), "fixed": fixed, "broke": broke, "a": sa, "b": sb,
            "seconds_delta": round(sb["seconds"] - sa["seconds"], 1)}


def load_results(results_dir: Path | None = None, limit: int = 12) -> list[dict]:
    """Stored reports (newest first), without the per-task rows, for the dashboard."""
    rows = []
    for f in sorted(Path(results_dir or RESULTS).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({k: d.get(k) for k in ("label", "time", "meta", "tasks", "passed", "success_rate", "seconds", "avg_attempts")})
    return rows


def deep_merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def agent_runner(overrides: dict | None = None):
    """The real runner: one directed task in a fresh minimal-stack project. `overrides` is merged into the config (features, models, ...)."""
    def run(task: dict, ws: Path) -> dict:
        import codefactory as cf
        from core.cfutil import write_json, write_text
        (ws / "agent").mkdir(parents=True)
        cfg = json.loads(json.dumps(cf.DEFAULT_CONFIG))
        preset = cf.PRESETS["minimal"]
        cfg.update(preset="minimal", commands=preset["commands"], stack=preset["stack"], src_dirs=preset["src_dirs"])
        deep_merge(cfg, overrides or {})
        write_json(ws / "agent" / "config.json", {k: cfg[k] for k in ("preset", "doc_language", "models", "coder_model", "features")})
        a = cf.Agent(ws, cfg)
        write_text(a.dir / "GOAL.md", f"# Benchmark {task['id']}\n\n{task['title']}\n")
        a.git.ensure()
        for rel, content in {**cf.stacks.scaffold("minimal"), **task.get("seed", {})}.items():
            write_text(a.repo / rel, content)
        a.git.commit_all("scaffold")
        a.mem.sync()
        item = a.add_user_task(task["prompt"])
        a.run_user_tasks()
        return {"attempts": item.get("attempts")}
    return run
