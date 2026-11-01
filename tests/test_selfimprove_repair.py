import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import codefactory as sa
from dash import runledger
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
            (work / "infra").mkdir(exist_ok=True)
            (work / "infra" / "security.py").write_text("# tampered\n")
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
        changes = ["M\ttests/test_old.py", "M\tinfra/security.py", "D\ttests/test_x.py", "D\tPROJECT_MAP.md"]
        self.assertEqual(selfimprove.protected_violations(changes, selfimprove.PROTECTED_DIRECTED),
                         ["tests/test_old.py", "infra/security.py", "tests/test_x.py", "PROJECT_MAP.md"])
        self.assertEqual(selfimprove.protected_violations(changes, selfimprove.PROTECTED_DIRECTED, edit_tests=True),
                         ["infra/security.py", "tests/test_x.py", "PROJECT_MAP.md"])


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


class RoutingTests(unittest.TestCase):
    def test_route_uses_installed_models_and_falls_back(self):
        class L:
            installed = {"qwen2.5-coder:14b", "devstral:24b", "qwen2.5-coder:7b"}
            overrides = {}
        r = selfimprove._route(L())
        self.assertEqual((r["light"], r["heavy"]), ("qwen2.5-coder:7b", "devstral:24b"))
        L.installed = {"qwen2.5-coder:14b"}
        r = selfimprove._route(L())
        self.assertEqual((r["light"], r["heavy"]), (None, None))

    def test_coder_for_picks_model_by_job_and_defaults(self):
        def base(*a): return "base"
        base.routes = {"light": "qwen2.5-coder:7b", "heavy": "devstral:24b"}
        base.use = lambda name: (lambda *a: name)
        self.assertEqual(selfimprove._coder_for(base, "heavy")(None, [], ""), "devstral:24b")
        self.assertEqual(selfimprove._coder_for(base, "light")(None, [], ""), "qwen2.5-coder:7b")
        self.assertIs(selfimprove._coder_for(base, "standard"), base)
        plain = lambda *a: 1  # noqa: E731
        self.assertIs(selfimprove._coder_for(plain, "heavy"), plain)

    def test_last_repair_round_uses_heavy_model(self):
        used = []

        def coder(work, files, prompt):
            if not prompt.startswith(("Add tests", "Documentation update")):
                (work / "app.py").write_text("X = 5\n")
        coder.routes = {"light": None, "heavy": "devstral:24b"}
        coder.use = lambda name: (lambda w, f, p: (used.append(name), coder(w, f, p))[1])
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            sa.LLM.mock = lambda tag, *a: json.dumps({"files": ["app.py"]}) if tag == "tool_files" else "{}"
            real = selfimprove._tests_green
            selfimprove._tests_green = lambda w, t: (False, "red")
            try:
                selfimprove.directed("Change X to 5", repo=repo, coder=coder, log=lambda m: None)
            finally:
                sa.LLM.mock = None
                selfimprove._tests_green = real
        self.assertEqual(used, ["devstral:24b"])


class AdaptiveRoutingTests(unittest.TestCase):
    def test_track_record_switches_failing_default_to_heavy(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "self_improve"
            (out / "runs").mkdir(parents=True)
            att = [{"model": "qwen2.5-coder:14b", "ok": False}] * 4 + [{"model": "devstral:24b", "ok": True}] * 2
            (out / "runs" / "a.json").write_text(json.dumps({"proposals": [{"status": "kept", "attempts": att}]}))
            selfimprove.STATE = out

            def coder(*a): pass
            coder.model, coder.routes = "ollama_chat/qwen2.5-coder:14b", {"heavy": "devstral:24b", "light": None}
            self.assertEqual(selfimprove._learned_cls(coder, "standard", 0)[0], "heavy")
            self.assertEqual(selfimprove._learned_cls(coder, "standard", 2)[0], "standard")
            coder.routes = {"heavy": None, "light": None}
            self.assertEqual(selfimprove._learned_cls(coder, "standard", 0)[0], "standard")
            self.assertEqual(runledger.model_stats(out)["devstral:24b"], {"tries": 2, "ok": 2})

    def test_unproven_heavy_model_is_not_used_for_file_count_alone(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "self_improve"
            (out / "runs").mkdir(parents=True)
            att = [{"model": "qwen2.5-coder:14b", "ok": True}] * 3 + [{"model": "devstral:24b", "ok": False}] * 4
            (out / "runs" / "a.json").write_text(json.dumps({"proposals": [{"attempts": att}]}))
            selfimprove.STATE = out

            def coder(*a): pass
            coder.model, coder.routes = "ollama_chat/qwen2.5-coder:14b", {"heavy": "devstral:24b", "light": None}
            self.assertEqual(selfimprove._learned_cls(coder, "heavy", 0)[0], "standard")


class LeanContextTests(unittest.TestCase):
    def test_fit_drops_docs_map_and_over_budget_files(self):
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            (w / "docs").mkdir()
            for name, size in {"a.py": 3000, "big.py": 90000, "small.py": 300, "PROJECT_MAP.md": 100, "docs/x.md": 100, "README.md": 100}.items():
                (w / name).write_text("x" * size)
            got = selfimprove._fit(w, ["PROJECT_MAP.md", "a.py", "docs/x.md", "big.py", "small.py", "missing.py", "README.md"], budget=5000)
            self.assertEqual(got, ["a.py", "small.py", "README.md"])
            self.assertEqual(selfimprove._fit(w, ["big.py", "a.py"], budget=5000), ["big.py"])

    def test_directed_steps_share_one_impact_analysis(self):
        calls = []
        sa.LLM.mock = lambda tag, *a: (calls.append(tag), json.dumps({"requirements": ["one thing", "two thing"]}) if tag == "tool_requirements"
                                       else json.dumps({"files": ["app.py"]}) if tag == "tool_files" else "{}")[1]

        def coder(work, files, prompt):
            if not prompt.startswith(("Add tests", "Documentation update")):
                with open(work / "app.py", "a") as f:
                    f.write("# s\n")
        try:
            with tempfile.TemporaryDirectory() as d:
                repo = make_repo(Path(d))
                real = selfimprove._tests_green
                selfimprove._tests_green = lambda w, t: (True, "ok")
                try:
                    selfimprove.directed("do one thing and two thing", repo=repo, coder=coder, log=lambda m: None)
                finally:
                    selfimprove._tests_green = real
        finally:
            sa.LLM.mock = None
        self.assertEqual(calls.count("tool_impact"), 1)
        self.assertEqual(calls.count("tool_plan_critique"), 0)  # one file, no dependents: no critique round


class NoEditTests(unittest.TestCase):
    def test_no_edit_report_contains_coder_output_and_repair_hint(self):
        prompts = []

        def coder(work, files, prompt):
            prompts.append(prompt)
            if prompt.startswith("Repair round"):
                (work / "app.py").write_text("X = 7\n")
                return "ok"
            return "I would change X to 7 but did not output an edit block"

        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            sa.LLM.mock = lambda tag, *a: json.dumps({"files": ["app.py"]}) if tag == "tool_files" else "{}"
            real = selfimprove._tests_green
            selfimprove._tests_green = lambda w, t: (True, "ok")
            try:
                res = selfimprove.directed("Change X to 7", repo=repo, coder=coder, log=lambda m: None)
            finally:
                sa.LLM.mock = None
                selfimprove._tests_green = real
        rep = next(p for p in prompts if p.startswith("Repair round 1"))
        self.assertIn("did not output an edit block", rep)
        self.assertIn("changed no file", rep)
        self.assertTrue(res["branch"])


class SpeedTests(unittest.TestCase):
    def test_analyse_finds_cpu_offload_ctx_mismatch_and_multi_load(self):
        from infra import perf
        ps = {"models": [{"name": "a:14b", "size": 100, "size_vram": 70, "context_length": 4096}, {"name": "b:7b", "size": 50, "size_vram": 50}]}
        texts = " ".join(f["text"] for f in perf.analyse(ps, {"a:14b": {"parameters": "num_ctx 12288"}}, 16384))
        for needle in ("30% runs on the CPU", "reloads", "loaded at the same time", "num_ctx 12288"):
            self.assertIn(needle, texts)
        self.assertEqual(perf.analyse({"models": []}, {}, 16384)[0]["level"], "ok")
        self.assertEqual(perf.analyse(None, {})[0]["level"], "warn")

    def test_aider_args_pin_context_for_ollama_only(self):
        from infra import perf
        with tempfile.TemporaryDirectory() as d:
            a = perf.aider_args("ollama_chat/x", Path(d))
            self.assertIn("--model-settings-file", a)
            cfg = json.loads((Path(d) / "aider_models.json").read_text())
            self.assertEqual(cfg[0]["extra_params"]["num_ctx"], perf.NUM_CTX)
            self.assertNotIn("--model-settings-file", perf.aider_args("gpt-4o", Path(d)))

    def test_endless_reasoning_is_cut_and_asked_again_directly(self):
        llm = sa.LLM({"limits": {"think_chars": 50}, "models": {}}, lambda m: None)
        self.assertEqual(llm.think_cap, 50)

        class R:
            headers = {"Content-Type": "text/event-stream"}
            def __iter__(self):
                for _ in range(100):
                    yield ('data: ' + json.dumps({"choices": [{"delta": {"reasoning": "hmm " * 10}}]})).encode()
        llm.sched = type("S", (), {"check": lambda s: None, "waiting": False})()
        llm._publish = lambda *a, **k: None
        answer, thinking = llm._read(R(), "t", "planner", "m", 0)
        self.assertEqual(answer, "")
        self.assertLess(len(thinking), 200)
        self.assertTrue(llm.cut_thinking)

    def test_review_runs_while_full_tests_run(self):
        order = []

        def coder(work, files, prompt):
            if not prompt.startswith(("Add tests", "Documentation update")):
                (work / "app.py").write_text("X = 9\n")
        import time as _t
        sa.LLM.mock = lambda tag, *a: json.dumps({"files": ["app.py"]}) if tag == "tool_files" else (order.append("review_start") or _t.sleep(0.3) or order.append("review_end") or json.dumps({"approve": True, "issues": []}) if tag == "tool_review" else "{}")

        def green(w, t):
            order.append("tests_start")
            _t.sleep(0.5)
            order.append("tests_end")
            return True, "ok"
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            real = selfimprove._tests_green
            selfimprove._tests_green = green
            try:
                selfimprove.directed("Change X to 9", repo=repo, coder=coder, log=lambda m: None)
            finally:
                sa.LLM.mock = None
                selfimprove._tests_green = real
        self.assertLess(order.index("review_start"), order.index("tests_end"))
        self.assertLess(order.index("tests_start"), order.index("review_end"))
