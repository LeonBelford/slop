import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dash import projectdash  # noqa: E402


def it(i, status="pending", **kw):
    return {"id": i, "title": f"Task {i}", "status": status, "type": "feature", "priority": 5, **kw}


class QueueTests(unittest.TestCase):
    def test_queue_follows_pick_order_and_marks_waiting_tasks(self):
        items = [it("a", layer=1), it("b", layer=0), it("c", layer=0, dyn=1.5), it("d", depends_on=["a"], layer=1), it("u", source="user", layer=3)]
        q = projectdash.queue(items)
        self.assertEqual([r["id"] for r in q], ["u", "c", "b", "a", "d"])
        self.assertEqual(q[-1]["waiting_for"], ["a"])

    def test_layers_count_done_and_skip_cancelled(self):
        rows = projectdash.layers([it("a", "done", layer=0), it("b", layer=0), it("c", "cancelled", layer=0), it("d", "blocked")],
                                  {"layers": [{"name": "Core"}]})
        self.assertEqual(rows[0], {"layer": 0, "name": "Core", "done": 1, "total": 2, "blocked": 0})
        self.assertEqual(rows[1]["blocked"], 1)


class BuildTests(unittest.TestCase):
    def test_build_on_a_fresh_and_a_busy_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "p"
            a = ws / "agent"
            (a / "runs" / "0001-T1").mkdir(parents=True)
            (ws / "repo").mkdir()
            self.assertEqual(projectdash.build(ws, False)["status"]["iteration"], 0)
            (a / "state.json").write_text(json.dumps({"iteration": 4, "merged": 3, "elapsed": 7200, "consecutive_failures": 3,
                                                      "outcomes": [True, False, True], "triage_at": 1}))
            (a / "backlog.json").write_text(json.dumps([it("T1", "blocked", notes=["gave up"]), it("T2", dyn=0.75)]))
            (a / "activity.json").write_text(json.dumps({"phase": "Building", "step": "build", "task": "T2 Task T2", "attempt": 2, "since": 1}))
            (a / "runs" / "0001-T1" / "journal.md").write_text("# Task journal\n- [plan] x\n- [debug] y\n")
            s = projectdash.build(ws, True)
            self.assertEqual((s["status"]["recent_ok"], s["status"]["recent_total"]), (2, 3))
            self.assertEqual(s["current"]["title"], "Task T2")
            self.assertEqual(s["triage"]["merges_since_layout"], 2)
            self.assertEqual(s["triage"]["adjusted"][0]["id"], "T2")
            self.assertEqual(s["blocked"][0]["note"], "gave up")
            self.assertEqual(len(s["journal"]), 2)
            self.assertTrue(any("consecutive failures" in f["text"] for f in s["findings"]))
            json.dumps(s)


if __name__ == "__main__":
    unittest.main()
