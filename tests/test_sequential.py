import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codefactory as cf  # noqa: E402
from core import blueprint  # noqa: E402

DATA = {"layers": [{"name": "A", "goal": "g", "tasks": [{"key": "a", "title": "Foundation"}, {"key": "b", "title": "Input"}]},
                   {"name": "B", "goal": "g", "tasks": [{"key": "c", "title": "Logic"}]}]}


class SequentialTests(unittest.TestCase):
    def test_blueprint_is_one_chain(self):
        bp = blueprint.normalize(DATA)
        t = [x for ly in bp["layers"] for x in ly["tasks"]]
        self.assertEqual([x["depends_on"] for x in t], [[], ["a"], ["b"]])

    def test_chain_can_be_disabled(self):
        t = [x for ly in blueprint.normalize(DATA, chain=False)["layers"] for x in ly["tasks"]]
        self.assertEqual([x["depends_on"] for x in t], [[], [], []])

    def test_next_waits_and_flags_deadlock(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "p"
            (ws / "agent").mkdir(parents=True)
            ag = cf.Agent(ws)
            a = ag.backlog.add("A", "d", priority=1, force=True)
            b = ag.backlog.add("B", "d", priority=9, force=True, depends_on=[a["id"]])
            self.assertEqual(ag.backlog.next()["id"], a["id"])
            self.assertFalse(ag.backlog.deadlock)
            ag.backlog.set(a, status="blocked")
            self.assertEqual(ag.backlog.next()["id"], b["id"])
            self.assertTrue(ag.backlog.deadlock)
            ag.backlog.set(a, status="done")
            self.assertIn("A", ag.blueprint_ctx(b))


if __name__ == "__main__":
    unittest.main()
