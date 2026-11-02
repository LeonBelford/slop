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


class TruncatedJsonTests(unittest.TestCase):
    def test_cut_off_answer_is_repaired(self):
        from core.cfutil import parse_json
        t = '```json\n{"keep_plan": false, "assign": [{"id": "T1", "layer": 0}, {"id": "T2", "why": "UI needed"}, { "'
        self.assertEqual(parse_json(t)["assign"][1]["id"], "T2")
        self.assertEqual(parse_json('x {"a": 1} y'), {"a": 1})
        with self.assertRaises(ValueError):
            parse_json("no json {")


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

    def test_whole_project_is_linked_layer_by_layer_and_rebuilt_once(self):
        ag = self.ag
        base = ag.backlog.add("Database schema", "tables for notes", "feature", 5, ["x"], "w", "blueprint", force=True, layer=0)
        other = ag.backlog.add("Logging setup", "logging", "feature", 5, ["x"], "w", "blueprint", force=True, layer=0)
        api = ag.backlog.add("Notes API", "api on the database schema", "feature", 5, ["x"], "w", "blueprint", force=True, layer=1)
        ui = ag.backlog.add("Notes page", "ui", "feature", 5, ["x"], "w", "blueprint", force=True, layer=2)
        base["status"] = "done"
        self.assertEqual(ag.link_foundation(), 2)
        by = {i["id"]: i for i in ag.backlog.items}
        self.assertEqual(by[api["id"]]["depends_on"], [base["id"]])  # shares words with the schema task
        self.assertEqual(by[ui["id"]]["depends_on"], [api["id"]])
        self.assertNotIn("depends_on", by[other["id"]])  # the first layer is the root
        self.assertEqual(ag.link_foundation(), 0)
        ag.state["merged"] = 1
        self.assertTrue(ag.rebuild_pending())
        self.assertIn("REBUILD THE GRAPH", ag.rebuild_note())
        ag.rebuilt()
        self.assertFalse(ag.rebuild_pending())
        ag.rebuild_flag.write_text("1")
        self.assertTrue(ag.rebuild_pending())

    def test_files_on_nodes_give_small_context_and_file_links(self):
        from core import graphctx
        ag = self.ag
        add = lambda t, lay, **k: ag.backlog.add(t, t, "feature", 5, ["x"], "w", "blueprint", force=True, layer=lay, **k)  # noqa: E731
        a, b = add("Notes store", 0), add("Notes API", 1)
        a.update(status="done", files=["core/store.py", "core/db.py"], result="SQLite store")
        c = add("Export notes", 1)
        c["files"] = ["core/store.py"]
        b["depends_on"] = [a["id"]]
        pack = graphctx.context_pack(ag.backlog.items, b)
        self.assertIn("core/store.py", pack["text"])
        self.assertEqual(pack["files"][:2], ["core/store.py", "core/db.py"])
        self.assertLess(len(graphctx.context_pack(ag.backlog.items, c)["text"]), graphctx.PACK_CHARS + 1)
        self.assertEqual(graphctx.file_links(ag.backlog.items), {c["id"]: [a["id"]]})  # the same file as a finished task
        unplaced = add("Search", None)
        unplaced["files"] = ["core/store.py"]
        self.assertEqual(graphctx.place(ag.backlog.items), {unplaced["id"]: 1})
        self.assertEqual(graphctx.prefer_files(["x.py", "gone.py"], ["y.py", "x.py"], lambda f: f != "gone.py"), ["x.py", "y.py"])


if __name__ == "__main__":
    unittest.main()
