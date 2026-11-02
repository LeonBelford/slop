import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import cfa_build
from core.cfa_build import BuildMixin


class Git:
    def run(self, *a):
        return 0, ""


class Agent(BuildMixin):
    def __init__(self, d):
        self.repo = Path(d)
        self.dir = Path(d)
        self.run_dir = Path(d)
        self.git = Git()
        self.coder_mock = None
        self.lim = {"coder_timeout": 5}
        self.logs = []
        self.llm = mock.MagicMock()
        self.notes = []

    def log(self, m):
        self.logs.append(m)

    def note(self, k, t):
        self.notes.append(t)

    def coder_for(self, tag):
        return "ollama/x"

    def journal_file(self):
        return []

    def handoff_text(self):
        return ""


class WriteGuardTests(unittest.TestCase):
    def test_chat_only_answer_triggers_whole_file_retry(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text("x=1")
            calls = []

            def fake(cmd, *a, **k):
                calls.append(cmd)
                if "whole" in cmd:
                    (Path(d) / "a.py").write_text("x=2")
                    return 0, "done"
                return 0, "here:\n```python\nx=2\n```\n"
            ag = Agent(d)
            with mock.patch.object(cfa_build, "sh_live", fake), mock.patch.object(cfa_build.perf, "fit_context", lambda e, r, p, repo: (e, r, [])):
                ag.code("debug1", ["a.py"], [], "fix")
            self.assertEqual(len(calls), 2)
            self.assertIn("--edit-format", calls[1])
            self.assertEqual((Path(d) / "a.py").read_text(), "x=2")

    def test_no_retry_when_file_was_written(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text("x=1")

            def fake(cmd, *a, **k):
                (Path(d) / "a.py").write_text("x=3")
                return 0, "```\nx\n```\n```\n```"
            ag = Agent(d)
            with mock.patch.object(cfa_build, "sh_live", fake), mock.patch.object(cfa_build.perf, "fit_context", lambda e, r, p, repo: (e, r, [])):
                ag.code("debug1", ["a.py"], [], "fix")
            self.assertEqual(ag.logs, [])


if __name__ == "__main__":
    unittest.main()


class FallbackTests(unittest.TestCase):
    def test_answer_in_chat_is_written_to_disk_when_retry_fails_too(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text("x=1")
            ag = Agent(d)
            answer = "a.py\n```python\nx = 2\ny = 3\nz = 4\n```\n"
            with mock.patch.object(cfa_build, "sh_live", lambda *a, **k: (0, answer)), mock.patch.object(cfa_build.perf, "fit_context", lambda e, r, p, repo: (e, r, [])):
                ag.code("debug1", ["a.py"], [], "fix")
            self.assertIn("y = 3", (Path(d) / "a.py").read_text())
            self.assertTrue(any("written from the chat" in n for n in ag.notes))

    def test_unlisted_file_is_never_written(self):
        from core.cfa_write import extract_files
        self.assertEqual(extract_files("../evil.py\n```\nx\n```", ["a.py"]), {})


class NoThinkTests(unittest.TestCase):
    def test_qwen3_coder_gets_no_think_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            ag = Agent(d)
            ag.coder_for = lambda tag: "ollama/qwen3:14b"
            ag.feat = lambda n: True
            (Path(d) / "a.py").write_text("x")
            seen = []
            with mock.patch.object(cfa_build, "sh_live", lambda *a, **k: (0, "")), mock.patch.object(cfa_build.perf, "fit_context", lambda e, r, p, repo: (e, r, [])), \
                    mock.patch.object(cfa_build, "write_text", lambda p, t: seen.append(t)):
                ag.code("implement", ["a.py"], [], "do it")
            self.assertTrue(seen[0].startswith("/no_think"))
