"""Model choice for the self-improve feature (standard library only).

Stored in `self_improve/models.json`. An empty field means "automatic": the tool picks the best installed model for the
job. Jobs: planner (plans, requirement split), reviewer, summarizer, coder (standard steps), light coder (docs, small
edits), heavy coder (hard steps, late repair rounds) and the final reviewer. `routing.mode` "off" pins every job to the
models chosen here.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.cfutil import read_json, write_json
from infra import models

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,99}$")
ROLES = ("planner", "reviewer", "summarizer", "coder")
ROUTES = ("light", "heavy", "final_reviewer")


def default() -> dict:
    return {**{r: "" for r in ROLES}, "routing": {"mode": "auto", **{r: "" for r in ROUTES}}}


def file(state: Path) -> Path:
    return state / "models.json"


def _name(v) -> str:
    v = str(v or "").strip()
    if v and not MODEL_RE.match(v):
        raise ValueError(f"Invalid model name: {v!r}")
    return v


def load(state: Path) -> dict:
    """Saved choice merged over the defaults; unreadable or invalid entries fall back to automatic."""
    out, saved = default(), read_json(file(state), {})
    if not isinstance(saved, dict):
        return out
    for r in ROLES:
        try:
            out[r] = _name(saved.get(r))
        except ValueError:
            pass
    rt = saved.get("routing") if isinstance(saved.get("routing"), dict) else {}
    out["routing"]["mode"] = "off" if rt.get("mode") == "off" else "auto"
    for r in ROUTES:
        try:
            out["routing"][r] = _name(rt.get(r))
        except ValueError:
            pass
    return out


def save(state: Path, body: dict) -> dict:
    """Validates and stores the choice (raises ValueError with a readable message)."""
    cur = load(state)
    for r in ROLES:
        if r in body:
            cur[r] = _name(body[r])
    rt = body.get("routing")
    if isinstance(rt, dict):
        if rt.get("mode", "auto") not in ("auto", "off"):
            raise ValueError("Model selection must be auto or off")
        cur["routing"]["mode"] = rt.get("mode", "auto")
        for r in ROUTES:
            if r in rt:
                cur["routing"][r] = _name(rt[r])
    state.mkdir(parents=True, exist_ok=True)
    write_json(file(state), cur)
    return cur


def apply(cfg: dict, state: Path, run_model: str = "") -> dict:
    """Applies the saved choice to a config (in place) and returns it.

    `run_model` pins the coder for a single run (the task form): every coder step uses it and routing is switched off.
    """
    s = load(state)
    for r in ("planner", "reviewer", "summarizer"):
        if s[r]:
            cfg["models"][r] = s[r]
    if s["coder"]:
        cfg["coder_model"] = models.coder_string(s["coder"])
    cfg["routing"].update({"mode": s["routing"]["mode"], **{r: s["routing"][r] for r in ROUTES}})
    if run_model:
        cfg["coder_model"] = models.coder_string(_name(run_model))
        cfg["routing"]["mode"] = "off"
    return cfg
