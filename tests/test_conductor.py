import tempfile
import unittest
from pathlib import Path

from core.cfa_conductor import ConductorMixin


class Agent(ConductorMixin):
    def __init__(self, outcomes=()):
        self.cfg = {"features": {"cleanup_pass": True, "plan_critique": True, "mutation_check": False}}
        self.lim = {"max_debug_rounds": 3}
        self.state = {"outcomes": list(outcomes), "merged": 10, "consecutive_failures": 0}
        self.backlog = type("B", (), {"items": []})()
        self.llm = type("L", (), {"session_note": ""})()
        self.logs, self.replans = [], 0

    def log(self, m):
        self.logs.append(m)

    def metric(self, **k):
        pass

    def reevaluate(self):
        self.replans += 1


class ConductorTests(unittest.TestCase):
    def test_stuck_project_gets_careful_mode_and_settings_are_restored(self):
        a = Agent([True, False, False, False])
        with a.conducting({"id": "T1", "type": "feature"}) as mode:
            self.assertEqual(mode, "careful")
            self.assertEqual((a.lim["max_debug_rounds"], a.cfg["features"]["cleanup_pass"], a.force_heavy), (4, False, True))
        self.assertEqual((a.lim["max_debug_rounds"], a.cfg["features"]["cleanup_pass"], a.force_heavy), (3, True, False))

    def test_fast_lane_for_docs_and_flow_after_clean_streak_never_drop_gates(self):
        a = Agent()
        self.assertEqual(a.choose_profile({"type": "docs"})[0], "fast")
        self.assertEqual(Agent([True] * 5).choose_profile({"type": "feature"})[0], "flow")
        for ag, t in ((a, "docs"), (Agent([True] * 5), "feature"), (Agent([False] * 4), "feature")):
            overrides = ag.choose_profile({"type": t})[2]
            self.assertFalse({"require_tests", "security", "acceptance_check", "protect_tests"} & set(overrides))

    def test_split_counts_as_progress(self):
        a = Agent()
        self.assertTrue(a.record_outcome({"status": "cancelled"}, False))
        self.assertFalse(a.record_outcome({"status": "blocked"}, False))
        self.assertEqual(a.state["outcomes"], [True, False])

    def test_replan_only_when_stuck_and_not_too_often(self):
        a = Agent()
        self.assertFalse(a.replan_if_stuck())
        a.state["consecutive_failures"] = 3
        self.assertTrue(a.replan_if_stuck())
        self.assertFalse(a.replan_if_stuck())
        self.assertEqual(a.replans, 1)

    def test_journal_is_shown_to_every_later_prompt_and_bounded(self):
        a = Agent()
        with tempfile.TemporaryDirectory() as t:
            a.run_dir = Path(t)
            a.journal_start({"id": "T1", "title": "Explorer", "acceptance": ["lists files"]})
            for n in range(60):
                a.note("debug", f"round {n} failed with a rather long explanation " * 3)
            self.assertLessEqual(len(a.llm.session_note), 1900)
            self.assertIn("Explorer", a.llm.session_note)
            self.assertTrue((Path(t) / "journal.md").exists())
            self.assertEqual(a.journal_file(), [str(Path(t) / "journal.md")])


if __name__ == "__main__":
    unittest.main()
