import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import codefactory as sa
import runledger
import selfimprove


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_old.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test(self): self.assertTrue(True)\n")
    (repo / "app.py").write_text("X = 1\n")
    (repo / ".gitignore").write_text("self_improve/\n__pycache__/\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


class RepairLoopTests(unittest.TestCase):
    def run_directed(self, coder, tests_green):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            sa.LLM.mock = lambda tag, *a: json.dumps({"files": ["app.py"]}) if tag == "tool_files" else "{}"
            real = selfimprove._tests_green
            selfimprove._tests_green = tests_green
            try:
                res = selfimprove.directed("Change X to 2 please", repo=repo, coder=coder, log=lambda m: None)
            finally:
                sa.LLM.mock = None
                selfimprove._tests_green = real
            run = runledger.list_runs(repo / "self_improve")[0]
            full = runledger.load_run(repo / "self_improve", run["id"])
            patches = list((repo / "self_improve").glob("failed-*.patch"))
            return res, full, patches

    def test_failed_test_is_diagnosed_and_repaired(self):
        prompts, calls = [], {"n": 0}

        def coder(work, files, prompt):
            prompts.append(prompt)
            if prompt.startswith(("Add tests", "Documentation update")):
                return
            (work / "app.py").write_text(f"X = {2 if prompts and any(p.startswith('Repair round') for p in prompts) else 99}\n")

        def tests_green(work, t):
            calls["n"] += 1
            return (calls["n"] > 1), ("AssertionError: X is 99" if calls["n"] == 1 else "ok")

        res, run, _ = self.run_directed(coder, tests_green)
        self.assertTrue(res["branch"])
        self.assertTrue(any(p.startswith("Repair round 1") and "AssertionError: X is 99" in p for p in prompts))
        self.assertEqual(run["proposals"][0]["rounds"], 1)
        self.assertEqual(run["proposals"][0]["status"], "kept")

    def test_gives_up_after_repair_rounds_and_keeps_patch(self):
        def coder(work, files, prompt):
            if not prompt.startswith(("Add tests", "Documentation update")):
                (work / "app.py").write_text("X = 3\n")

        res, run, patches = self.run_directed(coder, lambda w, t: (False, "still red"))
        self.assertIsNone(res["branch"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["proposals"][0]["rounds"], selfimprove.FIX_ROUNDS + 1)
        self.assertEqual(len(patches), 1)

    def test_directed_run_restores_protected_and_allows_test_adaptation(self):
        def coder(work, files, prompt):
            if prompt.startswith(("Add tests", "Documentation update")):
                return
            (work / "app.py").write_text("X = 2\n")
            (work / "security.py").write_text("# tampered\n")
            with open(work / "tests" / "test_old.py", "a") as f:
                f.write("# adapted\n")

        res, run, _ = self.run_directed(coder, lambda w, t: (True, "ok"))
        self.assertTrue(res["branch"])
        self.assertEqual(run["proposals"][0]["tests_modified"], ["tests/test_old.py"])
        self.assertIn("restored protected", run["proposals"][0]["note"])

    def test_multi_requirement_runs_in_steps_and_keeps_passing_ones(self):
        prompts = []

        def coder(work, files, prompt):
            prompts.append(prompt)
            if prompt.startswith(("Add tests", "Documentation update")):
                return
            with open(work / "app.py", "a") as f:
                f.write("# step\n")

        sa.LLM.mock = lambda tag, *a: json.dumps({"requirements": ["first thing", "second thing"]}) if tag == "tool_requirements" else json.dumps({"files": ["app.py"]})
        try:
            with tempfile.TemporaryDirectory() as d:
                repo = make_repo(Path(d))
                n = {"i": 0}

                def green(w, t):
                    n["i"] += 1
                    return (n["i"] <= 1 or n["i"] > 4), "red"

                real = selfimprove._tests_green
                selfimprove._tests_green = green
                try:
                    res = selfimprove.directed("Do first thing and second thing", repo=repo, coder=coder, log=lambda m: None)
                finally:
                    selfimprove._tests_green = real
        finally:
            sa.LLM.mock = None
        self.assertEqual(len(res["kept"]), 1)
        self.assertTrue(any("ONLY this step" in p and "first thing" in p for p in prompts))
        self.assertTrue(any("step 2 of 2" in p for p in prompts))

    def test_protected_violation_rules(self):
        changes = ["M\ttests/test_old.py", "M\tsecurity.py", "D\ttests/test_x.py", "D\tPROJECT_MAP.md"]
        self.assertEqual(selfimprove.protected_violations(changes, selfimprove.PROTECTED_DIRECTED),
                         ["tests/test_old.py", "security.py", "tests/test_x.py", "PROJECT_MAP.md"])
        self.assertEqual(selfimprove.protected_violations(changes, selfimprove.PROTECTED_DIRECTED, edit_tests=True),
                         ["security.py", "tests/test_x.py", "PROJECT_MAP.md"])


if __name__ == "__main__":
    unittest.main()


class SmartHelpersTests(unittest.TestCase):
    def test_keyword_files_and_lessons_and_stats(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            (repo / "gpu.py").write_text("def scheduler(): pass\n# scheduler scheduler\n")
            self.assertEqual(selfimprove._keyword_files(repo, "improve the scheduler", ["app.py", "gpu.py"], []), ["gpu.py"])
            selfimprove.STATE = repo / "self_improve"
            selfimprove._remember("t", "test", "boom", "cause x", True)
            self.assertIn("boom", selfimprove._lessons())
            (repo / "self_improve" / "runs").mkdir()
            (repo / "self_improve" / "runs" / "a.json").write_text(json.dumps({"proposals": [
                {"status": "kept", "rounds": 1}, {"status": "reverted", "rounds": 3}]}))
            st = runledger.stats(repo / "self_improve")
            self.assertEqual((st["proposals"], st["kept"], st["repaired"], st["success_rate"]), (2, 1, 1, 0.5))

    def test_new_test_that_passes_without_change_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            (repo / "tests" / "test_new.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test(self): self.assertTrue(True)\n")
            git(repo, "add", "-A")
            msg = selfimprove._new_tests_red(repo, ["A\ttests/test_new.py"])
            self.assertIn("already pass", msg)
            self.assertEqual(selfimprove._fast_tests(repo, ["A\ttests/test_new.py"])[0], True)
