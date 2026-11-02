import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dash import pdextra  # noqa: E402


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")


class PdExtraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.a = self.ws / "agent"
        self.addCleanup(self.tmp.cleanup)

    def test_timeline_lays_tasks_out_one_after_another(self):
        rows = pdextra.timeline([{"id": "T1", "title": "A", "status": "done", "runtime_s": 60, "attempts": 1},
                                 {"id": "T2", "title": "B", "status": "pending"}, {"id": "T3", "title": "C", "status": "done", "runtime_s": 30}])
        self.assertEqual([(r["id"], r["start"], r["seconds"]) for r in rows], [("T1", 0, 60), ("T3", 60, 30)])

    def test_causes_group_failed_attempts_by_class(self):
        write(self.a / "metrics.jsonl", [{"kind": "attempt_failed", "item": "T1", "title": "A", "cls": "build"},
                                         {"kind": "attempt_failed", "item": "T2", "title": "B", "cls": "build"}, {"kind": "attempt_failed", "item": "T1", "cls": "test"}])
        c = pdextra.causes(self.a)
        self.assertEqual((c[0]["cls"], c[0]["n"], [t["id"] for t in c[0]["tasks"]]), ("build", 2, ["T1", "T2"]))

    def test_step_time_counts_gaps_but_not_long_pauses(self):
        write(self.a / "events.jsonl", [{"time": "2026-11-02 10:00:00", "phase": "p", "step": "plan"}, {"time": "2026-11-02 10:01:00", "phase": "i", "step": "implement"},
                                        {"time": "2026-11-02 10:03:00", "phase": "b", "step": "build"}, {"time": "2026-11-02 14:00:00", "phase": "x", "step": "plan"}])
        self.assertEqual(pdextra.step_time(self.a), {"implement": 120, "plan": 60})

    def test_task_detail_and_id_validation(self):
        write(self.a / "backlog.json", [])
        (self.a / "backlog.json").write_text(json.dumps([{"id": "T0001", "title": "A", "type": "feature", "status": "blocked", "reason": "why", "notes": ["n"]}]), "utf-8")
        run = self.a / "runs" / "0001-T0001"
        run.mkdir(parents=True)
        (run / "journal.md").write_text("- [x] did it\nother\n", "utf-8")
        (run / "plan.json").write_text(json.dumps({"understanding": "u", "steps": ["s"]}), "utf-8")
        d = pdextra.task_detail(self.ws, "T0001")
        self.assertEqual((d["item"]["reason"], d["journal"], d["plan"]["understanding"]), ("why", ["- [x] did it"], "u"))
        self.assertFalse(pdextra.task_detail(self.ws, "../../etc")["found"])
        self.assertFalse(pdextra.task_detail(self.ws, "T9999")["found"])


if __name__ == "__main__":
    unittest.main()
