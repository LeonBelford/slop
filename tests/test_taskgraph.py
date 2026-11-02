import tempfile
import unittest
from pathlib import Path

from core import blueprint, taskgraph
from core.cfa_graph import GraphMixin
from core.cfgit import Backlog


def it(i, status="pending", deps=(), **kw):
    return {"id": i, "title": f"task {i}", "status": status, "depends_on": list(deps), "type": "feature", "priority": 5.0, "acceptance": [], **kw}


ITEMS = [it("T1", "done"), it("T2", "pending", ["T1"]), it("T3", "pending", ["T1", "T2"]), it("T4", "done"), it("T5", "done", ["T4"]),
         it("T6"), it("T7", "cancelled", ["T6"])]


class GraphFunctionTests(unittest.TestCase):
    def test_components_and_visibility(self):
        self.assertEqual(taskgraph.components(ITEMS), [["T1", "T2", "T3"], ["T4", "T5"], ["T6"]])
        self.assertEqual(taskgraph.active_graphs(ITEMS), [["T1", "T2", "T3"], ["T6"]])
        # a finished task of an active graph stays visible, a finished graph only on request
        self.assertEqual(taskgraph.visible_ids(ITEMS), {"T1", "T2", "T3", "T6"})
        self.assertEqual(taskgraph.visible_ids(ITEMS, show_done=True), {"T1", "T2", "T3", "T4", "T5", "T6"})

    def test_cycles_are_broken_and_levels_follow_dependencies(self):
        deps = taskgraph.break_cycles({"A": ["B"], "B": ["C"], "C": ["A"]})
        self.assertEqual(sum(len(v) for v in deps.values()), 2)
        self.assertEqual(taskgraph.levels(ITEMS), {"T1": 0, "T2": 1, "T3": 2, "T4": 0, "T5": 1, "T6": 0})

    def test_trees_hang_a_task_under_its_first_dependency(self):
        t = taskgraph.trees(ITEMS, {"T1", "T2", "T3"})
        self.assertEqual([r["id"] for r in t], ["T1"])
        t2 = t[0]["children"][0]
        self.assertEqual(t2["id"], "T2")
        self.assertEqual([c["id"] for c in t[0]["children"]], ["T2", "T3"])
        self.assertEqual(t[0]["children"][1]["also"], ["T2"])

    def test_unlocks_render_and_layers(self):
        self.assertEqual(taskgraph.unlocks(ITEMS)["T1"], 2)
        text = taskgraph.render(ITEMS)
        self.assertIn("T3 [pending] task T3 <- T1, T2", text)
        self.assertNotIn("T4", text)
        self.assertIn("1 finished graph", text)
        items = [it("A", layer=2), it("B", deps=["A"], layer=0)]
        taskgraph.raise_layers(items)
        self.assertEqual(items[1]["layer"], 2)
        self.assertNotEqual(taskgraph.signature(ITEMS), taskgraph.signature(ITEMS[:-1]))


class Fake(GraphMixin):
    def __init__(self, path):
        self.backlog = Backlog(path)


class MergeSplitTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.a = Fake(Path(self.t.name) / "b.json")
        self.a.backlog.items[:] = [it("T1", "done"), it("T2", deps=["T1"], layer=0, priority=4), it("T3", deps=["T1"], layer=1, priority=7),
                                   it("T4", deps=["T2", "T3"], layer=1), it("T5", deps=["T4"])]
        self.by = {i["id"]: i for i in self.a.backlog.items}

    def tearDown(self):
        self.t.cleanup()

    def test_merge_replaces_members_and_rewires_dependents(self):
        new = self.a.merge_tasks({"ids": ["T2", "T3"], "title": "Both", "description": "", "acceptance": [], "why": "same code"}, self.by)
        self.assertEqual(new["depends_on"], ["T1"])
        self.assertEqual((new["layer"], new["priority"]), (1, 7.0))
        self.assertEqual(self.by["T4"]["depends_on"], [new["id"]])
        self.assertEqual((self.by["T2"]["status"], self.by["T2"]["merged_into"]), ("cancelled", new["id"]))

    def test_split_chains_steps_and_the_dependents_wait_for_the_last(self):
        steps = [{"title": f"step {n}", "description": "", "acceptance": []} for n in range(3)]
        ids = self.a.split_into_steps(self.by["T2"], steps, self.by)
        self.assertEqual(self.by[ids[0]]["depends_on"], ["T1"])
        self.assertEqual(self.by[ids[2]]["depends_on"], [ids[1]])
        self.assertEqual(self.by["T4"]["depends_on"], [ids[2], "T3"])
        self.assertEqual(self.by["T2"]["status"], "cancelled")

    def test_normalizer_keeps_only_valid_merges_and_splits(self):
        items = [it("T1", "in_progress"), it("T2"), it("T3"), it("T4", source="user"), it("T5")]
        data = {"layers": [{"name": "A", "goal": "g"}], "merge": [{"ids": ["T2", "T3", "T1", "T4", "X"], "title": "m"}, {"ids": ["T5"], "title": "one"}],
                "split": [{"id": "T2", "steps": [{"title": "a"}, {"title": "b"}]}, {"id": "T5", "steps": [{"title": "a"}, {"title": "b"}]},
                          {"id": "T1", "steps": [{"title": "a"}, {"title": "b"}]}]}
        r = blueprint.normalize_reevaluation(data, items)
        self.assertEqual([m["ids"] for m in r["merge"]], [["T2", "T3"]])
        self.assertEqual([s["id"] for s in r["split"]], ["T5"])


class ReevaluateGraphTests(unittest.TestCase):
    def test_reevaluate_applies_merge_split_and_keeps_the_graph_acyclic(self):
        import codefactory as cf
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t) / "p"
            (ws / "agent").mkdir(parents=True)
            ag = cf.Agent(ws)
            ag.repo.mkdir(parents=True, exist_ok=True)
            ag.git.commit_all = lambda *a, **k: None
            for n in range(6):
                ag.backlog.add(f"Task {n}", "d", priority=5, force=True, layer=0, depends_on=["T0001"] if n else None)
            seen = {}

            def fake(tag, role, system, user, **kw):
                seen["user"] = user
                return {"assessment": "x", "layers": [{"name": "A", "goal": "g"}, {"name": "B", "goal": "g"}],
                        "assign": [{"id": "T0001", "layer": 0, "depends_on": []}, {"id": "T0002", "layer": 1, "depends_on": ["T0003"]},
                                   {"id": "T0003", "layer": 1, "depends_on": ["T0002"]}, {"id": "T0006", "layer": 0, "depends_on": ["T0004"]}],
                        "merge": [{"ids": ["T0004", "T0005"], "title": "Merged", "why": "one change"}],
                        "split": [{"id": "T0003", "steps": [{"title": "s1"}, {"title": "s2"}]}]}
            ag.llm.json = fake
            ag.llm.record = lambda *a, **k: None
            stats = ag.reevaluate(requeue=False)
            self.assertEqual((stats["merged"], stats["split"]), (2, 1))
            self.assertIn("Dependency graphs now", seen["user"])
            items = ag.backlog.items
            by = {i["id"]: i for i in items}
            self.assertEqual(by["T0004"]["status"], "cancelled")
            merged = next(i for i in items if i["title"] == "Merged")
            self.assertEqual(by["T0006"]["depends_on"], [merged["id"]])
            deps = taskgraph.deps_of(items)
            self.assertEqual(deps, taskgraph.break_cycles(deps))
            self.assertEqual(ag.state["graph_sig"], taskgraph.signature(items))


if __name__ == "__main__":
    unittest.main()


class QualityGraphTest(unittest.TestCase):
    def test_why_normalized_and_cancel_needs_reason(self):
        items = [{"id": "T1", "title": "a", "type": "feature", "status": "pending", "source": "blueprint"},
                 {"id": "T2", "title": "b", "type": "feature", "status": "pending", "source": "blueprint"}]
        r = blueprint.normalize_reevaluation({"layers": [{"name": "x", "goal": "g"}], "cancel": [{"id": "T1"}, {"id": "T2", "why": "duplicate of T1"}],
                                              "keep_plan": True}, items)
        self.assertEqual(r["cancel"], ["T2"])
        self.assertTrue(r["keep"])

    def test_quality_graph_is_a_chain_with_reasons(self):
        from core.cfa_hygiene_graph import HygieneGraphMixin

        class A(HygieneGraphMixin):
            pass
        with tempfile.TemporaryDirectory() as d:
            a = A()
            a.backlog = Backlog(Path(d) / "b.json")
            a.lim = {"refactor_every": 4}
            a.schedule_quality(["x.py"], 4)
            items = a.backlog.items
            self.assertEqual(len(items), 2)
            self.assertEqual(items[1]["depends_on"], [items[0]["id"]])
            self.assertTrue(all(i["why"] for i in items))
            a.schedule_quality(["x.py"], 8)  # one is still open
            self.assertEqual(len(a.backlog.items), 2)
