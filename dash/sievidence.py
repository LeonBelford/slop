"""Evidence for self-improve beyond raw failure text (standard library only, no model calls).

`suggestions` is a small rule engine over the project diagnostics: every rule names the metric, the measured value and the area of
the tool to change, so a proposal is backed by a number instead of a feeling. `journal_evidence` and `triage_evidence` hand the
task journals and the triage/gate history of a project to the planner. `retry_plan` turns a repeated failure into a concrete
change of strategy (stall detection with a real retry strategy, not only a hint in the prompt).
"""
from __future__ import annotations

import json
from pathlib import Path

RULES = (
    ("reasoning_share", lambda m: m > 0.7, "core prompts / models",
     "Reasoning makes up {pct} of all model output: shorten planner and reviewer prompts or route the coder to a model without reasoning."),
    ("first_try_rate", lambda m: m < 0.4, "planning and spec",
     "Only {pct} of the tasks pass on the first attempt: improve the planner's design step and context selection."),
    ("fail_rate", lambda m: m > 0.5, "debug loop",
     "{pct} of all attempts fail: strengthen the debug loop (diagnosis, retry strategy) before adding features."),
    ("stalled", lambda m: m >= 2, "stall handling",
     "{n} stalled attempts: the same failure repeated; make the retry strategy change the approach earlier."),
    ("flaky", lambda m: m >= 2, "test handling",
     "{n} flaky tests were seen: stabilise or quarantine them so they stop costing attempts."),
    ("avg_call_s", lambda m: m > 120, "context size / model",
     "The average model call takes {n}s: cut the prompt size or use a smaller model for routine roles."),
)


def _pct(v: float) -> str:
    return f"{round(v * 100)}%"


def suggestions(project: dict) -> list[dict]:
    """Rule hits for one project; `project` is a `diagnostics.project(...)` dict plus an optional `quality` dict."""
    flow, models, quality = project.get("flow") or {}, project.get("models") or {}, project.get("quality") or {}
    metrics = {"reasoning_share": models.get("reasoning_share"), "first_try_rate": flow.get("first_try_rate"),
               "fail_rate": quality.get("fail_rate"), "stalled": quality.get("stalled"), "flaky": quality.get("flaky"),
               "avg_call_s": models.get("avg_call_s")}
    out = []
    for key, test, area, text in RULES:
        v = metrics.get(key)
        if isinstance(v, (int, float)) and test(v):
            out.append({"metric": key, "value": v, "area": area, "text": text.format(pct=_pct(v), n=v)})
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except OSError:
        return ""


def journal_evidence(agent: Path, limit: int = 3, chars: int = 600) -> list[dict]:
    """The journals of the latest blocked/cancelled tasks: what was tried, in the agent's own words."""
    try:
        items = json.loads(_read(agent / "backlog.json") or "[]")
    except ValueError:
        return []
    bad = [i for i in items if i.get("status") in ("blocked", "cancelled") and i.get("id")][-limit:]
    out = []
    for i in bad:
        runs = sorted((agent / "runs").glob(f"*-{i['id']}"))
        text = _read(runs[-1] / "journal.md") if runs else ""
        out.append({"task": i["id"], "status": i["status"], "reason": str(i.get("reason", ""))[:200], "journal": text[-chars:]})
    return out


def triage_evidence(agent: Path) -> dict:
    """Priority changes, layer gates and drift of a project: where the planning needed correcting."""
    try:
        state = json.loads(_read(agent / "state.json") or "{}")
    except ValueError:
        return {}
    gates = state.get("layer_gates") or {}
    return {"triage": (state.get("triage_log") or [])[-8:],
            "gate_failures": {k: v for k, v in gates.items() if isinstance(v, dict) and not v.get("ok", True)} if isinstance(gates, dict) else [],
            "drift": state.get("drift") or []}


def retry_plan(tried: list[str], stage: str, have_heavy: bool) -> dict:
    """What to change after repeated failures: {"escalate": use the heavy model, "hint": text}.
    Two identical failures in a row mean the same approach is being repeated, so something other than the prompt must change."""
    same = len(tried) >= 2 and tried[-1] == tried[-2]
    stuck = len(tried) >= 3 and len(set(tried[-3:])) == 1
    if not same:
        return {"escalate": False, "hint": ""}
    hint = "The previous two rounds failed in exactly the same way. Do NOT repeat that approach: change strategy."
    if stage == "implement":
        hint += " Reply only with edit blocks and copy the SEARCH lines exactly from the file."
    if stuck:
        hint += " Three identical failures: reread the failing check, then fix its root cause, not its symptom."
    return {"escalate": have_heavy, "hint": hint}


def run_times(runs: list[dict]) -> list[dict]:
    """Runtime and outcome per self-improve run (for the dashboard): runs carry `created`/`updated` timestamps."""
    out = []
    for d in runs:
        props = d.get("proposals", [])
        kept = sum(1 for p in props if p.get("status") == "kept")
        out.append({"id": d.get("id"), "kind": d.get("kind"), "status": d.get("status"),
                    "runtime_s": max(0, int((d.get("updated") or 0) - (d.get("created") or 0))), "kept": kept, "proposals": len(props)})
    return out
