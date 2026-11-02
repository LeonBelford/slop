"""Benchmark CLI.

  python3 bench/run.py --label base                       # run all tasks with the current pipeline
  python3 bench/run.py --label nogate --features size_gate=false,layer_gate=false --model qwen2.5-coder:14b
  python3 bench/run.py --compare base nogate              # A/B verdict of two stored reports

Needs Ollama and Aider like a normal project run; results are stored in bench/results/<label>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench import harness  # noqa: E402


def _flags(text: str) -> dict:
    out = {}
    for part in filter(None, (text or "").split(",")):
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip().lower() not in ("false", "0", "off", "no")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", help="Name of this run (stored as bench/results/<label>.json)")
    ap.add_argument("--features", default="", help="Feature flags to set, e.g. size_gate=false,spec_first=true")
    ap.add_argument("--model", default="", help="Ollama model for the coder (overrides the config)")
    ap.add_argument("--tasks", default="", help="Comma-separated task ids (default: all)")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="Compare two stored reports")
    a = ap.parse_args()
    if a.compare:
        reports = [json.loads((harness.RESULTS / f"{n}.json").read_text("utf-8")) for n in a.compare]
        res = harness.compare(*reports)
        print(json.dumps(res, indent=1))
        return 0 if res["verdict"] != "worse" else 1
    if not a.label:
        ap.error("--label is required for a run")
    overrides: dict = {"features": _flags(a.features)}
    if a.model:
        overrides["coder_model"] = f"ollama_chat/{a.model}"
    tasks = harness.load_tasks(only=[t for t in a.tasks.split(",") if t])
    rep = harness.run_suite(tasks, harness.agent_runner(overrides), a.label, meta={"features": overrides["features"], "model": a.model})
    for r in rep["results"]:
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['id']:<22} {r['seconds']:>7}s attempts={r['attempts']} {r['why'][:80]}")
    print(f"{rep['passed']}/{rep['tasks']} passed in {rep['seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
