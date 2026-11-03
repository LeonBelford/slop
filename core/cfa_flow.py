"""Agent methods for the optional quality steps of a task: tests first, step-by-step coding, acceptance check, retry strategy, test protection."""
from __future__ import annotations

import json
import re

from core.cfutil import clip

FEATURE_DEFAULTS = {"test_first": "auto", "step_by_step": "auto", "acceptance_check": True, "retry_replan": True, "protect_tests": True, "spike": True, "spike_split": True, "doc_sync": True, "coder_no_think": True, "triage": True, "essential_check": True, "triage_light": True, "skeleton": True, "dependency_plan": True, "contracts_code": True, "spec_first": True, "size_gate": True, "risk_spikes": True, "layer_gate": True, "drift_check": True, "regression_tests": True, "graph_plan": True, "graph_replan": True, "graph_review_always": False, "plan_confirm": False, "perf_guard": True, "scout": True, "tiers": True, "fast_verify": True, "light_plan": True, "dep_replan": True, "graph_split": True, "plan_split": True}
TEST_DEF = re.compile(r"^\s*(?:async\s+def\s+test_|def\s+test_|func\s+Test|(?:pub\s+)?(?:async\s+)?fn\s+test_|#\[(?:\w+::)*test\b[^\]]*\]|@Test\b|(?:it|test|describe)\s*\(|public\s+void\s+test)", re.M)


def removed_tests(diff: str) -> int:
    """Net number of test definitions a diff deletes (removed minus added); > 0 means tests were dropped."""
    gone = added = 0
    for line in diff.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-") and TEST_DEF.match(line[1:]):
            gone += 1
        elif line.startswith("+") and TEST_DEF.match(line[1:]):
            added += 1
    return gone - added


def added_test_names(diff: str, limit: int = 12) -> list[str]:
    """The test definitions a diff adds (names a bug fix must bring as its regression test)."""
    return [line[1:].strip() for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++") and TEST_DEF.match(line[1:])][:limit]


def removed_test_names(diff: str, limit: int = 12) -> list[str]:
    """The test definition lines a diff deletes without adding the same line back (for the message to the coder)."""
    gone, added = [], set()
    for line in diff.splitlines():
        if line.startswith(("---", "+++")) or not TEST_DEF.match(line[1:]):
            continue
        text = line[1:].strip()
        if line.startswith("+"):
            added.add(text)
        elif line.startswith("-") and not text.startswith("#["):
            gone.append(text)
    return [t for t in gone if t not in added][:limit]


def split_steps(steps: list, parts: int = 3) -> list[list]:
    """Splits the plan steps into at most `parts` consecutive groups of similar size."""
    if len(steps) < 2:
        return [steps]
    parts = min(parts, len(steps))
    size, extra = divmod(len(steps), parts)
    out, i = [], 0
    for n in range(parts):
        j = i + size + (1 if n < extra else 0)
        out.append(steps[i:j])
        i = j
    return out


class FlowMixin:
    """Agent mixin: the optional quality steps around a task (tests-first, step-by-step coding, acceptance check,
    retry strategy, protecting tests from being weakened)."""

    def feat(self, name: str):
        return self.cfg["features"].get(name, FEATURE_DEFAULTS[name])

    def wants(self, name: str, item: dict, plan: dict) -> bool:
        """off | on | auto switches: auto enables the step only where it pays off for a small model."""
        mode = self.feat(name)
        if mode in (False, "off"):
            return False
        if mode in (True, "on"):
            return True
        rt = self.cfg["routing"]
        big = len(plan.get("files_to_change", []) + plan.get("new_files", [])) >= int(rt["complex_files"]) or len(plan.get("steps", [])) >= 4
        if name == "test_first":
            # a separate coder call for the tests is worth it for a bug (the regression test proves the fix) or a big change; a small
            # feature gets code and tests from one call (the task prompt already asks for tests)
            return item["type"] in ("feature", "bug") and bool(self.test_paths(plan)) and (item["type"] == "bug" or big)
        return item["type"] in ("feature", "bug", "refactor") and big

    def test_paths(self, plan: dict) -> list[str]:
        out = []
        for f in plan.get("test_files", []) or []:
            f = str(f).strip().removeprefix("./")
            if f and ".." not in f and not f.startswith("/") and re.search(r"test|spec", f, re.I) and f not in out:
                out.append(f)
        return out[:3]

    def test_first(self, item: dict, plan: dict) -> list[str]:
        """Writes the tests for the task before any code exists; the coder then has a concrete target. Returns the test files."""
        if not self.wants("test_first", item, plan):
            return []
        self.phase("Writing the tests first", "implement", ", ".join(self.test_paths(plan)))
        self.set_class(item, plan)
        self.code("tests_first", self.test_paths(plan), [str(self.run_dir / "context.md")],
                  f"Task: {item['title']}\n{item['description']}\nAcceptance: {item.get('acceptance')}\n"
                  f"Tests to write: {plan.get('tests_to_add')}\n\nWrite ONLY the tests for this task in the given test files, so they fail now "
                  f"and pass once the feature exists. Do not implement the feature and do not change other files.\n\n{self.RULES}")
        found = [f for f in self.git.changed_files() if re.search(r"test|spec", f, re.I) and (self.repo / f).exists()]
        self.log(f"Tests first: {len(found)} test file(s) written before the code")
        return found

    def step_groups(self, item: dict, plan: dict) -> list[list]:
        steps = [str(s) for s in plan.get("steps", [])]
        if self.wants("step_by_step", item, plan) and len(steps) >= 2:
            groups = split_steps(steps, 2 if len(steps) < 6 else 3)  # every pass is a full coder call: two passes unless the plan is long
            if len(groups) > 1:
                self.log(f"Step by step: implementing in {len(groups)} passes")
            return groups
        return [steps]

    def acceptance_check(self, item: dict, plan: dict) -> bool:
        """The reviewer ticks off every acceptance criterion against the diff; unmet ones get one fix round. False = still red afterwards."""
        crit = [str(c) for c in (item.get("acceptance") or []) if str(c).strip()]
        if not self.feat("acceptance_check") or not crit:
            return True
        self.phase("Checking the acceptance criteria against the change", "review", f"{len(crit)} criteria")
        try:
            res = self.llm.json("acceptance", "reviewer", "You check, criterion by criterion, whether a diff fulfils the acceptance criteria. "
                                "Mark a criterion met only if the diff or a test proves it.",
                                f"Task: {item['title']}\nCriteria: {json.dumps(crit)}\nDiff:\n{clip(self.git.diff(), 12000)}\n"
                                'Schema: {"results": [{"criterion": "", "met": true, "evidence": "file or test", "fix": "what is missing"}]}')
        except Exception as e:  # noqa: BLE001
            self.log(f"Acceptance check failed: {e}")
            return True
        open_ = [r for r in res.get("results", []) if isinstance(r, dict) and r.get("met") is False]
        self.metric(kind="acceptance", item=item["id"], criteria=len(crit), unmet=len(open_))
        if not open_:
            return True
        self.log(f"Acceptance check: {len(open_)} of {len(crit)} criteria not met, one fix round")
        self.handoff("acceptance fix", "acceptance check", goal=item["title"], do=[f"make this true: {c}" for c in open_],
                     do_not=["remove existing behaviour or tests"], done_when=["every listed criterion is demonstrably met by code and a test"])
        self.code("acceptance_fix", sorted(set(self.git.changed_files()))[:self.lim["max_files_per_task"] + 4], [str(self.run_dir / "context.md")],
                  f"These acceptance criteria are not met yet:\n{json.dumps(open_, indent=1)}\n\n{self.RULES}")
        ok, _ = self.debug_loop(item)
        return ok

    def retry_strategy(self, item: dict, previous: list[str]) -> None:
        """Before a retry: one short analysis of why the last attempt failed and what to do differently, added to the failure notes."""
        if not previous or not self.feat("retry_replan"):
            return
        try:
            d = self.llm.json("retry_strategy", "reviewer", "You analyse a failed coding attempt and name a different approach.",
                              f"Task: {item['title']}\n{item['description']}\nFailed attempts:\n" + "\n".join(previous) +
                              '\nSchema: {"cause": "root cause in one sentence", "change": "what to do differently this time, concretely"}')
            previous.append(f"Strategy for the next attempt: cause: {d.get('cause', '')}; do differently: {d.get('change', '')}")
            self.handoff("planning of the next attempt", "failure analysis", goal=item["title"],
                         decided=[f"root cause of the last failure: {d.get('cause', '')}"],
                         do=[str(d.get("change", "")), "then still deliver the WHOLE task, not only this fix"],
                         do_not=["repeat the approach of the failed attempt", "reduce the task to repairing the earlier error"],
                         done_when=["all acceptance criteria of the task are met"])
            self.log(f"Retry strategy: {clip(str(d.get('change', '')), 160)}")
            if d.get("cause"):
                self.mem.add_lesson(clip(f"{d['cause']} Do instead: {d.get('change', '')}", 400), [], item["id"])
        except Exception as e:  # noqa: BLE001
            self.log(f"Retry strategy failed: {e}")
            self.mem.add_lesson(clip(previous[0], 300), [], item["id"])
