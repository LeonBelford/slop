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

    def test_big_plan_grows_the_tree_without_a_model_call(self):
        a = Stub(self.p, SUBS)
        a.cfg, a.lim = {"features": {}}, {"max_files_per_task": 6}
        a.feat = lambda n: True
        t = a.backlog.add("Argument parsing", "d", layer=0, force=True)
        dep = a.backlog.add("Use args", "d", layer=0, depends_on=[t["id"]], force=True)
        plan = {"steps": [f"step {n} in app/main.py" for n in range(7)], "files_to_change": ["app/main.py"], "new_files": []}
        self.assertTrue(a.plan_split(t, plan))
        slices = [i for i in a.backlog.items if i.get("parent") == t["id"]]
        self.assertEqual(len(slices), 2)
        self.assertEqual((slices[0]["files"], slices[1]["depends_on"], dep["depends_on"]), (["app/main.py"], [slices[0]["id"]], [slices[1]["id"]]))
        self.assertTrue(slices[0]["hot"] and "step 0" in slices[0]["description"] and "step 6" in slices[1]["description"])
        self.assertEqual(t["split_into"], [i["id"] for i in slices])
        small = a.backlog.add("Small", "d", force=True)
        self.assertFalse(a.plan_split(small, {"steps": ["a", "b", "c"], "files_to_change": ["x.py"]}))
        slices[0]["split_depth"] = 2
        self.assertFalse(a.plan_split(slices[0], plan))

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


class ReevaluationRobustnessTests(unittest.TestCase):
    def test_plain_string_layers_and_odd_split_entries_do_not_crash(self):
        from core import blueprint
        data = {"layers": ["Core", {"name": "UI", "goal": "g"}], "split": ["T0001"], "merge": ["x"], "assign": [], "keep_plan": True}
        self.assertEqual([ly["name"] for ly in blueprint.keep_closed(data["layers"], [{"name": "Core"}], {0})], ["Core", "UI"])
        res = blueprint.normalize_reevaluation(data, [{"id": "T0001", "status": "pending"}])
        self.assertEqual([ly["name"] for ly in res["layers"]], ["Core", "UI"])
        self.assertEqual(res["split"], [])

    def test_no_edit_retry_reuses_the_plan_and_skips_the_model(self):
        from core.cfa_flow import FlowMixin
        a = FlowMixin.__new__(FlowMixin)
        tmp = Path(tempfile.mkdtemp())
        a.run_dir, a.logs = tmp, []
        a.cfg = {"features": {}}
        a.log = a.logs.append
        a.handoff = lambda *x, **k: None
        a.llm = None
        (tmp / "plan.json").write_text(json.dumps({"files_to_change": ["a.py"], "steps": ["s"]}))
        item = {"id": "T1", "title": "t", "description": "d"}
        prev = ["Attempt 1 [no_edit]: Exception: the coder made no usable edit"]
        self.assertEqual(a.reuse_plan(item, prev)["steps"], ["s"])
        a.retry_strategy(item, prev)
        self.assertIn("wrote no file", prev[-1])
        self.assertIsNone(a.reuse_plan(item, ["Attempt 1 [test_fail]: red"]))
        self.assertIsNone(a.reuse_plan(item, []))
