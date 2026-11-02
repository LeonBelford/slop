import subprocess
import tempfile
import unittest
from pathlib import Path

import webui
from webapi.common import UiError


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "demo"
        (ws / "agent").mkdir(parents=True)
        (ws / "agent" / "GOAL.md").write_text("Build a demo")
        self.repo = ws / "repo"
        self.repo.mkdir()
        (self.repo / "app.py").write_text("def f():\n    return 1\n")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run(["git", *c], cwd=self.repo, check=True, capture_output=True)
        self.svc = webui.Service(Path(self.tmp.name))
        self.svc._running = lambda ws, name: False
        self.ws = ws

    def tearDown(self):
        self.tmp.cleanup()

    def act(self, **body):
        return self.svc.act("demo", "editor", body)

    def test_mentions_lists_files_and_tasks(self):
        (self.ws / "agent" / "backlog.json").write_text('[{"id": "T0001", "title": "x", "status": "pending"}]')
        r = self.svc.editor_get("demo", "mentions", {})
        self.assertIn("app.py", r["files"])
        self.assertEqual(r["tasks"][0]["id"], "T0001")

    def test_save_create_rename_delete_and_path_safety(self):
        self.act(op="save", path="app.py", text="def f():\n    return 2\n")
        self.assertIn("return 2", (self.repo / "app.py").read_text())
        self.act(op="create", path="src/new.py")
        self.assertTrue((self.repo / "src" / "new.py").is_file())
        self.act(op="rename", path="src/new.py", to="src/other.py")
        self.assertTrue((self.repo / "src" / "other.py").is_file())
        self.act(op="delete", path="src/other.py")
        self.assertFalse((self.repo / "src" / "other.py").exists())
        for bad in ("../evil.py", ".git/config", "/etc/passwd"):
            with self.assertRaises(UiError, msg=bad):
                self.act(op="save", path=bad, text="x")

    def test_writes_are_refused_while_the_agent_runs_but_tests_still_run(self):
        self.svc._running = lambda ws, name: True
        with self.assertRaises(UiError) as cm:
            self.act(op="save", path="app.py", text="x")
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("rc", self.act(op="run-tests"))  # not blocked by the running agent

    def test_git_status_head_revert_and_commit(self):
        self.act(op="save", path="app.py", text="changed\n")
        self.act(op="create", path="n.txt")
        g = self.svc.editor_get("demo", "git", {})
        self.assertEqual({f["path"] for f in g["files"]}, {"app.py", "n.txt"})
        self.assertEqual(self.svc.editor_get("demo", "head", {"path": "app.py"})["text"], "def f():\n    return 1\n")
        self.assertIsNone(self.svc.editor_get("demo", "head", {"path": "n.txt"})["text"])
        self.act(op="revert", path="app.py")
        self.assertIn("return 1", (self.repo / "app.py").read_text())
        self.act(op="revert", path="n.txt")
        self.assertFalse((self.repo / "n.txt").exists())
        self.act(op="save", path="app.py", text="v3\n")
        self.act(op="commit", message="third")
        g = self.svc.editor_get("demo", "git", {})
        self.assertEqual(g["files"], [])
        self.assertEqual(g["log"][0]["subject"], "third")
        with self.assertRaises(UiError):
            self.act(op="commit", message="nothing")

    def test_search(self):
        (self.repo / "b.py").write_text("Hello World\nhello again\n")
        r = self.svc.editor_get("demo", "search", {"q": "hello"})
        self.assertEqual([(h["path"], h["line"]) for h in r["hits"]], [("b.py", 1), ("b.py", 2)])
        self.assertEqual(len(self.svc.editor_get("demo", "search", {"q": "hello", "case": "1"})["hits"]), 1)
        self.assertEqual(self.svc.editor_get("demo", "search", {"q": "h.llo", "regex": "1"})["hits"][0]["line"], 1)
        with self.assertRaises(UiError):
            self.svc.editor_get("demo", "search", {"q": "(ab", "regex": "1"})
        self.assertEqual(self.svc.editor_get("demo", "search", {"q": "a"})["hits"], [])


if __name__ == "__main__":
    unittest.main()
