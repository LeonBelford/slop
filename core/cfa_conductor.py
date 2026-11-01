"""The conductor: shapes the pipeline for each task from the project's recent track record and keeps one task journal
that every prompt of a task sees (instead of every stage starting blind)."""
from __future__ import annotations

from contextlib import contextmanager

from core.cfutil import clip, write_text

WINDOW = 8
STUCK_RATE = 0.5
JOURNAL_CHARS = 1800
REPLAN_EVERY_MERGES = 5


class ConductorMixin:
    # ---- Task journal: shared working memory of one task
    def journal_start(self, item: dict) -> None:
        self.journal: list[str] = []
        self.note("task", f"{item['id']} {item['title']} | acceptance: {clip(str(item.get('acceptance')), 300)}")

    def note(self, kind: str, text: str) -> None:
        """Adds one line to the task journal; the newest part is shown to the model in every later prompt of this task."""
        if not hasattr(self, "journal"):
            self.journal = []
        self.journal.append(f"- [{kind}] {' '.join(str(text).split())[:400]}")
        body = "\n".join(self.journal)
        self.llm.session_note = body[:300] + "\n...\n" + body[-(JOURNAL_CHARS - 300):] if len(body) > JOURNAL_CHARS else body
        rd = getattr(self, "run_dir", None)
        if rd:
            write_text(rd / "journal.md", "# Task journal\n" + body + "\n")

    def journal_file(self) -> list[str]:
        p = getattr(self, "run_dir", None)
        return [str(p / "journal.md")] if p and (p / "journal.md").exists() else []

    # ---- Pipeline profile from recent outcomes
    def fail_rate(self) -> float:
        recent = self.state.get("outcomes", [])[-WINDOW:]
        return recent.count(False) / len(recent) if recent else 0.0

    def choose_profile(self, item: dict) -> tuple[str, str, dict, dict]:
        """(mode, reason, feature overrides, limit overrides). Gates that protect correctness (build, tests, security, review,
        acceptance) are never switched off; only optional extras are traded for speed, or effort is added where it is stuck."""
        rate, outcomes = self.fail_rate(), self.state.get("outcomes", [])
        lim = self.lim
        if len(outcomes) >= 3 and rate >= STUCK_RATE:
            return ("careful", f"{round(rate * 100)}% of the last {min(len(outcomes), WINDOW)} tasks failed",
                    {"cleanup_pass": False, "plan_critique": True, "mutation_check": False},
                    {"max_debug_rounds": min(lim["max_debug_rounds"] + 1, 6)})
        if item["type"] in ("docs", "test"):
            return "fast", "docs/test task", {"cleanup_pass": False, "plan_critique": False, "mutation_check": False}, {}
        if len(outcomes) >= 5 and not any(not o for o in outcomes[-5:]):
            return "flow", "five clean tasks in a row", {"plan_critique": False}, {}
        return "standard", "", {}, {}

    @contextmanager
    def conducting(self, item: dict):
        """Applies the chosen profile to one task and always restores the configuration afterwards; records the outcome."""
        mode, why, feats, lims = self.choose_profile(item)
        old_f, old_l = dict(self.cfg["features"]), dict(self.lim)
        self.cfg["features"].update(feats)
        self.lim.update(lims)
        if mode != "standard":
            self.log(f"Conductor: {mode} mode for {item['id']} ({why})")
        self.metric(kind="conductor", item=item["id"], mode=mode)
        self.force_heavy = mode == "careful"
        try:
            yield mode
        finally:
            self.cfg["features"].clear()
            self.cfg["features"].update(old_f)
            self.lim.clear()
            self.lim.update(old_l)
            self.llm.session_note = ""
            self.force_heavy = False

    def record_outcome(self, item: dict, ok: bool) -> bool:
        """Remembers the result; a task that was split into slices counts as progress, not as a failure."""
        good = ok or item.get("status") == "cancelled"
        self.state.setdefault("outcomes", []).append(bool(good))
        self.state["outcomes"] = self.state["outcomes"][-WINDOW * 2:]
        return good

    def replan_if_stuck(self) -> bool:
        """When several tasks are blocked or failing, re-think the plan (strategist) instead of grinding on; at most every few merges."""
        blocked = sum(1 for i in self.backlog.items if i["status"] == "blocked")
        stuck = blocked >= 3 or self.state.get("consecutive_failures", 0) >= 3
        if not stuck or self.state["merged"] - self.state.get("auto_replan_at", -REPLAN_EVERY_MERGES) < REPLAN_EVERY_MERGES:
            return False
        self.state["auto_replan_at"] = self.state["merged"]
        self.log("Conductor: the project is stuck, re-evaluating the plan")
        try:
            self.reevaluate()
        except Exception as e:  # noqa: BLE001
            self.log(f"Conductor: re-evaluation failed ({e}), continuing with the current plan")
            return False
        return True
