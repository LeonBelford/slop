import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import webui  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, check=True, capture_output=True)


class CodeViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "demo"
        (self.ws / "agent").mkdir(parents=True)
        (self.ws / "agent" / "GOAL.md").write_text("g")
        self.repo = self.ws / "repo"
        (self.repo / "src").mkdir(parents=True)
        git(self.repo, "init", "-q")
        (self.repo / "src" / "a.py").write_text("one\ntwo\nthree\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        (self.repo / "src" / "a.py").write_text("one\nTWO\nthree\nfour\n")
        (self.repo / "new.txt").write_text("x\ny\n")
        self.svc = webui.Service(root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tree_marks_changes(self):
        files = {f["path"]: f for f in self.svc.tree("demo")["files"]}
        self.assertEqual(files["src/a.py"]["status"], "M")
        self.assertEqual(files["new.txt"]["status"], "A")

    def test_file_changed_lines(self):
        v = self.svc.file("demo", "src/a.py")
        self.assertEqual(v["changed"], [2, 4])
        self.assertTrue(self.svc.file("demo", "new.txt")["new_file"])

    def test_file_rejects_escape(self):
        for bad in ("../x", "/etc/passwd", ".git/config", ""):
            with self.assertRaises(webui.UiError):
                self.svc.file("demo", bad)

    def test_thoughts(self):
        (self.ws / "agent" / "thoughts.jsonl").write_text(json.dumps({"n": 1, "tag": "impact", "answer": "{}"}) + "\n")
        self.assertEqual(self.svc.thoughts("demo")["entries"][0]["tag"], "impact")

    def test_thoughts_brief_limits_and_clips_for_the_always_visible_panel(self):
        rows = [json.dumps({"n": n, "tag": "impact", "thinking": "x" * 9000, "answer": "{}", "prompt": "p" * 5000}) for n in range(1, 6)]
        (self.ws / "agent" / "thoughts.jsonl").write_text("\n".join(rows) + "\n")
        r = self.svc.thoughts("demo", 2, True)
        self.assertEqual([e["n"] for e in r["entries"]], [5, 4])
        self.assertLess(len(r["entries"][0]["thinking"]), 4100)
        self.assertLess(len(r["entries"][0]["prompt"]), 1600)


if __name__ == "__main__":
    unittest.main()
