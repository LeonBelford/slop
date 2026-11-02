import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codefactory as cf  # noqa: E402


class EssentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.addCleanup(self.tmp.cleanup)

    def ask(self, answer):
        self.ag.llm.json = lambda *a, **k: answer

    def test_not_essential_is_cancelled_with_reason(self):
        a = self.ag.backlog.add("Dark mode", "d", force=True)
        self.ask({"essential": False, "reason": "cosmetic extra"})
        self.assertTrue(self.ag.essential_gate(a, ["x"]))
        self.assertEqual(a["status"], "cancelled")
        self.assertIn("cosmetic extra", a["reason"])

    def test_essential_is_not_cancelled(self):
        a = self.ag.backlog.add("Core", "d", force=True)
        self.ask({"essential": True, "reason": "core"})
        self.assertFalse(self.ag.essential_gate(a, ["x"]))
        self.assertNotEqual(a["status"], "cancelled")

    def test_task_others_depend_on_is_never_dropped(self):
        a = self.ag.backlog.add("Base", "d", force=True)
        self.ag.backlog.add("Next", "d", force=True, depends_on=[a["id"]])
        self.ask({"essential": False, "reason": "meh"})
        self.assertFalse(self.ag.essential_gate(a, ["x"]))

    def test_llm_error_means_essential_and_flag_disables(self):
        a = self.ag.backlog.add("Core", "d", force=True)

        def boom(*x, **k):
            raise RuntimeError("down")
        self.ag.llm.json = boom
        self.assertFalse(self.ag.essential_gate(a, ["x"]))
        b = self.ag.backlog.add("Extra", "d", force=True)
        self.ag.cfg["features"]["essential_check"] = False
        self.ask({"essential": False, "reason": "x"})
        self.assertFalse(self.ag.essential_gate(b, ["x"]))


if __name__ == "__main__":
    unittest.main()
