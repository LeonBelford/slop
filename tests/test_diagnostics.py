import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import diagnostics as dg  # noqa: E402
import estimate  # noqa: E402
import webui  # noqa: E402


class FigureTests(unittest.TestCase):
    def test_flow_figures_and_causes(self):
        backlog = [{"status": "done", "attempts": 1}, {"status": "done", "attempts": 3}, {"status": "blocked"}, {"status": "pending"}]
        metrics = [{"item": "a", "review_score": 8}, {"item": "b", "review_score": 6},
                   {"kind": "attempt_failed", "why": "unit tests failed: AssertionError"}, {"kind": "attempt_failed", "why": "weird"}]
        f = dg.flow(backlog, metrics, 2.0)
        self.assertEqual((f["done"], f["blocked"], f["pending"], f["total"]), (2, 1, 1, 4))
        self.assertEqual((f["first_try_rate"], f["avg_attempts"], f["avg_review_score"], f["tasks_per_hour"]), (0.5, 2.0, 7.0, 1.0))
        self.assertEqual(f["fail_causes"], {"tests": 1, "other": 1})
        self.assertIsNone(dg.flow([], [], 0)["first_try_rate"])

    def test_model_time_and_reasoning_share(self):
        rows = [{"role": "planner", "tag": "impact", "model": "m", "seconds": 100, "thinking": "x" * 300, "answer": "y" * 100},
                {"role": "coder", "tag": "code", "model": "m", "seconds": 20, "thinking": "", "answer": "z"}]
        m = dg.models(rows)
        self.assertEqual((m["calls"], m["total_s"], m["avg_call_s"], m["reasoning_share"]), (2, 120, 60.0, 0.75))
        self.assertEqual(m["slowest"][0]["tag"], "planner/impact")

    def test_health_detects_stall_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d)
            (a / "agent.log").write_text("ok\nError: boom\nall fine\n")
            h = dg.health(a, True, now=time.time() + 3600)
            self.assertTrue(h["stalled"])
            self.assertEqual(h["log_errors"], 1)
            self.assertFalse(dg.health(a, False, now=time.time() + 3600)["stalled"])

    def test_findings_are_sorted_and_actionable(self):
        p = {"name": "demo", "flow": {"blocked": 2, "first_try_rate": 0.2, "done": 5}, "health": {"stalled": True, "idle_s": 1200},
             "models": {"reasoning_share": 0.9, "calls": 10, "slowest": [{"tag": "planner/x", "avg_s": 200, "calls": 3}]}}
        out = dg.findings([p], {"disk_free_gb": 2, "ram_free_mb": 500, "ollama": [{"level": "warn", "text": "cpu offload"}]})
        self.assertEqual(out[0]["level"], "bad")
        texts = " ".join(f["text"] for f in out)
        for needle in ("no activity", "blocked", "first try", "reasoning", "slowest", "disk", "RAM", "cpu offload"):
            self.assertIn(needle, texts)
        self.assertEqual([f["level"] for f in out], sorted([f["level"] for f in out], key=["bad", "warn", "info"].index))

    def test_endpoint_returns_project_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "demo"
            (ws / "agent").mkdir(parents=True)
            (ws / "agent" / "GOAL.md").write_text("g")
            (ws / "agent" / "backlog.json").write_text(json.dumps([{"status": "done", "attempts": 1}]))
            svc = webui.Service(Path(tmp))
            webui.Service._oll = (time.time(), [])
            d = svc.diagnostics()
            self.assertEqual(d["projects"][0]["flow"]["done"], 1)
            self.assertIn("disk_free_gb", d["system"])
            self.assertIn("findings", d)


class EstimateTests(unittest.TestCase):
    def rows(self, d):
        p = Path(d) / "durations.jsonl"
        for t, ty, s in [("Add login form", "feature", 600), ("Add login validation", "feature", 700), ("Fix login redirect", "bug", 300),
                         ("Export report as csv", "feature", 1800), ("Export report as pdf", "feature", 2000)]:
            estimate.record(p, t, s, ty)
        return estimate.load(p)

    def test_similar_tasks_drive_the_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            rows = self.rows(d)
            e = estimate.estimate(rows, "Add login throttling", "feature")
            self.assertEqual(e["basis"], "similar")
            self.assertLess(e["seconds"], 800)
            e = estimate.estimate(rows, "Export report as xml", "feature")
            self.assertGreater(e["seconds"], 1700)

    def test_fallbacks_and_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            rows = self.rows(d)
            self.assertEqual(estimate.estimate(rows, "Zebra quantum", "feature")["basis"], "type")
            self.assertEqual(estimate.estimate(rows, "Zebra quantum", "refactor")["basis"], "all")
            self.assertEqual(estimate.estimate([], "x y z", "feature")["basis"], "none")

    def test_forecast_queue_and_running_task(self):
        with tempfile.TemporaryDirectory() as d:
            rows = self.rows(d)
            backlog = [{"id": "7", "title": "Add login throttling", "type": "feature", "status": "in_progress"},
                       {"id": "8", "title": "Export report as xml", "type": "feature", "status": "pending"}, {"id": "1", "status": "done"}]
            f = estimate.forecast(rows, backlog, {"task": "7 Add login throttling", "since": time.time() - 60})
            self.assertEqual((f["open"], f["known"]), (2, 2))
            self.assertGreater(f["total_s"], 2000)
            self.assertIn(f["running"]["elapsed_s"], range(59, 63))
            self.assertFalse(f["running"]["overdue"])

    def test_write_access_check_and_fix(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(estimate.write_problems([Path(d) / "new" / "sub"]), [])
            ro = Path(d) / "ro"
            ro.mkdir()
            ro.chmod(0o500)
            problems = estimate.write_problems([ro], fix=False)
            if os.geteuid() != 0:
                self.assertTrue(problems)
                self.assertEqual(estimate.write_problems([ro], fix=True), [])

    def test_project_diagnostics_include_forecast(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "p"
            (ws / "agent").mkdir(parents=True)
            (ws / "agent" / "backlog.json").write_text(json.dumps([{"id": "1", "title": "Add login form", "type": "feature", "status": "pending"}]))
            p = dg.project(ws, False, 1.0, with_repo=False)
            self.assertEqual(p["forecast"]["open"], 1)
            self.assertEqual(p["write_problems"], [])


if __name__ == "__main__":
    unittest.main()
