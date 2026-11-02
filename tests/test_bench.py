import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench import harness  # noqa: E402
from core.cfa_flow import added_test_names  # noqa: E402
from dash import recommend, replay  # noqa: E402


def good_runner(task, ws):
    repo = ws / "repo" / "app"
    repo.mkdir(parents=True)
    (repo / "__init__.py").write_text("")
    (ws / "repo" / "app" / "text.py").write_text("import re\n\n\ndef slugify(t):\n    return re.sub(r'[^a-z0-9]+', '-', t.lower()).strip('-')\n")
    return {"attempts": 1}


class HarnessTests(unittest.TestCase):
    def test_tasks_file_is_valid(self):
        tasks = harness.load_tasks()
        self.assertGreaterEqual(len(tasks), 5)
        self.assertEqual(len({t["id"] for t in tasks}), len(tasks))
        for t in tasks:
            self.assertTrue(t["prompt"] and t["check"], t["id"])

    def test_suite_scores_pass_and_fail_and_stores_report(self):
        tasks = harness.load_tasks(only=["b01-slugify", "b02-fizzbuzz"])
        with tempfile.TemporaryDirectory() as t:
            rep = harness.run_suite(tasks, good_runner, "a", results_dir=Path(t))
            self.assertEqual([r["ok"] for r in rep["results"]], [True, False])
            self.assertEqual((rep["passed"], rep["tasks"], rep["success_rate"]), (1, 2, 0.5))
            self.assertEqual(harness.load_results(Path(t))[0]["label"], "a")

    def test_crashing_runner_is_a_failed_task(self):
        def boom(task, ws):
            raise RuntimeError("no ollama")
        with tempfile.TemporaryDirectory() as t:
            rep = harness.run_suite(harness.load_tasks(only=["b01-slugify"]), boom, "c", results_dir=Path(t))
        self.assertFalse(rep["results"][0]["ok"])
        self.assertIn("no ollama", rep["results"][0]["why"])

    def test_compare_verdicts(self):
        def rep(oks, secs):
            return {"results": [{"id": f"t{i}", "ok": ok, "seconds": s, "attempts": 1} for i, (ok, s) in enumerate(zip(oks, secs, strict=True))]}
        base = rep([True, False], [10, 10])
        self.assertEqual(harness.compare(base, rep([True, True], [10, 10]))["verdict"], "better")
        self.assertEqual(harness.compare(base, rep([False, True], [10, 10]))["verdict"], "worse")
        self.assertEqual(harness.compare(base, rep([True, False], [10, 10]))["verdict"], "same")
        self.assertEqual(harness.compare(base, rep([True, False], [5, 10]))["verdict"], "better")
        self.assertEqual(harness.compare(base, rep([True, False], [10, 14]))["verdict"], "worse")


class RegressionTests(unittest.TestCase):
    def test_added_test_names(self):
        diff = "+++ b/t.py\n+def test_empty_list():\n+    pass\n-def test_old():\n+x = 1\n"
        self.assertEqual(added_test_names(diff), ["def test_empty_list():"])
        self.assertEqual(added_test_names("+x = 1\n"), [])


class BugfixPolicyTests(unittest.TestCase):
    def test_bug_fix_without_new_test_is_sent_back_until_it_has_one(self):
        import codefactory as cf
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t) / "p"
            (ws / "agent").mkdir(parents=True)
            ag = cf.Agent(ws)
            ag.repo.mkdir(parents=True, exist_ok=True)
            logs, diffs = [], ["+++ b/app.py\n+x = 1\n"]
            ag.git.diff = lambda *a, **k: diffs[0]
            ag.git.changed_files = lambda: ["tests/test_a.py"]
            ag.verify_stage = lambda *a: (True, "")
            ag.handoff = lambda *a, **k: None
            ag.tried = []
            ag.log = logs.append
            ag.code = lambda *a, **k: diffs.__setitem__(0, "+++ b/t.py\n+def test_empty_average():\n+    pass\n")
            item = {"id": "T1", "title": "Fix", "type": "bug"}
            self.assertTrue(ag.debug_loop(item)[0])
            self.assertTrue(any("regression test" in m for m in logs))
            self.assertEqual(item["regression"], ["def test_empty_average():"])


class RecommendTests(unittest.TestCase):
    def rows(self, model, role, think, answer, n=3):
        return [{"model": model, "role": role, "seconds": 10, "thinking": "x" * think, "answer": "y" * answer} for _ in range(n)]

    def test_planner_without_reasoning_is_flagged(self):
        r = recommend.recommend(self.rows("qwen", "planner", 0, 100))
        self.assertIn("reasoning model", r[0]["text"])

    def test_reasoning_planner_is_kept_and_few_calls_are_ignored(self):
        self.assertIn("Keep r1", recommend.recommend(self.rows("r1", "planner", 80, 20))[0]["text"])
        self.assertEqual(recommend.recommend(self.rows("r1", "planner", 80, 20, n=1)), [])

    def test_coder_prefers_less_reasoning(self):
        r = recommend.recommend(self.rows("r1", "coder", 90, 10) + self.rows("qwen", "coder", 0, 100))
        self.assertEqual(r[0]["model"], "qwen")
        self.assertTrue(recommend.coder_hint("deepseek-r1:14b"))
        self.assertFalse(recommend.coder_hint("qwen2.5-coder:14b"))


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "projects" / "demo"
        (self.ws / "agent").mkdir(parents=True)
        (self.ws / "agent" / "backlog.json").write_text(json.dumps([{"id": "T0007", "title": "Search", "description": "d", "acceptance": ["fast"], "status": "blocked", "reason": "glob"}]), "utf-8")
        (self.ws / "agent" / "config.json").write_text(json.dumps({"coder_model": "ollama_chat/a", "models": {"planner": "a"}}), "utf-8")
        (self.ws / "agent" / "STOP").write_text("x")

    def test_prepare_copies_applies_model_and_keeps_original(self):
        info = replay.prepare(self.ws, "T0007", "qwen2.5-coder:7b", "use std only")
        cfg = json.loads((info["dest"] / "agent" / "config.json").read_text("utf-8"))
        self.assertEqual(cfg["coder_model"], "ollama_chat/qwen2.5-coder:7b")
        self.assertFalse((info["dest"] / "agent" / "STOP").exists())
        self.assertIn("use std only", info["text"])
        self.assertIn("blocked before: glob", info["text"])
        self.assertEqual(json.loads((self.ws / "agent" / "config.json").read_text("utf-8"))["coder_model"], "ollama_chat/a")
        self.assertEqual(info["dest"].parent.name, ".replay")

    def test_bad_input_is_rejected(self):
        for args in (("../x",), ("T9999",), ("T0007", "bad model; rm")):
            with self.assertRaises(ValueError):
                replay.prepare(self.ws, *args)

    def test_record_and_history(self):
        replay.record(self.ws, "T0007", {"dest": "d", "model": "m"}, "done", 12.4)
        self.assertEqual(replay.history(self.ws / "agent")[0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
