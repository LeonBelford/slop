import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import selfimprove  # noqa: E402
from dash import sievidence  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_rules_name_metric_and_value(self):
        hits = sievidence.suggestions({"flow": {"first_try_rate": 0.2}, "models": {"reasoning_share": 0.8, "avg_call_s": 10}, "quality": {"stalled": 3}})
        self.assertEqual({h["metric"] for h in hits}, {"reasoning_share", "first_try_rate", "stalled"})
        self.assertIn("80%", [h for h in hits if h["metric"] == "reasoning_share"][0]["text"])

    def test_healthy_project_gives_no_suggestion(self):
        self.assertEqual(sievidence.suggestions({"flow": {"first_try_rate": 0.9}, "models": {"reasoning_share": 0.2}, "quality": {"fail_rate": 0.1}}), [])
        self.assertEqual(sievidence.suggestions({}), [])

    def test_retry_plan_escalates_only_on_identical_failures(self):
        self.assertFalse(sievidence.retry_plan(["a"], "build", True)["escalate"])
        self.assertFalse(sievidence.retry_plan(["a", "b"], "build", True)["escalate"])
        r = sievidence.retry_plan(["a", "a"], "implement", True)
        self.assertTrue(r["escalate"])
        self.assertIn("edit blocks", r["hint"])
        self.assertFalse(sievidence.retry_plan(["a", "a"], "build", False)["escalate"])
        self.assertIn("Three identical", sievidence.retry_plan(["a", "a", "a"], "build", False)["hint"])

    def test_run_times(self):
        r = sievidence.run_times([{"id": "x", "created": 100, "updated": 160, "status": "done", "proposals": [{"status": "kept"}, {"status": "reverted"}]}])
        self.assertEqual((r[0]["runtime_s"], r[0]["kept"], r[0]["proposals"]), (60, 1, 2))


class EvidenceTests(unittest.TestCase):
    def test_gather_evidence_carries_journals_planning_and_suggestions(self):
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t) / "p"
            a = ws / "agent"
            (a / "runs" / "0001-T0001").mkdir(parents=True)
            (a / "runs" / "0001-T0001" / "journal.md").write_text("tried X\n", "utf-8")
            (a / "GOAL.md").write_text("goal", "utf-8")
            (a / "backlog.json").write_text(json.dumps([{"id": "T0001", "status": "blocked", "reason": "glob"}]), "utf-8")
            (a / "state.json").write_text(json.dumps({"triage_log": [{"task": "T2"}], "layer_gates": {"0": {"ok": False}}}), "utf-8")
            (a / "metrics.jsonl").write_text(json.dumps({"kind": "attempt_failed", "why": "boom"}) + "\n", "utf-8")
            p = selfimprove.gather_evidence(Path(t))["projects"][0]
        self.assertEqual(p["journals"][0]["reason"], "glob")
        self.assertIn("tried X", p["journals"][0]["journal"])
        self.assertEqual(p["planning"]["triage"], [{"task": "T2"}])
        self.assertIn("0", p["planning"]["gate_failures"])
        self.assertIsInstance(p["suggestions"], list)


if __name__ == "__main__":
    unittest.main()
