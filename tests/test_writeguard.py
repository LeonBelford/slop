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


class TokenLimitTests(unittest.TestCase):
    def test_detects_aider_limit_messages(self):
        from infra import perf
        self.assertTrue(perf.token_limit_hit("Model ollama_chat/x has hit a token limit!\nFor more info: https://aider.chat/docs/troubleshooting/token-limits.html"))
        self.assertTrue(perf.token_limit_hit("Your estimated chat context of 20,000 tokens exceeds the 16,384 token limit"))
        self.assertFalse(perf.token_limit_hit("Applied edit to a.py"))

    def test_lean_cmd_keeps_one_read_file_and_drops_the_map(self):
        from infra import perf
        cmd = ["aider", "--model", "m", "--message-file", "p.md", "--read", "ctx.md", "--read", "a.py", "--read", "b.py", "--map-tokens", "1024", "src/x.rs"]
        lean = perf.lean_cmd(cmd)
        self.assertEqual(lean.count("--read"), 1)
        self.assertIn("ctx.md", lean)
        self.assertNotIn("a.py", lean)
        self.assertEqual(lean[lean.index("--map-tokens") + 1], "0")
        self.assertEqual(lean[-1], "src/x.rs")
        self.assertEqual(perf.AIDER_ENV["BROWSER"], "true")


class TokenLimitRetryTests(unittest.TestCase):
    def test_limit_triggers_one_lean_retry_and_never_opens_a_browser(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text("x=1")
            calls = []

            def fake(cmd, *a, **k):
                calls.append((cmd, k.get("env")))
                if len(calls) == 1:
                    return 0, "Model x has hit a token limit!\nhttps://aider.chat/docs/troubleshooting/token-limits.html"
                (Path(d) / "a.py").write_text("x=2")
                return 0, "ok"
            ag = Agent(d)
            with mock.patch.object(cfa_build, "sh_live", fake), mock.patch.object(cfa_build.perf, "fit_context", lambda e, r, p, repo: (e, ["ctx.md", "big.py"], [])):
                ag.code("t1", ["a.py"], [], "fix")
            self.assertEqual(len(calls), 2)
            self.assertNotIn("big.py", calls[1][0])
            self.assertEqual(calls[1][0][calls[1][0].index("--map-tokens") + 1], "0")
            self.assertTrue(all(env["BROWSER"] == "true" for _, env in calls))
            self.assertTrue(any("token" in n for n in ag.notes))
