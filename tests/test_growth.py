import json
import tempfile
import unittest
from pathlib import Path

from core.cfa_milestone import MilestoneMixin
from core.cfa_rules import RulesMixin, fail_rate
from core.cfa_spike import SpikeMixin
from core.cfgit import Backlog


class Base:
    def __init__(self, d):
        self.dir = Path(d)
        self.logs = []
        self.state = {}

    def log(self, m):
        self.logs.append(m)

    def metric(self, **k):
        pass

    def note(self, *a):
        pass

    def phase(self, *a, **k):
        pass


class SpikeAgent(Base, SpikeMixin):
    cur_attempt = 1
    RULES = ""

    def feat(self, n):
        return True

    def __init__(self, d, ok=True):
        super().__init__(d)
        self.run_dir = self.dir
        self.ok, self.reset = ok, 0
        self.git = type("G", (), {"changed_files": lambda s: ["a.py"], "hard_reset": lambda s: setattr(self, "reset", self.reset + 1)})()

    def code(self, *a):
        pass

    def journal_file(self):
        return []

    def verify_stage(self):
        return self.ok, "boom\nmore"


class SpikeTests(unittest.TestCase):
    def test_only_uncertain_first_attempts_spike_and_work_is_discarded(self):
        with tempfile.TemporaryDirectory() as t:
            a = SpikeAgent(t)
            self.assertFalse(a.spike_due({}, {"risks": ["x"]}))
            self.assertTrue(a.spike_due({}, {"risks": list("abcd")}))
            self.assertTrue(a.spike_due({"revives": 1}, {}))
            a.cur_attempt = 2
            self.assertFalse(a.spike_due({"revives": 1}, {}))
            ok, finding = a.spike({"id": "T1", "title": "t", "description": "d"}, {"files_to_change": ["a.py"]})
            self.assertTrue(ok)
            self.assertEqual(a.reset, 1)
            a.ok = False
            ok, finding = a.spike({"id": "T1", "title": "t", "description": "d"}, {})
            self.assertFalse(ok)
            self.assertIn("did not hold: boom", finding)


class MilestoneAgent(Base, MilestoneMixin):
    goal = "vision"

    def __init__(self, d, reply):
        super().__init__(d)
        self.backlog = Backlog(self.dir / "b.json")
        self.mem = type("M", (), {"features": {"F1": {"title": "Core"}}})()
        self.llm = type("L", (), {"json": lambda s, *a, **k: reply})()

    def blueprint(self):
        return {"layers": [{"name": "Core", "goal": "g"}, {"name": "UI", "goal": "g"}]}


class MilestoneTests(unittest.TestCase):
    def test_adds_missing_cancels_obsolete_but_never_user_tasks_and_can_ask_for_replan(self):
        with tempfile.TemporaryDirectory() as t:
            a = MilestoneAgent(t, {"alignment": 3, "missing": [{"title": "File watcher", "description": "d"}],
                                   "obsolete": ["T0001", "T0002"], "summary": "drifted"})
            a.backlog.add("Old idea", "d", force=True)
            a.backlog.add("User wish", "d", source="user", force=True)
            rec = a.milestone_review(0, "Core")
            self.assertEqual((rec["added"], rec["cancelled"]), (["T0003"], ["T0001"]))
            self.assertEqual(a.backlog.items[2]["layer"], 1)
            self.assertEqual(a.backlog.items[1]["status"], "pending")
            self.assertTrue(a.state["milestone_replan"])
            self.assertTrue((Path(t) / "milestones.jsonl").exists())


class RulesAgent(Base, RulesMixin):
    pass


class RulesTests(unittest.TestCase):
    def rows(self, failed, done, time="2030-01-01 00:00:00"):
        return [{"kind": "attempt_failed", "time": time}] * failed + [{"review_score": 8, "time": time}] * done

    def test_fail_rate_window(self):
        self.assertEqual(fail_rate(self.rows(1, 3)), (0.25, 4))
        self.assertEqual(fail_rate(self.rows(1, 3), "2031-01-01 00:00:00"), (0.0, 0))

    def test_worse_playbook_is_rolled_back_better_one_is_kept(self):
        with tempfile.TemporaryDirectory() as t:
            a = RulesAgent(t)
            (a.dir / "playbook.md").write_text("old rules")
            a.save_playbook("new rules", 0.1)
            self.assertEqual(a.judge_playbook(self.rows(1, 1)), "")  # too little data
            self.assertEqual(a.judge_playbook(self.rows(4, 2)), "reverted")
            self.assertEqual((a.dir / "playbook.md").read_text(), "old rules")
            a.save_playbook("newer rules", 0.5)
            self.assertEqual(a.judge_playbook(self.rows(1, 4)), "kept")
            self.assertEqual((a.dir / "playbook.md").read_text(), "newer rules")
            self.assertEqual(json.loads((a.dir / "playbook_eval.json").read_text())["verdict"], "kept")

    def test_master_suggestions_are_collected_not_applied(self):
        with tempfile.TemporaryDirectory() as t:
            a = RulesAgent(t)
            a.master_suggestions(["Always write tests", "", "b", "c", "d"])
            self.assertEqual(len((a.dir / "master_suggestions.md").read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
