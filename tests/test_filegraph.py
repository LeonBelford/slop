import unittest

from core import filegraph


class FileGraphTest(unittest.TestCase):
    def test_imports_and_tasks(self):
        files = ["app.py", "core/util.py", "web/a.js", "web/b.js"]
        src = {"app.py": "from core.util import x\nimport os\n", "web/a.js": "import {b} from './b.js'\n"}
        items = [{"id": f"T{n}", "title": "t", "status": "done", "files": ["core/util.py"]} for n in range(1, 7)]
        g = filegraph.build(files, lambda f: src.get(f, ""), items)
        self.assertIn(["app.py", "core/util.py"], g["edges"])
        self.assertIn(["web/a.js", "web/b.js"], g["edges"])
        u = next(n for n in g["nodes"] if n["path"] == "core/util.py")
        self.assertEqual([t["id"] for t in u["tasks"]], ["T6", "T5", "T4", "T3"])
        self.assertEqual((u["more"], u["total"], u["used_by"]), (2, 6, 1))


if __name__ == "__main__":
    unittest.main()


class PackHistoryTest(unittest.TestCase):
    def test_pack_names_earlier_tasks_on_the_same_files(self):
        from core import graphctx
        mk = lambda n, st, f: {"id": f"T{n}", "title": f"task {n}", "status": st, "files": f, "depends_on": [], "type": "feature"}  # noqa: E731
        items = [mk(1, "done", ["a.py"]), mk(2, "done", ["a.py"]), mk(3, "pending", ["a.py"])]
        text = graphctx.context_pack(items, items[2])["text"]
        self.assertIn("Earlier tasks that touched the same files", text)
        self.assertIn("T1", text)
        self.assertIn("T2", text)

    def test_pack_neighbourhood_and_hotspot(self):
        from core import graphctx
        cm = {"a.py": {"imports": ["b.py"], "imported_by": ["c.py", "d.py"]}, "b.py": {}}
        it = {"id": "T1", "title": "t", "status": "pending", "files": ["a.py"], "depends_on": [], "type": "feature"}
        text = graphctx.context_pack([it], it, codemap=cm)["text"]
        self.assertIn("your files use b.py", text)
        self.assertIn("used by c.py, d.py", text)
        busy = graphctx.hotspot_score("a.py", 100, 2, [it], cm)
        self.assertGreater(busy, graphctx.hotspot_score("b.py", 100, 2, [it], cm))

    def test_budget_trims_only_the_builds_on_list(self):
        from core import graphctx
        items = [{"id": f"T{n}", "title": "x" * 70, "status": "done", "files": ["a.py"], "result": "r" * 100, "depends_on": [f"T{n - 1}"] if n > 1 else [], "type": "feature"} for n in range(1, 9)]
        cur = {"id": "T9", "title": "t", "status": "pending", "files": ["a.py"], "depends_on": ["T8"], "type": "feature"}
        cm = {"a.py": {"imports": ["b.py"], "imported_by": ["c.py"]}}
        text = graphctx.context_pack(items + [cur], cur, codemap=cm)["text"]
        self.assertLessEqual(len(text), graphctx.PACK_CHARS)
        self.assertIn("File neighbourhood", text)

    def test_python_import_forms(self):
        files = {"core/graphctx.py", "core/__init__.py", "core/cfa_build.py", "tests/json.py", "pkg/util.py"}
        text = "from core import graphctx\nfrom . import graphctx as g\nfrom .graphctx import x\nimport json\nfrom pkg.util import y\n"
        self.assertEqual(filegraph.imports_of("core/cfa_build.py", text, files), {"core/graphctx.py", "pkg/util.py"})

    def test_blocked_process_step_does_not_hold_back_successors(self):
        import tempfile
        from pathlib import Path

        from core.cfgit import Backlog
        bl = Backlog(Path(tempfile.mkdtemp()) / "b.json")
        step = bl.add("Docs pass", "d", force=True)
        bl.set(step, status="blocked", process="p1")
        nxt = bl.add("Feature", "d", force=True, depends_on=[step["id"]])
        self.assertTrue(bl.ready([nxt]))
