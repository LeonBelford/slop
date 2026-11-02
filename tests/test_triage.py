import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codefactory as cf  # noqa: E402
from core import cfa_triage  # noqa: E402


def item(i, status="pending", **kw):
    return {"id": i, "status": status, "type": "feature", "priority": 5, **kw}


class ScoreTests(unittest.TestCase):
    def test_fanout_is_transitive_and_ignores_closed_tasks(self):
        items = [item("a"), item("b", depends_on=["a"]), item("c", depends_on=["b"]), item("d", "done", depends_on=["a"])]
        self.assertEqual(cfa_triage.fanout(items), {"a": 2, "b": 1, "c": 0, "d": 0})

    def test_unblocking_task_rises_and_failing_task_sinks(self):
        items = [item("a"), item("b", depends_on=["a"]), item("c", attempts=2), item("u", source="user")]
        s = cfa_triage.score(items)
        self.assertGreater(s["a"], 0)
        self.assertLess(s["c"], 0)
        self.assertNotIn("u", s)  # user tasks are never adjusted

    def test_penalty_and_bonus_are_capped(self):
        items = [item("a", attempts=50)] + [item(f"x{n}", depends_on=["a"]) for n in range(20)]
        self.assertLessEqual(cfa_triage.score(items)["a"], cfa_triage.MAX_FANOUT_BONUS)
        self.assertGreaterEqual(cfa_triage.score([item("z", attempts=50)])["z"], -cfa_triage.MAX_PENALTY)


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.ag.repo.mkdir(parents=True, exist_ok=True)
        self.calls = []
        self.ag.reevaluate = lambda **kw: self.calls.append(kw) or {}
        self.ag.replan_if_stuck = lambda: False
        self.ag.light_check = lambda: False

    def tearDown(self):
        self.tmp.cleanup()

    def test_dyn_changes_the_pick(self):
        a = self.ag.backlog.add("Base", "d", priority=5, force=True, layer=0)
        b = self.ag.backlog.add("Other", "d", priority=5, force=True, layer=0)
        self.ag.backlog.add("Needs base", "d", priority=5, force=True, layer=1).update(depends_on=[a["id"]])
        self.ag.backlog.set(b, priority=5.5)
        self.assertEqual(self.ag.backlog.next()["id"], b["id"])
        self.ag.triage()
        self.assertEqual(self.ag.backlog.next()["id"], a["id"], "the task others wait for comes first")

    def test_relayout_only_after_enough_merges_and_not_every_iteration(self):
        for n in range(3):
            self.ag.backlog.add(f"T{n}", "d", force=True, layer=0)
        self.ag.state["merged"] = 1
        self.ag.triage()
        self.assertEqual(self.calls, [])
        self.ag.state["merged"] = 3
        self.ag.triage()
        self.ag.triage()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.calls[0]["requeue"])
        self.assertIn("3 task(s) merged", self.calls[0]["state_note"])

    def test_failed_relayout_is_not_retried_and_does_not_raise(self):
        for n in range(3):
            self.ag.backlog.add(f"T{n}", "d", force=True, layer=0)
        self.ag.state["merged"] = 3

        def boom(**kw):
            self.calls.append(kw)
            raise RuntimeError("no layers")
        self.ag.reevaluate = boom
        self.ag.triage()
        self.ag.triage()
        self.assertEqual(len(self.calls), 1)

    def test_feature_flag_off_only_runs_stuck_replan(self):
        self.ag.cfg["features"]["triage"] = False
        ran = []
        self.ag.replan_if_stuck = lambda: ran.append(1) or False
        self.ag.state["merged"] = 9
        self.ag.triage()
        self.assertEqual((ran, self.calls), ([1], []))


if __name__ == "__main__":
    unittest.main()
