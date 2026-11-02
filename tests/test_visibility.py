import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codefactory as cf  # noqa: E402


class ReasonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.ag.repo.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.tmp.cleanup)

    def test_every_block_and_cancel_has_a_reason_and_reopening_clears_it(self):
        a = self.ag.backlog.add("A", "d", force=True)
        self.ag.backlog.set(a, status="cancelled", notes=["cancelled because of IDEAS.md"])
        self.assertEqual(a["reason"], "cancelled because of IDEAS.md")
        self.ag.backlog.set(a, status="pending")
        self.assertNotIn("reason", a)
        self.ag.backlog.set(a, status="blocked")
        self.assertTrue(a["reason"])
        self.ag.block_task(a, "Attempt 3 [build]: boom")
        self.assertIn("boom", a["reason"])

    def test_process_adds_runtime_even_when_the_task_fails(self):
        a = self.ag.backlog.add("A", "d", force=True)

        def slow(item):
            time.sleep(0.05)
            return False
        self.ag._process = slow
        self.ag.process(a)
        self.ag.process(a)
        self.assertGreaterEqual(a["runtime_s"], 0.1)

    def test_split_cancel_names_the_slices(self):
        a = self.ag.backlog.add("Big", "d", force=True)
        self.ag.llm.json = lambda *x, **k: {"cause": "too big", "subtasks": [{"title": "S1", "description": "d"}, {"title": "S2", "description": "d"}]}
        self.ag.split_task(a, ["x"])
        self.assertEqual(a["status"], "cancelled")
        self.assertIn("smaller slices", a["reason"])


if __name__ == "__main__":
    unittest.main()
