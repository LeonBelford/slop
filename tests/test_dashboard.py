import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codefactory as sa  # noqa: E402
import webui  # noqa: E402


class LiveRateTests(unittest.TestCase):
    def test_idle_without_stream(self):
        self.assertEqual(webui.Service.live_rate(None)["tps"], 0.0)
        self.assertFalse(webui.Service.live_rate(None)["active"])

    def test_tokens_per_second_while_streaming(self):
        r = webui.Service.live_rate({"tokens": 100, "chars": 400, "t0": 1000.0, "updated": 1010.0, "role": "coder"}, now_t=1011.0)
        self.assertEqual(r["tps"], 10.0)
        self.assertTrue(r["streaming"])

    def test_rate_drops_to_zero_when_stream_is_quiet(self):
        r = webui.Service.live_rate({"tokens": 100, "chars": 400, "t0": 1000.0, "updated": 1010.0}, now_t=1020.0)
        self.assertEqual(r["tps"], 0.0)
        self.assertTrue(r["active"])


class DashboardTests(unittest.TestCase):
    def test_dashboard_reports_running_agent_and_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "demo"
            (ws / "agent").mkdir(parents=True)
            (ws / "agent" / "GOAL.md").write_text("g")
            sa.write_json(ws / "agent" / "metrics.json", {"calls": 3, "tokens": 900, "chars": 3600, "day_calls": 1, "day_tokens": 300, "day_chars": 1200})
            svc = webui.Service(Path(tmp))
            webui.Service._gpu = (time.time(), None)
            svc.loaded_models = staticmethod(lambda: [])
            d = svc.dashboard()
            self.assertEqual(d["agents_running"], 0)
            self.assertEqual(d["totals"]["tokens"], 900)
            self.assertEqual(d["projects"][0]["name"], "demo")
            json.dumps(d)


if __name__ == "__main__":
    unittest.main()


class CompactTests(unittest.TestCase):
    def test_old_thoughts_are_archived_compressed_and_seq_survives(self):
        import gzip
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "thoughts.jsonl"
            f.write_text("".join(json.dumps({"n": i, "prompt": "p" * 5000, "thinking": "t", "answer": "a"}) + "\n" for i in range(1, 401)))
            self.assertTrue(sa.compact_thoughts(f, keep=100))
            self.assertEqual(len(f.read_text().splitlines()), 100)
            self.assertEqual(sa.last_seq(f), 400)
            arch = list((Path(tmp) / "thoughts-archive").glob("*.gz"))
            rows = [json.loads(x) for x in gzip.open(arch[0], "rt").read().splitlines()]
            self.assertEqual((len(rows), len(rows[0]["prompt"])), (300, 1500))

    def test_aider_token_lines(self):
        self.assertEqual(sa.aider_tokens("Tokens: 1.2k sent, 345 received.\nTokens: 8,432 sent, 1,023 received. Cost"), (9632, 1368))
