"""Spike: for uncertain tasks, prove the approach on throwaway work before the real attempt starts."""
from __future__ import annotations

from core.cfutil import clip

RISKS_FOR_SPIKE = 4
KNOWN_FOR_SPIKE = 3


SMALL_CTX = 8192  # below this window a spike sees only a cut-down prompt: it would prove nothing and cost many minutes


class SpikeMixin:
    def small_ctx(self) -> bool:
        ctx = getattr(getattr(self, "llm", None), "server_ctx", 0) or 0
        return 0 < ctx < SMALL_CTX

    def spike_failed_split(self, item: dict, finding: str) -> bool:
        """A spike that really failed means the planned approach is too big or wrong: slice the task now instead of spending an attempt on
        it (spike and split used to run one after the other, each blind). True if the task was split."""
        if not self.feat("spike_split") or finding == "inconclusive" or int(item.get("split_depth", 0)) >= 1:
            return False
        if not self.split_task(item, [f"Pre-check: a spike of the approach failed ({finding}). Cut the task so that the first slice avoids the failing part."]):
            return False
        self._spike_split = True
        return True

    def spike_due(self, item: dict, plan: dict) -> bool:
        """Only the first attempt, and only if the task looks uncertain: it was blocked before, the plan lists many risks,
        or the knowledge base knows many consequences/lessons for the files involved."""
        if not self.feat("spike") or self.cur_attempt != 1 or int(item.get("split_depth", 0)) > 0:
            return False
        if self.small_ctx():
            self.log(f"Spike skipped for {item['id']}: the model server window is only {self.llm.server_ctx} tokens, a spike would be blind")
            return False
        if self.feat("risk_spikes") and item.get("risk") == "high":
            return True  # risk-first: unsure libraries, parsers and performance are tested early
        return (int(item.get("revives", 0)) > 0 or len(plan.get("risks") or []) >= RISKS_FOR_SPIKE
                or len(plan.get("known_consequences") or []) >= KNOWN_FOR_SPIKE)

    def spike(self, item: dict, plan: dict) -> tuple[bool, str]:
        """Builds the smallest proof of the approach in the working tree, runs build and tests, then discards everything.
        Returns (worked, one-line finding) for the journal and the real attempt."""
        self.phase("Spike: testing the approach on throwaway work", "implement", item["title"])
        edit = plan.get("files_to_change", []) + plan.get("new_files", [])
        self.code("spike", edit, [str(self.run_dir / "context.md")] + self.journal_file(),
                  f"SPIKE for task {item['id']}: {item['title']}\n{item['description']}\nSteps: {plan.get('steps')}\n"
                  f"Risks: {plan.get('risks')}\n\nThis is throwaway work to check that the approach holds. Implement only the riskiest "
                  "core of the task in the smallest possible way, plus one tiny test that exercises it. No polish, no docs.\n\n" + self.RULES)
        changed = self.git.changed_files()
        ok, report = self.verify_stage() if changed else (False, "the spike produced no change")
        self.git.hard_reset()
        if not ok and changed and report.startswith(("[build]", "[setup]", "[lint]")):
            # unfixed throwaway code that does not compile says nothing about the approach: do not re-plan because of it
            self.note("spike", "inconclusive: the spike code did not compile")
            self.log(f"Spike {item['id']}: inconclusive (the throwaway code did not compile), continuing with the plan")
            return True, "inconclusive"
        finding = (f"approach holds (proved with {changed[:5]})" if ok else f"approach did not hold: {clip(report.splitlines()[0] if report else '', 220)}")
        self.note("spike", finding)
        self.metric(kind="spike", item=item["id"], ok=bool(ok))
        self.log(f"Spike {item['id']}: {finding}")
        return bool(ok), finding
