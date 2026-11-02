import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codefactory as cf  # noqa: E402
from core import cfa_triage  # noqa: E402


class LightCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.addCleanup(self.tmp.cleanup)
        self.a = self.ag.backlog.add("A", "d", priority=3, force=True)
        self.b = self.ag.backlog.add("B", "d", priority=5, force=True)
        self.u = self.ag.backlog.add("U", "d", priority=5, force=True, source="user")
        self.ag.state["merged"] = 1

    def test_changes_are_applied_logged_and_run_once_per_merge(self):
        calls = []

        def fake(*a, **k):
            calls.append(1)
            return {"changes": [{"id": self.a["id"], "priority": 9, "reason": "unblocks B"}, {"id": self.u["id"], "priority": 1, "reason": "x"},
                                {"id": "T9999", "priority": 2}, {"id": self.b["id"], "priority": "bad"}]}
        self.ag.llm.json = fake
        self.assertTrue(self.ag.light_check())
        self.assertEqual(self.a["priority"], 9)
        self.assertEqual(self.u["priority"], 5)
        self.assertEqual(self.ag.state["triage_log"][0]["reason"], "unblocks B")
        self.assertFalse(self.ag.light_check())
        self.assertEqual(len(calls), 1)

    def test_error_and_flag_off_are_harmless(self):
        def boom(*a, **k):
            raise RuntimeError("x")
        self.ag.llm.json = boom
        self.assertFalse(self.ag.light_check())
        self.ag.state["merged"] = 2
        self.ag.cfg["features"]["triage_light"] = False
        self.assertFalse(self.ag.light_check())

    def test_risk_essential_and_size_sources(self):
        items = [{"id": "r", "status": "pending", "priority": 5, "risk": "high"},
                 {"id": "e", "status": "pending", "priority": 5, "essential_note": "core"},
                 {"id": "s", "status": "pending", "priority": 5, "estimate_s": 300},
                 {"id": "b", "status": "pending", "priority": 5, "estimate_s": 7200}]
        sc = cfa_triage.score(items)
        self.assertEqual((sc["r"], sc["e"], sc["s"], sc["b"]), (1.5, 1.0, 0.5, -0.5))


if __name__ == "__main__":
    unittest.main()
