import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cfa_build import BuildMixin  # noqa: E402
from core.cfmemory import Memory  # noqa: E402


class Calls:
    def __init__(self):
        self.names = []

    def json(self, name, *a, **k):
        self.names.append(name)
        return {"files": ["a.py"], "purpose": "p"}

    def chat(self, name, *a, **k):
        self.names.append(name)
        return "body"


def memory(tmp):
    m = Memory.__new__(Memory)
    m.dir, m.repo, m.log = Path(tmp) / "agent", Path(tmp) / "repo", lambda *_: None
    m.dir.mkdir(parents=True)
    (m.repo / "docs").mkdir(parents=True)
    m.cfg, m.llm = {"doc_language": "English"}, Calls()
    m.codemap = {"a.py": {"purpose": "p", "loc": 3}, "b.py": {"purpose": "q", "loc": 3}}
    (m.repo / "a.py").write_text("x=1\n")
    (m.repo / "b.py").write_text("y=1\n")
    return m


class SpeedTests(unittest.TestCase):
    def test_graph_known_files_skip_the_file_selection_call(self):
        with tempfile.TemporaryDirectory() as t:
            m = memory(t)
            m.select_candidates({"title": "a", "description": "a"}, use_llm=False)
            self.assertEqual(m.llm.names, [])
            m.select_candidates({"title": "a", "description": "a"})
            self.assertEqual(m.llm.names, ["select_files"])

    def test_module_docs_are_queued_and_written_once_per_layer(self):
        with tempfile.TemporaryDirectory() as t:
            m = memory(t)
            m.write_module_doc("a.py", body=False)
            self.assertEqual(m.llm.names, [])
            m.queue_docs(["a.py", "b.py", "gone.py"])
            self.assertEqual(m.flush_docs(), 2)
            self.assertEqual(m.llm.names, ["module_doc", "module_doc"])
            self.assertEqual(json.loads((m.dir / "docs_pending.json").read_text()), [])

    def test_small_task_records_the_decision_without_a_model_call(self):
        with tempfile.TemporaryDirectory() as t:
            m = memory(t)
            m.add_decision({"id": "T1", "title": "t", "description": "d"}, {}, {"summary": "s"}, "", use_llm=False)
            self.assertEqual(m.llm.names, [])
            self.assertIn("T1", (m.repo / "docs" / "DECISIONS.md").read_text())

    def test_plan_critique_only_where_it_can_find_something(self):
        class A(BuildMixin):
            cfg = {"features": {}}

            def log(self, m):
                pass

        a = A()
        a.mem = type("M", (), {"codemap": {"a.py": {"imported_by": []}, "b.py": {"imported_by": ["c.py"]}}})()
        self.assertFalse(a.critique_pays({"attempts": 0}, {"files_to_change": ["a.py"]}, []))
        self.assertTrue(a.critique_pays({"attempts": 0}, {"files_to_change": ["b.py"]}, []))  # c.py depends on it
        self.assertTrue(a.critique_pays({"attempts": 0}, {"files_to_change": ["a.py"]}, ["earlier decision"]))
        self.assertTrue(a.critique_pays({"attempts": 0, "risk": "high"}, {"files_to_change": ["a.py"]}, []))


if __name__ == "__main__":
    unittest.main()


class SnippetTests(unittest.TestCase):
    def test_failure_report_is_turned_into_code_snippets_of_project_files_only(self):
        from core import failures
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "app").mkdir()
            (root / "app" / "m.py").write_text("\n".join(f"line{i}" for i in range(1, 30)))
            rep = f'File "/usr/lib/python3/x.py", line 3\n  File "{root}/app/m.py", line 10, in f\napp/m.py:12: AssertionError'
            out = failures.snippets(rep, root)
            self.assertIn("app/m.py", out)
            self.assertIn("  10| line10", out)
            self.assertNotIn("x.py", out)
            self.assertEqual(failures.snippets("nothing useful", root), "")


class RouteTests(unittest.TestCase):
    def test_route_lists_only_the_steps_a_task_goes_through(self):
        from core import progress
        self.assertEqual(progress.route_for(), ["plan", "implement", "build", "docs", "merge"])
        self.assertEqual(progress.route_for(["e2e", "security"]), ["plan", "implement", "build", "e2e", "security", "docs", "merge"])
        full = progress.route_for(["coverage", "mutation", "e2e", "security", "review", "acceptance"], True)
        self.assertEqual(full, [n for n, _ in progress.AGENT_PIPELINE])
        self.assertEqual(progress.step_pct([("a", 50), ("b", 50)], "b", 0.0), 50)


class DoneLedgerTests(unittest.TestCase):
    def test_finished_tasks_survive_stale_writes_and_replans(self):
        import tempfile
        from pathlib import Path
        from core.cfgit import Backlog
        with tempfile.TemporaryDirectory() as d:
            bl = Backlog(Path(d) / "backlog.json")
            a, b = bl.add("one", "d"), bl.add("two", "d", force=True)
            bl.set(a, status="done")
            bl.items[:] = [b]  # a re-plan or stale writer that dropped the finished task
            bl.save()
            self.assertEqual([i["status"] for i in Backlog(Path(d) / "backlog.json").items if i["id"] == a["id"]], ["done"])
            bl.by_id(a["id"])["status"] = "pending"  # a rollback that downgraded it
            bl.save()
            self.assertEqual(bl.by_id(a["id"])["status"], "done")
            bl.set(bl.by_id(a["id"]), status="pending")  # deliberate reopen still works
            bl.save()
            self.assertEqual(bl.by_id(a["id"])["status"], "pending")
            bl.set(b, status="done")
            bl.items[:] = []
            bl.forget(b["id"])
            bl.save()
            self.assertEqual(bl.items, [])
