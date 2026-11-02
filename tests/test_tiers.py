import tempfile
import unittest
from pathlib import Path

import codefactory as cf
from core import graphctx
from core.cfgit import Backlog


class GraphStructureTests(unittest.TestCase):
    def test_group_files_follows_modules_and_needs_enough_files(self):
        g = graphctx.group_files(["core/a.py", "core/b.py", "web/x.js", "web/y.js", "tests/test_a.py"])
        self.assertEqual(len(g), 2)
        self.assertIn("tests/test_a.py", g[0])  # the test follows its module
        self.assertEqual(graphctx.group_files(["a.py", "b.py"]), [])
        self.assertEqual(graphctx.group_files(["core/a.py", "core/b.py", "core/c.py", "core/d.py"]), [])  # one block: not worth splitting

    def test_cancelled_dependency_without_heir_counts_but_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            b = Backlog(Path(d) / "b.json")
            gone = b.add("Gone", "d", force=True)
            heir = b.add("Merged target", "d", force=True)
            merged = b.add("Merged away", "d", force=True)
            nxt = b.add("Next", "d", force=True, depends_on=[gone["id"], merged["id"]])
            b.set(gone, status="cancelled")
            b.set(merged, status="cancelled", merged_into=heir["id"])
            self.assertEqual(b.dead_deps(nxt), [gone["id"]])
            self.assertFalse(b.satisfied(merged["id"]), "a merged task waits for the task it was merged into")
            self.assertNotIn(nxt["id"], [i["id"] for i in b.ready([nxt])])
            b.set(heir, status="done")
            self.assertEqual([i["id"] for i in b.ready([nxt])], [nxt["id"]])

    def test_split_slice_is_picked_next(self):
        with tempfile.TemporaryDirectory() as d:
            b = Backlog(Path(d) / "b.json")
            other = b.add("Other", "d", priority=9, force=True, layer=0)
            slice_ = b.add("Slice", "d", priority=1, force=True, layer=0)
            b.set(slice_, hot=True)
            self.assertEqual(b.next()["id"], slice_["id"])
            self.assertNotEqual(other["id"], slice_["id"])


class TierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.ag.repo.mkdir(parents=True, exist_ok=True)
        self.ag.llm.record = lambda *a, **k: None
        self.ag.log = lambda *a, **k: None
        self.files = ["app.py"]
        self.diff = "+++ b/app.py\n+x = 1\n"
        self.ag.git.changed_files = lambda base="main": self.files
        self.ag.git.diff = lambda base="main": self.diff

    def tearDown(self):
        self.tmp.cleanup()

    def test_tier_from_the_real_diff(self):
        item = {"id": "T1", "title": "t", "attempts": 1}
        self.assertEqual(self.ag.tier_of(item, "x"), "tiny")
        self.files = ["a.py", "tests/test_a.py", "b.py"]
        self.assertEqual(self.ag.tier_of(item, "x"), "small")
        self.files = ["a.py", "b.py", "c.py"]
        self.assertEqual(self.ag.tier_of(item, "x"), "full")
        self.assertEqual(self.ag.tier_of(dict(item, risk="high"), "x"), "full")
        self.assertEqual(self.ag.tier_of(dict(item, attempts=2), "x"), "full")

    def test_only_the_full_tier_runs_review_and_mutation(self):
        ran = []
        ag = self.ag
        ag.coverage_check = lambda i: ran.append("coverage")
        ag.mutation_check = lambda i: ran.append("mutation") or True
        ag.e2e_check = lambda i: ran.append("e2e") or (True, "")
        ag.security_gate = lambda i, p: ran.append("security") or (True, "")
        ag.review_gate = lambda i, p: ran.append("review") or (True, "", {"score": 9})
        ag.acceptance_check = lambda i, p: ran.append("acceptance") or True
        ag.backlog.set = lambda item, **kw: item.update(kw)
        item = {"id": "T1", "title": "t", "attempts": 1}
        ok, _, review = ag.run_gates(item, {}, "x")
        self.assertTrue(ok)
        self.assertEqual(ran, ["e2e", "security"])
        self.assertEqual(review["verdict"], "approve")
        ran.clear()
        self.files = ["a.py", "b.py"]
        ag.run_gates(item, {}, "x")
        self.assertEqual(ran, ["e2e", "security", "review"])
        ran.clear()
        self.files = ["a.py", "b.py", "c.py"]
        ag.run_gates(item, {}, "x")
        self.assertEqual(ran, ["coverage", "mutation", "e2e", "security", "review", "acceptance"])
        ag.cfg["tiers"] = {"full": ["review"]}  # a project can reshape a tier
        ran.clear()
        ag.run_gates(item, {}, "x")
        self.assertEqual(ran, ["review"])

    def test_light_plan_only_for_small_tasks_with_known_files(self):
        ag = self.ag
        (ag.repo / "app.py").write_text("x = 1\n")
        ag.mem.context_pack = lambda item, cands: "ctx"
        ag.run_dir = ag.dir / "runs" / "t"
        ag.run_dir.mkdir(parents=True)
        ag.phase = lambda *a, **k: None
        item = {"id": "T1", "title": "t", "description": "d", "type": "feature", "status": "pending", "files": ["app.py"], "acceptance": ["works"]}
        ag.backlog.items.append(item)
        plan = ag.light_plan(item, [])
        self.assertTrue(plan and plan["light"])
        self.assertEqual(plan["files_to_change"], ["app.py"])
        self.assertIsNone(ag.light_plan(item, ["failed"]))
        self.assertIsNone(ag.light_plan(dict(item, files=["a.py", "b.py", "c.py"]), []))
        self.assertIsNone(ag.light_plan(dict(item, risk="high"), []))
        self.assertIsNone(ag.light_plan(dict(item, files_guess=True), []))

    def test_graph_split_cuts_along_file_groups_and_continues_with_slice_one(self):
        ag = self.ag
        item = ag.backlog.add("Big", "do it", priority=5, force=True, layer=1, acceptance=["all works"])
        item["files"] = ["core/a.py", "core/b.py", "web/x.js", "web/y.js", "tests/test_a.py"]
        after = ag.backlog.add("After", "d", force=True, layer=2, depends_on=[item["id"]])
        self.assertTrue(ag.graph_split(item, "too big"))
        self.assertEqual(item["status"], "cancelled")
        first, second = (ag.backlog.by_id(i) for i in item["split_into"])
        self.assertTrue(first["hot"] and not second.get("hot"))
        self.assertEqual(second["depends_on"], [first["id"]])
        self.assertEqual(after["depends_on"], [second["id"]])
        self.assertEqual(second["acceptance"], ["all works"])
        self.assertEqual(ag.backlog.next()["id"], first["id"])

    def test_docs_only_change_skips_the_verify_run(self):
        ag = self.ag
        ag.ok_tree = "green"
        names = ["README.md"]
        ag.git.out = lambda *a, **k: "\n".join(names)
        self.assertTrue(ag.unchanged_since_green("other"))
        names[:] = ["requirements.txt"]
        self.assertFalse(ag.unchanged_since_green("other"))
        names[:] = ["README.md", "app.py"]
        self.assertFalse(ag.unchanged_since_green("other"))
        self.assertTrue(ag.unchanged_since_green("green"))

    def test_due_process_grows_out_of_the_last_merged_task(self):
        ag = self.ag
        done = ag.backlog.add("Built", "d", force=True, layer=1)
        ag.backlog.set(done, status="done", files=["core/a.py"])
        ag.state["last_done"] = done["id"]
        made = ag.run_process({"id": "p1", "name": "Bug hunt", "steps": "Read for bugs\nFix them", "sequential": True, "type": "refactor", "every": 5})
        self.assertEqual(made[0]["depends_on"], [done["id"]])
        self.assertEqual(made[0]["layer"], 1)
        self.assertEqual(made[1]["depends_on"], [made[0]["id"]])
        self.assertEqual(made[0]["files"], ["core/a.py"])

    def test_process_is_spliced_between_last_task_and_its_successor(self):
        ag = self.ag
        done = ag.backlog.add("Built", "d", force=True, layer=1)
        ag.backlog.set(done, status="done")
        nxt = ag.backlog.add("Next feature", "d", force=True, layer=1, depends_on=[done["id"]])
        ag.state["last_done"] = done["id"]
        made = ag.run_process({"id": "p1", "name": "Docs", "steps": "Read\nUpdate", "sequential": True, "type": "refactor", "every": 3})
        self.assertIn(made[-1]["id"], ag.backlog.by_id(nxt["id"])["depends_on"])
        self.assertFalse(ag.backlog.ready([ag.backlog.by_id(nxt["id"])]))
        self.assertEqual(ag.backlog.next()["id"], made[0]["id"])


if __name__ == "__main__":
    unittest.main()
