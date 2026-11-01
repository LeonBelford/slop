import tempfile
import unittest
from pathlib import Path

from core import blueprint, uidetect
from core.cfgit import Backlog

PLAN = {"architecture": {"principles": ["core first"], "contracts": ["f() -> int"]}, "layers": [
    {"name": "Core", "goal": "kernel", "tasks": [{"key": "a", "title": "Data model", "value": 8, "effort": 2},
                                                  {"key": "b", "title": "Storage", "depends_on": ["a", "zzz"], "value": 5, "effort": 5}]},
    {"name": "Features", "goal": "uses core", "tasks": [{"key": "c", "title": "Export feature", "depends_on": ["b", "c"], "value": 9, "effort": 1}]}]}


class BlueprintTests(unittest.TestCase):
    def test_normalize_cleans_dependencies_and_adds_missing_mvp(self):
        bp = blueprint.normalize(PLAN, [{"title": "Search by tag", "value": 7}])
        tasks = [t for ly in bp["layers"] for t in ly["tasks"]]
        self.assertEqual(tasks[1]["depends_on"], ["a"])
        self.assertEqual(tasks[2]["depends_on"], ["b"])
        self.assertEqual(tasks[-1]["title"], "Search by tag")
        self.assertEqual(blueprint.normalize({"layers": []})["layers"], [])

    def test_context_and_progress(self):
        bp = blueprint.normalize(PLAN)
        ctx = blueprint.context_for(bp, {"layer": 1})
        self.assertIn("Layer 1: Features", ctx)
        self.assertIn("0: Core", ctx)
        self.assertEqual(blueprint.context_for(bp, {}), "")
        self.assertEqual(blueprint.context_for(None, {"layer": 0}), "")
        rows = blueprint.progress(bp, [{"layer": 0, "status": "done"}, {"layer": 0, "status": "pending"}, {"layer": 2, "status": "done"}])
        self.assertEqual([(r["layer"], r["done"], r["total"]) for r in rows], [(0, 1, 2), (1, 0, 0), (2, 1, 1)])
        self.assertEqual(blueprint.clamp_layer(bp, 9), 2)
        self.assertEqual(blueprint.clamp_layer(bp, "x"), 2)
        self.assertIsNone(blueprint.clamp_layer(None, 1))


class ReevaluationTests(unittest.TestCase):
    def test_clamps_layers_protects_user_tasks_and_drops_unknown_ids(self):
        items = [{"id": "T1", "source": "user"}, {"id": "T2", "source": "ideation"}]
        r = blueprint.normalize_reevaluation({
            "layers": [{"name": "Core", "goal": "g"}, {"name": "UI", "goal": "g"}],
            "assign": [{"id": "T2", "layer": 9}, {"id": "nope", "layer": 0}],
            "new_tasks": [{"key": "a", "title": "Base", "layer": -3, "depends_on": ["T2", "zzz"]}],
            "cancel": [{"id": "T1"}, {"id": "T2"}, {"id": "ghost"}]}, items)
        self.assertEqual(list(r["assign"]), ["T2"])
        self.assertEqual(r["assign"]["T2"]["layer"], 1)
        self.assertEqual((r["new"][0]["layer"], r["new"][0]["depends_on"]), (0, ["T2"]))
        self.assertEqual(r["cancel"], ["T2"])

    def test_no_layers_means_no_changes(self):
        self.assertEqual(blueprint.normalize_reevaluation({"layers": []}, [])["layers"], [])


class StrategistModelTests(unittest.TestCase):
    def test_default_role_exists_and_falls_back_to_planner(self):
        import codefactory as sa
        self.assertIn("strategist", sa.DEFAULT_CONFIG["models"])


class BacklogOrderTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.b = Backlog(Path(self.d.name) / "b.json")

    def tearDown(self):
        self.d.cleanup()

    def test_lowest_layer_first_regardless_of_priority(self):
        feat = self.b.add("Fancy feature", "d", priority=19, layer=2)
        core = self.b.add("Core model", "d", priority=1, layer=0)
        loose = self.b.add("Loose idea", "d", priority=20)
        self.assertEqual(self.b.next()["id"], core["id"])
        self.b.set(core, status="done")
        self.assertEqual(self.b.next()["id"], feat["id"])
        self.b.set(feat, status="done")
        self.assertEqual(self.b.next()["id"], loose["id"])

    def test_dependencies_wait_and_never_deadlock(self):
        a = self.b.add("A", "d", priority=1, layer=1)
        c = self.b.add("C", "d", priority=9, layer=1, depends_on=[a["id"]])
        self.assertEqual(self.b.next()["id"], a["id"])
        self.b.set(a, status="blocked")
        self.assertEqual(self.b.next()["id"], c["id"])

    def test_user_tasks_and_bugs_still_jump_the_queue(self):
        self.b.add("Core", "d", layer=0)
        bug = self.b.add("Crash", "d", "bug", layer=3)
        self.assertEqual(self.b.next()["id"], bug["id"])
        user = self.b.add("Mine", "d", source="user", layer=5)
        self.assertEqual(self.b.next()["id"], user["id"])


class UiDetectTests(unittest.TestCase):
    def test_cli_has_no_ui_and_web_needs_ui_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "repo").mkdir()
            info = root / "interface.json"
            (root / "repo" / "main.py").write_text("print(1)")
            uidetect.save(info, {"interface": "web", "has_ui": True})
            self.assertFalse(uidetect.has_ui(root / "repo", info), "no UI files yet")
            (root / "repo" / "templates").mkdir()
            self.assertTrue(uidetect.has_ui(root / "repo", info))
            uidetect.save(info, {"interface": "cli", "has_ui": False})
            self.assertFalse(uidetect.has_ui(root / "repo", info), "a CLI never gets UI tests")

    def test_docs_html_does_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "docs").mkdir()
            (Path(d) / "docs" / "index.html").write_text("<p>")
            self.assertFalse(uidetect.repo_has_ui_files(Path(d)))


if __name__ == "__main__":
    unittest.main()
