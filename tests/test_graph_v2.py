import tempfile
import unittest
from pathlib import Path

import codefactory as cf
from core import graph_templates, taskgraph
from core.cfa_scout import scout_task
from core.cfgit import Backlog


def item(i, status="pending", deps=None, **kw):
    return {"id": i, "title": f"task {i}", "type": "feature", "status": status, "priority": 5, "depends_on": deps or [], "layer": 0, **kw}


class GraphMathTests(unittest.TestCase):
    def test_summaries_critical_path_and_calibration(self):
        items = [item("A", "done", estimate_s=100, runtime_s=300), item("B", "done", estimate_s=100, runtime_s=300),
                 item("C", deps=["B"], estimate_s=100), item("D", deps=["C"], estimate_s=100), item("E", deps=["B"], estimate_s=10)]
        self.assertEqual(taskgraph.duration_ratio(items), {"feature": 3.0})
        g = [s for s in taskgraph.summaries(items) if "C" in s["ids"]][0]
        self.assertEqual(g["critical"], ["C", "D"])
        self.assertEqual(g["remaining_s"], 630)  # (100+100+10) * 3.0
        self.assertEqual((g["done"], g["total"], g["goal"]), (1, 4, "task D"))
        self.assertIn("Critical paths", taskgraph.render(items))

    def test_blocked_impact_and_problems(self):
        items = [item("A", "blocked"), item("B", deps=["A"]), item("C", deps=["B"]), item("D", layer=None)]
        self.assertEqual(taskgraph.blocked_impact(items), {"A": ["B", "C"]})
        found = taskgraph.problems(items)
        self.assertTrue(any("holds back 2" in f for f in found) and any("D has no layer" in f for f in found))
        self.assertEqual(taskgraph.problems([item("A"), item("B", deps=["A"])]), [])
        self.assertIn("dependency cycle", taskgraph.problems([item("A", deps=["B"]), item("B", deps=["A"])]))

    def test_damage_detects_mass_cancel_and_fragmentation(self):
        before = [item(str(n), deps=["0"] if n else []) for n in range(6)]
        after = [dict(i, status="cancelled") if i["id"] in "1234" else dict(i) for i in before]
        self.assertIn("cancelled", taskgraph.damage(before, after))
        merged = [dict(i, status="cancelled", merged_into="9") if i["id"] in "1234" else dict(i) for i in before]
        self.assertEqual(taskgraph.damage(before, merged), "")
        torn = [dict(i, depends_on=[]) for i in before]
        self.assertIn("fell apart", taskgraph.damage(before, torn))
        self.assertEqual(taskgraph.snapshot(before, "r", {"new": 1}, "t")["nodes"][1]["d"], ["0"])


class ChainAndTemplateTests(unittest.TestCase):
    def test_next_prefers_the_chain_of_the_last_finished_task(self):
        with tempfile.TemporaryDirectory() as d:
            b = Backlog(Path(d) / "b.json")
            done = b.add("Base", "d", priority=5, force=True, layer=0)
            other = b.add("Other graph", "d", priority=5.5, force=True, layer=0)
            follow = b.add("Follow up", "d", priority=5, force=True, layer=0, depends_on=[done["id"]])
            b.set(done, status="done")
            self.assertEqual(b.next()["id"], other["id"])
            self.assertEqual(b.next(chain_from=done["id"])["id"], follow["id"])

    def test_template_makes_a_connected_graph_with_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            b = Backlog(Path(d) / "b.json")
            made = graph_templates.instantiate(b, "api-endpoint", "Orders")
            self.assertEqual(len(made), 4)
            self.assertEqual(made[2]["depends_on"], [made[1]["id"]])
            self.assertTrue(all(i["why"] for i in made))
            self.assertEqual(len(taskgraph.components(b.items)), 1)
            with self.assertRaises(ValueError):
                graph_templates.instantiate(b, "nope", "x")

    def test_scout_finds_files_and_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "orders.py").write_text("def create_order():\n    pass\nclass Order:\n    pass\n")
            hint = scout_task(repo, {"orders.py": {"purpose": "orders storage"}}, {"title": "Add orders endpoint", "description": ""})
            self.assertIn("orders.py", hint)
            self.assertIn("create_order", hint)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.ag.repo.mkdir(parents=True, exist_ok=True)
        self.ag.git.commit_all = lambda *a, **k: None
        self.ag.llm.record = lambda *a, **k: None
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_review_for_all_reasons_and_skip_when_unchanged(self):
        ag = self.ag
        ag.reevaluate = lambda **kw: self.calls.append(kw) or {}
        for n in range(3):
            ag.backlog.add(f"Task {n}", "d", priority=5, force=True, layer=0)
        self.assertTrue(ag.graph_review())
        self.assertEqual(len(self.calls), 1)
        self.assertIn("start of the run", self.calls[0]["reason"])
        ag._graph_started = False
        ag.state["graph_sig"] = taskgraph.signature(ag.backlog.items)
        self.assertFalse(ag.graph_review())  # same structure, no flaw: no model call
        ag.cfg["features"]["graph_review_always"] = True
        ag._graph_started = False
        self.assertTrue(ag.graph_review())

    def test_changed_goal_or_ideas_trigger_a_review(self):
        ag = self.ag
        ag._graph_started = True
        ag.backlog.add("Task", "d", priority=5, force=True, layer=0)
        ag.state["directives_hash"] = ag.directives_hash()
        self.assertEqual(ag.review_reasons()[0], [])
        (ag.dir / "GOAL.md").write_text("new wish")
        self.assertIn("GOAL.md or IDEAS.md changed", ag.review_reasons()[0][0])

    def test_blocked_chain_gets_a_focused_mini_replan_once(self):
        ag = self.ag
        ag.reevaluate = lambda **kw: self.calls.append(kw) or {}
        ag._graph_started = True
        a = ag.backlog.add("Base", "d", priority=5, force=True, layer=0)
        b = ag.backlog.add("Mid", "d", priority=5, force=True, layer=0, depends_on=[a["id"]])
        ag.backlog.add("Top", "d", priority=5, force=True, layer=0, depends_on=[b["id"]])
        ag.backlog.set(a, status="blocked", reason="x")
        self.assertTrue(ag.graph_review())
        self.assertEqual(sorted(self.calls[0]["focus"]), sorted(taskgraph.components(ag.backlog.items)[0]))
        self.assertFalse(ag.graph_review())  # the same blocked task is not reviewed again

    def _fake(self, data):
        self.ag.llm.json = lambda *a, **k: data

    def test_harmful_result_is_rolled_back_and_history_recorded(self):
        ag = self.ag
        for n in range(6):
            ag.backlog.add(f"Task {n}", "d", priority=5, force=True, layer=0, depends_on=["T0001"] if n else None, source="blueprint")
        self._fake({"layers": [{"name": "A", "goal": "g"}], "cancel": [{"id": f"T000{n}", "why": "meh"} for n in range(2, 7)]})
        out = ag.reevaluate(requeue=False)
        self.assertIn("rolled_back", out)
        self.assertEqual([i["status"] for i in ag.backlog.items], ["pending"] * 6)
        self.assertIn("rolled back", ag.history_path.read_text("utf-8"))

    def test_proposal_is_only_applied_after_confirmation(self):
        ag = self.ag
        for n in range(3):
            ag.backlog.add(f"Task {n}", "d", priority=5, force=True, layer=0, source="blueprint")
        self._fake({"layers": [{"name": "A", "goal": "g"}], "cancel": [{"id": "T0003", "why": "duplicate"}]})
        out = ag.reevaluate(requeue=False, preview=True)
        self.assertEqual(out, {"proposal": 1})
        self.assertEqual(ag.backlog.items[2]["status"], "pending")
        stats = ag.apply_proposal()
        self.assertEqual(stats["cancelled"], 1)
        self.assertEqual(ag.backlog.items[2]["status"], "cancelled")
        self.assertFalse(ag.proposal_path.exists())


if __name__ == "__main__":
    unittest.main()
