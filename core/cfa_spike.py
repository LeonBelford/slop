"""Spike: for uncertain tasks, prove the approach on throwaway work before the real attempt starts."""
from __future__ import annotations

from core.cfutil import clip

RISKS_FOR_SPIKE = 4
KNOWN_FOR_SPIKE = 3


class SpikeMixin:
    def spike_due(self, item: dict, plan: dict) -> bool:
        """Only the first attempt, and only if the task looks uncertain: it was blocked before, the plan lists many risks,
        or the knowledge base knows many consequences/lessons for the files involved."""
        if not self.feat("spike") or self.cur_attempt != 1:
            return False
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
        finding = (f"approach holds (proved with {changed[:5]})" if ok else f"approach did not hold: {clip(report.splitlines()[0] if report else '', 220)}")
        self.note("spike", finding)
        self.metric(kind="spike", item=item["id"], ok=bool(ok))
        self.log(f"Spike {item['id']}: {finding}")
        return bool(ok), finding
