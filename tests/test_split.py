import json
import tempfile
import unittest
from pathlib import Path

from core.cfa_split import SplitMixin
from core.cfgit import Backlog


class Stub(SplitMixin):
    goal = "explorer app"

    def __init__(self, path, reply):
        self.backlog = Backlog(path)
        self.state = {"merged": 0}
        self.reply = reply
        self.logs = []
        self.mem = type("M", (), {"render_codemap": staticmethod(lambda: "")})()
        self.llm = type("L", (), {"json": lambda s, *a, **k: reply})()

    def log(self, m):
        self.logs.append(m)

    def phase(self, *a, **k):
        pass

    def metric(self, **k):
        pass


SUBS = {"cause": "too big", "subtasks": [{"title": "Path helpers", "description": "d", "acceptance": ["a"]},
                                           {"title": "List directory", "description": "d"}]}


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = Path(self.d.name) / "b.json"

    def tearDown(self):
        self.d.cleanup()

    def test_failed_task_becomes_ordered_slices_and_dependents_follow(self):
        a = Stub(self.p, SUBS)
        t = a.backlog.add("Filesystem access", "d", layer=0, force=True)
        dep = a.backlog.add("Explorer view", "d", layer=1, depends_on=[t["id"]], force=True)
        self.assertTrue(a.split_task(t, ["attempt failed"]))
        self.assertEqual(t["status"], "cancelled")
        s1, s2 = (i for i in a.backlog.items if i.get("split_depth") == 1)
        self.assertEqual((s1["layer"], s2["depends_on"], dep["depends_on"]), (0, [s1["id"]], [s2["id"]]))
        self.assertEqual(a.backlog.next()["id"], s1["id"])

    def test_depth_limit_and_bad_answer_keep_the_task(self):
        a = Stub(self.p, SUBS)
        t = a.backlog.add("x task", "d", force=True)
        t["split_depth"] = 2
        self.assertFalse(a.split_task(t, []))
        t["split_depth"] = 0
        a.reply = {"subtasks": [{"title": "only one"}]}
        a.llm = type("L", (), {"json": lambda s, *x, **k: a.reply})()
        self.assertFalse(a.split_task(t, []))
        self.assertEqual(t["status"], "pending")

    def test_blocked_task_revives_after_merges_at_most_twice(self):
        a = Stub(self.p, SUBS)
        t = a.backlog.add("Core", "d", force=True)
        a.block_task(t)
        self.assertEqual(a.revive_blocked(), 0)
        for expect in (1, 2):
            a.state["merged"] += 2
            self.assertEqual(a.revive_blocked(), 1)
            self.assertEqual((t["status"], t["attempts"], t["revives"]), ("pending", 0, expect))
            a.block_task(t)
        a.state["merged"] += 5
        self.assertEqual(a.revive_blocked(), 0)


if __name__ == "__main__":
    unittest.main()


class WipTests(unittest.TestCase):
    def test_green_work_survives_a_failed_gate_and_is_resumed(self):
        from core.cfa_build import BuildMixin
        from core.cfgit import Git

        class A(BuildMixin):
            lim = {}

            def __init__(self, repo):
                self.git = Git(repo)
                self.run_dir = repo.parent
                self.logs = []

            def log(self, m):
                self.logs.append(m)

        with tempfile.TemporaryDirectory() as t:
            repo = Path(t) / "repo"
            a = A(repo)
            a.git.ensure()
            (repo / "a.py").write_text("x = 1\n")
            a.git.commit_all("base")
            item = {"id": "T0001"}
            a.git.branch_from_main("agent/T0001-1")
            (repo / "b.py").write_text("y = 2\n")
            a.save_wip(item)
            a.git.hard_reset()
            a.git.run("checkout", "-q", "main")
            a.git.run("branch", "-D", "agent/T0001-1")
            self.assertFalse((repo / "b.py").exists())
            a.git.branch_from_main("agent/T0001-2")
            self.assertIsNone(a.resume_wip(item))  # no plan saved yet: start fresh
            (Path(t) / "plan.json").write_text('{"files_to_change": ["a.py"]}')
            self.assertEqual(a.resume_wip(item)["files_to_change"], ["a.py"])
            self.assertTrue((repo / "b.py").exists())
            a.drop_wip(item)
            self.assertNotEqual(a.git.run("rev-parse", "--verify", "-q", "agent/wip-T0001")[0], 0)
