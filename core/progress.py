"""Shared step pipelines and progress-percentage math for the "where is the agent right now" UI.

Both the main agent (core/cfa_build.py etc.) and the self-improve tool (selfimprove.py) publish a
`step` name with every phase change. This module turns that name into a rough, monotonically
increasing percentage so the dashboard can show an estimated "x% of this task" instead of just a
label, plus a shared helper to keep a short, per-task history of the individual sub-steps taken.
"""
from __future__ import annotations

import time

# Order and relative weight of the stages a normal task goes through; must match the UI's STEPS list.
AGENT_PIPELINE = [("plan", 8), ("implement", 25), ("build", 20), ("e2e", 10), ("security", 10),
                   ("review", 12), ("cleanup", 7), ("docs", 5), ("merge", 3)]
# Same idea for a self-improve run on the tool itself; must match the UI's TOOL_STEPS list.
TOOL_PIPELINE = [("plan", 10), ("backup", 5), ("impact", 10), ("implement", 30), ("security", 15),
                  ("test", 20), ("review", 7), ("docs", 3)]


def step_pct(pipeline: list[tuple[str, int]], step: str | None, enter_credit: float = 0.35) -> int | None:
    """Percentage reached once `step` starts: every earlier step's weight plus partial credit for this one."""
    if not step:
        return None
    total = sum(w for _, w in pipeline)
    done = 0
    for name, w in pipeline:
        if name == step:
            return round(100 * (done + w * enter_credit) / total)
        done += w
    return None


class Tracker:
    """Keeps progress monotonic (never shown going backwards), a short log of recent sub-steps, and a
    per-step time-spent total (`durations`) for one task. `durations` only counts *closed* time spent in a
    step (i.e. before it moved on); the UI adds the live elapsed time for the currently open step itself
    using `since`, so totals stay correct even if a step repeats (e.g. several debug rounds of "build")."""

    def __init__(self) -> None:
        self.key = None
        self.max_pct = 0
        self.steps: list[dict] = []
        self.durations: dict[str, float] = {}
        self.last_step: str | None = None
        self.last_time: float | None = None

    def update(self, pipeline: list[tuple[str, int]], key, name: str, step: str | None, detail: str = "",
               done: bool = False) -> tuple[int | None, list[dict], dict[str, float]]:
        now = time.time()
        if key != self.key:
            self.key, self.max_pct, self.steps = key, 0, []
            self.durations, self.last_step, self.last_time = {}, None, None
        if self.last_step is not None and self.last_time is not None:
            self.durations[self.last_step] = self.durations.get(self.last_step, 0.0) + (now - self.last_time)
        self.last_step, self.last_time = step, now
        pct = 100 if done else step_pct(pipeline, step)
        if pct is not None:
            self.max_pct = max(self.max_pct, pct)
            pct = self.max_pct
        self.steps.append({"phase": name, "step": step, "detail": detail[:200] if detail else "", "time": now})
        self.steps = self.steps[-20:]
        return pct, self.steps, dict(self.durations)
