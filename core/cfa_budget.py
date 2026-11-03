"""Time budget per task: tasks should finish in about 15-25 minutes. The pipeline cuts a task when the evidence says it will not fit,
not before: (1) comparable finished tasks were slow, (2) the plan is bigger than this project's recent pace allows, (3) an attempt used up
its time and is not making progress. Cheap failures are simply retried. Pure helpers first, then a small mixin."""
from __future__ import annotations

import re
import statistics
import time

TARGET_MIN = 20          # a task should be done in about this many minutes
SOFT = 1.25              # an attempt past TARGET*SOFT is cut as soon as it stalls
HARD = 1.75              # ... and unconditionally past TARGET*HARD
PLACEHOLDER = re.compile(r"(^|/)(path/to|your[_-]?(file|path|module)|filename|file[_-]?name|file[_-]?here)(\.|/|$)|<[^>]+>|\.\.\.", re.I)


def real_paths(paths: list) -> list[str]:
    """Drops placeholder paths a model copies from the prompt example (path/to/filename.js, <file>, ...)."""
    return [p for p in (str(x).strip() for x in paths or []) if p and not PLACEHOLDER.search(p)]


def should_cut(elapsed_task_s: float, last_attempt_s: float, target_min: float = TARGET_MIN) -> bool:
    """After a failed attempt: would another one of the same length still fit? A 5 min failure is retried; a 20 min failure is split."""
    return elapsed_task_s + last_attempt_s > target_min * HARD * 60


def attempt_over(elapsed_s: float, stuck: bool, target_min: float = TARGET_MIN) -> bool:
    """Inside an attempt: stop debugging when the time is up and it is not progressing (still progressing: until the hard limit)."""
    return elapsed_s > target_min * 60 * (SOFT if stuck else HARD)


def adaptive_steps(base: int, recent_s: list[float], target_min: float = TARGET_MIN) -> int:
    """Plan size limit that follows the real pace: recent tasks slower than the target allow fewer steps, clearly faster ones allow more."""
    if len(recent_s) < 3:
        return base
    med = statistics.median(recent_s) / 60
    if med > target_min * SOFT:
        return max(2, base - 1)
    if med < target_min * 0.5:
        return min(base + 1, 6)
    return base


class BudgetMixin:
    def task_target(self) -> float:
        return float(self.cfg["features"].get("task_target_minutes", TARGET_MIN))

    def recent_runtimes(self, n: int = 8) -> list[float]:
        done = [float(i.get("runtime_s") or 0) for i in self.backlog.items if i.get("status") == "done" and not i.get("parent_summary")]
        return [s for s in done if s > 0][-n:]

    def plan_step_limit(self, base: int) -> int:
        return adaptive_steps(base, self.recent_runtimes(), self.task_target())

    def attempt_started(self) -> None:
        self._attempt_t0 = time.time()

    def out_of_time(self, stuck: bool) -> bool:
        t0 = getattr(self, "_attempt_t0", None)
        return t0 is not None and self.feat("time_budget") and attempt_over(time.time() - t0, stuck, self.task_target())

    def predicted_too_long(self, item: dict) -> bool:
        """Comparable finished tasks (at least 3) took far longer than the target: cut before the first attempt, no model call needed."""
        return self.feat("time_budget") and int(item.get("estimate_n", 0)) >= 3 \
            and float(item.get("estimate_s") or 0) / 60 > self.task_target() * HARD
