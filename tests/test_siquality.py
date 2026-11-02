import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.cfllm import scaled
from dash import modcards, sievidence, sifiles, sigate, siwatch


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


class ScaledTest(unittest.TestCase):
    def test_full_size_when_window_unknown_and_smaller_when_small(self):
        class L:
            server_ctx = 0
        self.assertEqual(scaled(L(), 9000), 9000)
        L.server_ctx = 4096
        self.assertLess(scaled(L(), 9000), 3500)
        self.assertGreaterEqual(scaled(L(), 9000), 2700)  # never below the floor
        L.server_ctx = 32768
        self.assertEqual(scaled(L(), 9000), 9000)


class CardsTest(unittest.TestCase):
    def test_cards_list_signatures_and_users(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / "dash").mkdir()
            (r / "dash" / "a.py").write_text('"""Alpha module."""\ndef run(x, *, y=1):\n    """Runs."""\n\nclass B:\n    def go(self): pass\n', "utf-8")
            (r / "dash" / "b.py").write_text("from dash import a\n", "utf-8")
            out = modcards.render(r, ["dash/a.py"])
            self.assertIn("Alpha module.", out)
            self.assertIn("def run(x, y): Runs.", out)
            self.assertIn(".go(self)", out)
            self.assertIn("Used by: dash/b.py", out)
            (r / "docs").mkdir()
            self.assertTrue(modcards.write(r).read_text("utf-8").startswith("# Module cards"))

    def test_repo_cards_cover_every_module(self):
        info = modcards.parse(Path(__file__).resolve().parent.parent)
        self.assertIn("selfimprove.py", info)
        self.assertIn("dash/sigate.py", info)


class FilesTest(unittest.TestCase):
    def test_symbol_names_find_the_file(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / "gpu.py").write_text("def schedule_jobs():\n    pass\n", "utf-8")
            (r / "other.py").write_text("x = 1\n", "utf-8")
            self.assertEqual(sifiles._keyword_files(r, "fix the schedule logic", ["gpu.py", "other.py"], []), ["gpu.py"])


class EvidenceTest(unittest.TestCase):
    def test_bench_weaknesses_and_health(self):
        rep = {"label": "base", "results": [{"id": "a", "ok": False, "why": "boom"}, {"id": "b", "ok": True, "attempts": 3}, {"id": "c", "ok": True, "attempts": 1}]}
        w = sievidence.bench_weaknesses([rep])
        self.assertEqual([x["task"] for x in w], ["a", "b"])
        self.assertEqual(sievidence.bench_weaknesses([]), [])
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / "core").mkdir()
            (r / "core" / "big.py").write_text("x = 1\n" * 700, "utf-8")
            (r / "core" / "loose.py").write_text("x = 1\n", "utf-8")
            (r / "PROJECT_MAP.md").write_text("`core/big.py`", "utf-8")
            kinds = {(h["metric"], h["file"]) for h in sievidence.health(r)}
            self.assertEqual(kinds, {("module_size", "core/big.py"), ("unmapped", "core/loose.py")})


class GateTest(unittest.TestCase):
    def _repo(self, t, base_ok):
        r = Path(t)
        (r / "bench" / "results").mkdir(parents=True)
        (r / "bench" / "results" / "base.json").write_text(json.dumps({"label": "base", "results": [{"id": "x", "ok": base_ok, "seconds": 1, "attempts": 1}]}), "utf-8")
        return r

    def test_off_by_default_and_modes(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._repo(t, True)
            with mock.patch.dict("os.environ", {"SI_BENCH_GATE": ""}):
                self.assertEqual(sigate.evaluate(r, r, "s", lambda m: None)["verdict"], "skipped")
            worse = {"label": "n", "results": [{"id": "x", "ok": False, "seconds": 1, "attempts": 1}]}
            for mode, block in (("warn", False), ("block", True)):
                with mock.patch.dict("os.environ", {"SI_BENCH_GATE": mode}):
                    res = sigate.evaluate(r, r, "s", lambda m: None, run=lambda *a: worse)
                self.assertEqual((res["verdict"], res["block"]), ("worse", block))
            with mock.patch.dict("os.environ", {"SI_BENCH_GATE": "block"}):
                self.assertEqual(sigate.evaluate(r, r, "s", lambda m: None, run=lambda *a: None)["verdict"], "error")

    def test_no_baseline_is_skipped(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict("os.environ", {"SI_BENCH_GATE": "block"}):
            self.assertEqual(sigate.evaluate(Path(t), Path(t), "s", lambda m: None)["verdict"], "skipped")


class WatchdogTest(unittest.TestCase):
    def test_rollback_reverts_selfimprove_merge_and_refuses_dirty_tree(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            git(r, "init", "-q", "-b", "main")
            (r / "a.txt").write_text("1", "utf-8")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "init")
            git(r, "checkout", "-qb", "self-improve/x")
            (r / "b.txt").write_text("2", "utf-8")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "change")
            git(r, "checkout", "-q", "main")
            (r / "c.txt").write_text("3", "utf-8")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "other")
            git(r, "merge", "--no-ff", "-qm", "Merge branch 'self-improve/x'", "self-improve/x")
            self.assertTrue(siwatch.last_selfimprove_merge(r))
            (r / "a.txt").write_text("dirty", "utf-8")
            self.assertFalse(siwatch.rollback(r)[0])
            git(r, "checkout", "--", "a.txt")
            ok, msg = siwatch.rollback(r)
            self.assertTrue(ok, msg)
            self.assertFalse((r / "b.txt").exists())
            self.assertTrue((r / "c.txt").exists())

    def test_hook_and_last_result(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            self.assertFalse(siwatch.install_hook(r))
            (r / ".git" / "hooks").mkdir(parents=True)
            self.assertTrue(siwatch.install_hook(r))
            (r / ".git" / "hooks" / "post-merge").write_text("#!/bin/sh\necho mine\n", "utf-8")
            self.assertFalse(siwatch.install_hook(r))
            self.assertEqual(siwatch.last_result(r), {})


if __name__ == "__main__":
    unittest.main()
