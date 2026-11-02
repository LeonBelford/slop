"""Model routing and the failure notebook of self-improve."""
from __future__ import annotations

import os
from pathlib import Path

import codefactory as sa
from dash import runledger
from infra import models


def _route(llm) -> dict:
    """Which installed model does which job: light coder for docs, standard for normal steps, heavy for hard steps and the last
    repair round, strongest reviewer for review/verification/diagnosis. Missing models fall back to the defaults, nothing is required."""
    installed = getattr(llm, "installed", None) or set()
    if isinstance(llm, sa.LLM) and not sa.LLM.mock and not installed:
        installed = llm.installed = models.installed_from_ollama(os.getenv("OLLAMA_HOST_URL", "http://localhost:11434")) or set()
    cfg = getattr(llm, "cfg", None) or sa.DEFAULT_CONFIG  # the saved model choice (self_improve/models.json) lives in the LLM's config
    r = models.resolve(installed, cfg["routing"], cfg["coder_model"])
    big = os.getenv("CODER_MODEL_BIG")
    if big and not r["heavy"]:
        r["heavy"] = big
    if r["final_reviewer"] and isinstance(llm, sa.LLM):
        llm.overrides["reviewer"] = r["final_reviewer"]  # review, plan critique and requirement checks use the strongest reviewer
    return r


def _plain(name: str) -> str:
    return str(name or "default").split("/", 1)[-1] if str(name or "").startswith("ollama") else str(name or "default")


def _learned_cls(coder, cls: str, rnd: int, state: Path) -> tuple[str, str]:
    """Adjusts the job class by what the track record says (needs a few attempts per model before it speaks up):
    a default coder that keeps failing gets replaced by the heavy one, a heavy model that is not better is not worth the model swap."""
    routes = getattr(coder, "routes", None) or {}
    heavy, default = routes.get("heavy"), _plain(getattr(coder, "model", ""))
    if not heavy or rnd >= 2:
        return cls, ""
    s = runledger.model_stats(state)
    d, h = s.get(default, {"tries": 0, "ok": 0}), s.get(_plain(heavy), {"tries": 0, "ok": 0})
    rd, rh = d["ok"] / d["tries"] if d["tries"] else 1, h["ok"] / h["tries"] if h["tries"] else 0
    if cls == "standard" and d["tries"] >= 3 and h["tries"] >= 2 and rd < 0.4 and rh - rd >= 0.25:
        return "heavy", f"{default} succeeded {d['ok']}/{d['tries']}, {heavy} {h['ok']}/{h['tries']}"
    if cls == "heavy" and h["tries"] >= 4 and rh <= rd and rh < 0.5:
        return "standard", f"{heavy} is not better ({h['ok']}/{h['tries']} vs {d['ok']}/{d['tries']}), saving the model swap"
    return cls, ""


def _coder_for(coder, cls: str):
    """The coder function for a job class (light | standard | heavy); the default coder when no better model is installed."""
    routes, use = getattr(coder, "routes", None), getattr(coder, "use", None)
    name = (routes or {}).get({"light": "light", "heavy": "heavy"}.get(cls, ""))
    return use(name) if name and use else coder


def _lessons(state: Path) -> str:
    f = state / "lessons.md"
    return f.read_text("utf-8", "replace")[-1500:] if f.is_file() else ""


def _remember(state: Path, title: str, stage: str, report: str, diag: str, fixed: bool) -> None:
    """Failure notebook that later prompts read: what went wrong, and what (if anything) fixed it."""
    try:
        state.mkdir(exist_ok=True)
        with open(state / "lessons.md", "a", encoding="utf-8") as f:
            f.write(f"- [{'fixed' if fixed else 'failed'}] {title}: stage {stage}: {sa.clip(report, 200)!r} {sa.clip(diag, 250)!r}\n")
    except OSError:
        pass
