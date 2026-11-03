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
