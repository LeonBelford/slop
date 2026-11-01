import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cfllm import LLM  # noqa: E402
from dash import throughput  # noqa: E402


class ThroughputTests(unittest.TestCase):
    def test_perf_rates(self):
        p = throughput.perf({"calls": 4, "tokens": 600, "words": 450, "gen_seconds": 30, "wall_seconds": 120})
        self.assertEqual((p["tps"], p["wps"], p["tokens_per_min"], p["gpu_share"], p["avg_call_s"]), (20.0, 15.0, 300, 0.25, 7.5))

    def test_perf_empty(self):
        p = throughput.perf({})
        self.assertIsNone(p["tps"])
        self.assertIsNone(p["tokens_per_min"])

    def test_record_writes_words_and_wall(self):
        with tempfile.TemporaryDirectory() as d:
            llm = LLM({"limits": {}}, lambda *_: None)
            llm.thoughts = Path(d) / "thoughts.jsonl"
            llm.record("t", "planner", "m", "q", "one two three", "four five", 5.0)
            m = __import__("json").loads((Path(d) / "metrics.json").read_text())
            self.assertEqual(m["words"], 5)
            self.assertGreaterEqual(m["wall_seconds"], 5)


if __name__ == "__main__":
    unittest.main()
