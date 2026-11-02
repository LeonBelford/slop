import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import selfimprove as si  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, check=True, capture_output=True)


class Ledger:
    d = {"kind": "auto", "proposals": [{}]}

    def stage(self, *a, **k):
        pass

    def save(self):
        pass

    def proposal(self, *a, **k):
        pass


class ParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_pinned_block_leads_the_prompt(self):
        pin = si._pin({"title": "T"}, "plan", "implement", do=["x"], do_not=["y"], done_when=["z"])
        self.assertTrue(pin.startswith("INSTRUCTIONS from the plan step to the implement step"))
        self.assertIn("Goal: T", pin)

    def test_write_guard_writes_a_complete_file_from_the_chat_answer(self):
        (self.w / "a.py").write_text("x = 1\n")
        before = si._fingerprint(self.w, ["a.py"])
        answer = "a.py\n```python\nx = 2\ny = 3\nz = 4\n```\n"
        extra = si._write_guard(self.w, ["a.py"], before, answer, ["aider"], self.w / "p.md", "prompt", 5)
        self.assertEqual((self.w / "a.py").read_text(), "x = 2\ny = 3\nz = 4\n")
        self.assertIn("write guard", extra)

    def test_write_guard_does_nothing_when_a_file_changed_and_retries_once_otherwise(self):
        (self.w / "a.py").write_text("x = 1\n")
        before = si._fingerprint(self.w, ["a.py"])
        (self.w / "a.py").write_text("x = 5\n")
        self.assertEqual(si._write_guard(self.w, ["a.py"], before, "", ["aider"], self.w / "p.md", "prompt", 5), "")
        calls = []
        with mock.patch.object(si.sa, "sh_live", lambda cmd, *a, **k: calls.append(cmd) or (0, "no files")):
            si._write_guard(self.w, ["a.py"], si._fingerprint(self.w, ["a.py"]), "chatter", ["aider"], self.w / "p.md", "prompt", 5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], ["--edit-format", "whole"])

    def test_deleting_a_test_is_rejected_by_the_gates(self):
        git(self.w, "init", "-q")
        (self.w / "tests").mkdir()
        (self.w / "tests" / "test_a.py").write_text("def test_one():\n    pass\n\ndef test_two():\n    pass\n")
        (self.w / "m.py").write_text("a = 1\n")
        git(self.w, "add", "-A")
        git(self.w, "commit", "-qm", "base")
        (self.w / "m.py").write_text("a = 2\n")
        (self.w / "tests" / "test_a.py").write_text("def test_one():\n    pass\n")
        ok, report, stage = si._gates(self.w, {"title": "t"}, ["m.py"], None, None, lambda m: None, Ledger(), 0, si.PROTECTED, True,
                                      {"tests_round": False, "notes": []})
        self.assertEqual((ok, stage), (False, "policy"))
        self.assertIn("test_two", report)


if __name__ == "__main__":
    unittest.main()
