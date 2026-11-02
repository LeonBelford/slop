"""Benchmark gate for self-improve: a change to the tool must not lower the success rate on the fixed benchmark tasks.

Mode via the environment variable SI_BENCH_GATE: `off` (default, the benchmark needs hours), `warn` (the result is shown, the branch is
kept) or `block` (a worse benchmark drops the branch). The baseline is bench/results/base.json of the checkout (`make bench LABEL=base`);
without a baseline the gate only says so. The candidate runs in the worktree, so it uses the changed code.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BASELINE = "base"


def mode() -> str:
    m = os.getenv("SI_BENCH_GATE", "off").strip().lower()
    return m if m in ("warn", "block") else "off"


def _run_bench(work: Path, label: str, timeout: int) -> dict | None:
    try:
        subprocess.run([sys.executable, "bench/run.py", "--label", label], cwd=work, timeout=timeout, capture_output=True, text=True, check=False)
        return json.loads((work / "bench" / "results" / f"{label}.json").read_text("utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def evaluate(repo: Path, work: Path, stamp: str, log, run=_run_bench, timeout: int = 4 * 3600) -> dict:
    """{"mode", "verdict": better|same|worse|skipped|error, "detail", "block"}; never raises."""
    m = mode()
    if m == "off":
        return {"mode": m, "verdict": "skipped", "detail": "bench gate is off (SI_BENCH_GATE=warn|block)", "block": False}
    try:
        base = json.loads((Path(repo) / "bench" / "results" / f"{BASELINE}.json").read_text("utf-8"))
    except (OSError, ValueError):
        log("  bench gate: no baseline (run `make bench LABEL=base` first), skipped")
        return {"mode": m, "verdict": "skipped", "detail": "no baseline", "block": False}
    from bench import harness
    log("▶ [bench] running the benchmark against the changed code (this takes long)")
    new = run(Path(work), f"si-{stamp}", timeout)
    if not new:
        return {"mode": m, "verdict": "error", "detail": "the benchmark did not produce a result", "block": False}
    cmp = harness.compare(base, new)
    log(f"  bench gate: {cmp['verdict']} (fixed {cmp.get('fixed')}, broke {cmp.get('broke')})")
    return {"mode": m, "verdict": cmp["verdict"], "detail": f"fixed {cmp.get('fixed')}, broke {cmp.get('broke')}",
            "block": m == "block" and cmp["verdict"] == "worse"}
