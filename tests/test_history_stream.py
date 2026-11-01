import http.client
import json
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import webui  # noqa: E402
from core import cfutil  # noqa: E402
from dash import history  # noqa: E402


def agent_dir(tmp: str) -> Path:
    a = Path(tmp) / "demo" / "agent"
    a.mkdir(parents=True)
    (a / "GOAL.md").write_text("g")
    today = date.today().isoformat()
    (a / "metrics.json").write_text(json.dumps({"gen_seconds": 7200, "days": {today: {"tokens": 500, "calls": 4, "gen_seconds": 3600, "wall_seconds": 4000}}}))
    rows = [{"time": today + " 10:00:00", "item": "T1", "attempts": 2}, {"time": today + " 11:00:00", "item": "T2", "attempts": 1},
            {"time": today + " 11:30:00", "kind": "attempt_failed", "cls": "test_fail"}, {"time": today + " 12:00:00", "kind": "flaky_test"}]
    (a / "metrics.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    return a


class HistoryTests(unittest.TestCase):
    def test_daily_and_quality(self):
        with tempfile.TemporaryDirectory() as t:
            a = agent_dir(t)
            rows = history.daily(a, 7)
            self.assertEqual(len(rows), 7)
            today = rows[-1]
            self.assertEqual((today["tokens"], today["done"], today["failed"], today["flaky"], today["gpu_hours"]), (500, 2, 1, 1, 1.0))
            q = history.quality(a)
            self.assertEqual((q["done"], q["failed"], q["fail_rate"], q["retry_rate"], q["gpu_hours"]), (2, 1, 0.33, 0.5, 2.0))
            self.assertEqual(q["by_class"], {"test_fail": 1})

    def test_empty_project_has_no_rates(self):
        with tempfile.TemporaryDirectory() as t:
            q = history.quality(Path(t))
            self.assertIsNone(q["fail_rate"])
            self.assertEqual(len(history.daily(Path(t), 3)), 3)

    def test_merge_sums_days(self):
        a = [{"day": "d", "tokens": 1, "gpu_hours": 0.5}]
        b = [{"day": "d", "tokens": 2, "gpu_hours": 0.25}]
        self.assertEqual(history.merge([a, b]), [{"day": "d", "tokens": 3, "gpu_hours": 0.75}])

    def test_rotate_keeps_newest_lines(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "x.jsonl"
            f.write_text("".join(f"{i}\n" for i in range(1000)))
            cfutil.rotate_jsonl(f, max_bytes=100, keep=10)
            self.assertEqual(f.read_text().split(), [str(i) for i in range(990, 1000)])


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent_dir(self.tmp.name)
        self.svc = webui.Service(Path(self.tmp.name))
        self.svc.loaded_models = staticmethod(lambda: [])
        webui.Service._gpu = (9e12, None)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(self.svc, 8765, None))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def conn(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        c.request("GET", path, headers={"Host": "127.0.0.1:8765"})
        return c, c.getresponse()

    def test_history_endpoint(self):
        _, r = self.conn("/api/history")
        d = json.loads(r.read())
        self.assertEqual(len(d["projects"]["demo"]), 30)
        self.assertEqual(d["total"][-1]["tokens"], 500)

    def test_dashboard_snapshot_is_cached(self):
        a = self.svc.dashboard()
        self.assertIs(a, self.svc.dashboard())
        self.assertIsNot(a, self.svc.dashboard(max_age=0))

    def test_sse_sends_the_snapshot_first(self):
        c, r = self.conn("/api/dashboard/stream")
        self.assertEqual(r.status, 200)
        self.assertIn("text/event-stream", r.getheader("Content-Type"))
        line = r.fp.readline().decode()
        self.assertTrue(line.startswith("data: "))
        self.assertEqual(json.loads(line[6:])["projects"][0]["name"], "demo")
        c.close()

    def test_static_files_are_whitelisted(self):
        self.assertEqual(self.conn("/static/..%2fwebui.py")[1].status, 404)
        self.assertEqual(self.conn("/static/missing.js")[1].status, 404)


if __name__ == "__main__":
    unittest.main()
