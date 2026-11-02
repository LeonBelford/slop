"""Model recommendation per role from a project's own call statistics (standard library only, no model calls).

Rule of thumb the recommendation follows: the coder should not burn its time on reasoning (it writes edit blocks), while
planner, reviewer and strategist profit from it. The advice only names models that were actually used, so it can be
checked against the numbers it shows.
"""
from __future__ import annotations

from collections import defaultdict

CODING = ("coder",)
THINKING = ("planner", "reviewer", "strategist")
MIN_CALLS = 3


def by_role(thoughts: list[dict]) -> dict[str, dict[str, dict]]:
    """{role: {model: {calls, avg_s, reasoning}}} where reasoning is the share of reasoning in the model's output."""
    acc: dict = defaultdict(lambda: defaultdict(lambda: {"calls": 0, "sec": 0.0, "think": 0, "answer": 0}))
    for r in thoughts:
        a = acc[r.get("role") or "?"][r.get("model") or "?"]
        a["calls"] += 1
        a["sec"] += float(r.get("seconds") or 0)
        a["think"] += len(r.get("thinking") or "")
        a["answer"] += len(r.get("answer") or "")
    return {role: {m: {"calls": a["calls"], "avg_s": round(a["sec"] / a["calls"], 1),
                       "reasoning": round(a["think"] / (a["think"] + a["answer"]), 2) if a["think"] + a["answer"] else 0.0}
                   for m, a in models.items()} for role, models in acc.items()}


def recommend(thoughts: list[dict]) -> list[dict]:
    out = []
    for role, models in by_role(thoughts).items():
        rows = [(m, s) for m, s in models.items() if s["calls"] >= MIN_CALLS]
        if not rows:
            continue
        if role in CODING:
            best = min(rows, key=lambda x: (x[1]["reasoning"], x[1]["avg_s"]))
            worst = max(rows, key=lambda x: x[1]["reasoning"])
            if worst[1]["reasoning"] > 0.3 and best[0] != worst[0]:
                out.append({"role": role, "model": best[0], "text": f"Use {best[0]} for {role}: {round(best[1]['reasoning'] * 100)}% reasoning "
                            f"instead of {round(worst[1]['reasoning'] * 100)}% with {worst[0]} ({best[1]['avg_s']}s per call)."})
            elif worst[1]["reasoning"] > 0.3:
                out.append({"role": role, "model": "", "text": f"{worst[0]} spends {round(worst[1]['reasoning'] * 100)}% of its {role} output on reasoning: "
                            "try a coder model without reasoning."})
        elif role in THINKING:
            best = max(rows, key=lambda x: x[1]["reasoning"])
            if best[1]["reasoning"] < 0.1 and len(rows) > 1:
                continue
            if best[1]["reasoning"] < 0.1:
                out.append({"role": role, "model": "", "text": f"{best[0]} does almost no reasoning as {role}: a reasoning model usually plans and reviews better."})
            else:
                out.append({"role": role, "model": best[0], "text": f"Keep {best[0]} for {role} ({round(best[1]['reasoning'] * 100)}% reasoning, {best[1]['avg_s']}s per call)."})
    return out


REASONING_NAMES = ("deepseek-r1", "qwq", "-r1", "think", "reason")


def coder_hint(coder_model: str) -> list[dict]:
    """The coder is Aider (not logged as an LLM call), so judge it by name: a reasoning model there only slows the edits down."""
    if any(k in (coder_model or "").lower() for k in REASONING_NAMES):
        return [{"role": "coder", "model": "", "text": f"{coder_model} is a reasoning model: a coder without reasoning (e.g. qwen2.5-coder) is faster for edit blocks."}]
    return []
