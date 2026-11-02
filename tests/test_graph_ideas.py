import tempfile
import unittest
from pathlib import Path

import codefactory as cf
from core import graph_ideas
from core.cfutil import read_json, write_json

IDEA = {"title": "Export to CSV", "why": "users ask for data", "steps": [
    {"title": "CSV writer", "description": "d", "type": "feature", "needs": []},
    {"title": "Export endpoint", "type": "bogus", "needs": [0, 7], "why": "exposes it"}]}


class IdeaNormalizeTests(unittest.TestCase):
    def test_ideas_need_two_steps_and_no_repeats(self):
        out = graph_ideas.norm_ideas([IDEA, {"title": "Short", "steps": [{"title": "only"}]}, "junk", IDEA], set(), [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["steps"][1]["needs"], [0])  # an unknown index is dropped
        self.assertEqual(out[0]["steps"][1]["type"], "feature")
        self.assertEqual(graph_ideas.norm_ideas([IDEA], {"export to csv"}, []), [])
        self.assertEqual(graph_ideas.norm_ideas([IDEA], set(), ["Export to CSV"]), [])

    def test_text_steps_and_process(self):
        steps = graph_ideas.steps_from_text("- Find dupes: in core\n2. Fix them\n\n", True)
        self.assertEqual([s["title"] for s in steps], ["Find dupes", "Fix them"])
        self.assertEqual((steps[0]["description"], steps[1]["needs"]), ("in core", [0]))
        self.assertEqual(graph_ideas.steps_from_text("a\nb", False)[1]["needs"], [])
        p = graph_ideas.norm_process({"name": " Cleanup ", "steps": "a\nb", "every": 5}, "P1")
        self.assertEqual((p["name"], p["every"], p["type"]), ("Cleanup", 5, "refactor"))
        with self.assertRaises(ValueError):
            graph_ideas.norm_process({"name": "", "steps": "a"}, "P2")


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.ag.repo.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_ideas_keeps_new_ones_only(self):
        ag = self.ag
        self.assertEqual(ag.store_ideas([IDEA], ag.backlog.items), 1)
        self.assertEqual(ag.store_ideas([IDEA], ag.backlog.items), 0)
        self.assertEqual(read_json(ag.ideas_file, {})["ideas"][0]["title"], "Export to CSV")

    def test_process_runs_as_a_chain_and_only_when_due(self):
        ag = self.ag
        proc = graph_ideas.norm_process({"name": "Docs", "steps": "Check docs\nUpdate docs", "every": 3}, "P1")
        write_json(ag.processes_file, [proc])
        made = ag.run_process(proc)
        self.assertEqual(len(made), 2)
        self.assertEqual(made[1]["depends_on"], [made[0]["id"]])
        self.assertEqual(made[0]["source"], "process")
        ag.run_due_processes(2)
        self.assertEqual(len(ag.backlog.items), 2)  # not due yet
        ag.run_due_processes(3)
        self.assertEqual(len(ag.backlog.items), 2)  # due, but the earlier run is still open
        for i in ag.backlog.items:
            i["status"] = "done"
        ag.state["process_at"]["P1"] = 0
        ag.run_due_processes(6)
        self.assertEqual(len(ag.backlog.items), 4)

    def test_custom_graph_is_built_out_by_the_next_review(self):
        ag = self.ag
        made = ag.add_custom_graph("Search", "Full text search over all notes", "Index notes\nSearch endpoint")
        self.assertEqual((len(made), made[0]["source"], made[1]["depends_on"]), (2, "user", [made[0]["id"]]))
        single = ag.add_custom_graph("Export", "Export as CSV", "")
        self.assertEqual((len(single), single[0]["source"]), (1, "custom"))
        ag._graph_started = True
        self.assertIn("custom graph(s) to build out: Search, Export", ag.review_reasons()[0][0])
        note = ag.custom_note()
        self.assertIn("Full text search", note)
        self.assertEqual(ag.custom_pending(), [])
        self.assertEqual(ag.custom_note(), "")
        with self.assertRaises(ValueError):
            ag.add_custom_graph("", "", "")


if __name__ == "__main__":
    unittest.main()
