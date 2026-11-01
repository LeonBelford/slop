import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import failures  # noqa: E402
from core.cfgit import Backlog  # noqa: E402
from infra import perf  # noqa: E402


class FailureTests(unittest.TestCase):
    def test_classes(self):
        c = failures.classify
        self.assertEqual(c("the coder made no usable edit"), "no_edit")
        self.assertEqual(c("Build/test red after debug rounds:\n[test] FAILED rc=1"), "test_fail")
        self.assertEqual(c("[build] FAILED rc=2"), "build_fail")
        self.assertEqual(c("[policy] No tests were written"), "policy")
        self.assertEqual(c("Security gate failed: x"), "security")
        self.assertEqual(c("TIMEOUT after 300s: pytest"), "timeout")
        self.assertEqual(c("something odd"), "other")

    def test_signature_ignores_numbers_and_paths_of_noise(self):
        a = "[test] FAILED rc=1\n$ pytest\nAssertionError: expected 3 got 4 at 0x7f12"
        b = "[test] FAILED rc=1\n$ pytest\nAssertionError: expected 9 got 1 at 0x99aa"
        self.assertEqual(failures.signature(a), failures.signature(b))
        self.assertNotEqual(failures.signature(a), failures.signature("[test] FAILED rc=1\n$ pytest\nKeyError: 'name'"))


class BacklogPickTests(unittest.TestCase):
    def test_prefers_task_that_matches_the_loaded_model_when_priorities_are_close(self):
        with tempfile.TemporaryDirectory() as t:
            b = Backlog(Path(t) / "backlog.json")
            b.add("doc a", "d", "docs", 5.0)
            b.add("feat b", "d", "feature", 5.5)
            self.assertEqual(b.next(None)["title"], "feat b")
            self.assertEqual(b.next(True)["title"], "doc a")
            self.assertEqual(b.next(False)["title"], "feat b")

    def test_clearly_higher_priority_still_wins(self):
        with tempfile.TemporaryDirectory() as t:
            b = Backlog(Path(t) / "backlog.json")
            b.add("doc a", "d", "docs", 3.0)
            b.add("feat b", "d", "feature", 8.0)
            self.assertEqual(b.next(True)["title"], "feat b")


class ContextBudgetTests(unittest.TestCase):
    def test_read_only_files_are_dropped_to_fit(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / "a.py").write_text("x" * 7000)
            (r / "ctx.md").write_text("y" * 700)
            (r / "big.py").write_text("z" * 40000)
            edit, read, dropped = perf.fit_context(["a.py"], ["ctx.md", "big.py"], "p" * 700, r, num_ctx=8000, reserve=2000)
            self.assertEqual(edit, ["a.py"])
            self.assertEqual(read, ["ctx.md"])
            self.assertEqual(dropped, ["big.py"])

    def test_everything_fits_in_a_big_window(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / "a.py").write_text("x" * 700)
            (r / "b.py").write_text("y" * 700)
            self.assertEqual(perf.fit_context(["a.py"], ["b.py"], "p", r, num_ctx=16384)[2], [])

    def test_aider_gets_a_small_repo_map(self):
        with tempfile.TemporaryDirectory() as t:
            a = perf.aider_args("ollama_chat/x", Path(t))
            self.assertEqual(a[a.index("--map-tokens") + 1], str(perf.MAP_TOKENS))


if __name__ == "__main__":
    unittest.main()
