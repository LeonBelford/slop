import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codefactory as cf  # noqa: E402


def agent(tmp, mode, rounds=3, **limits):
    cfg = json.loads(json.dumps(cf.DEFAULT_CONFIG))
    cfg["run"] = {"mode": mode, "rounds": rounds}
    cfg["limits"].update(limits)
    a = cf.Agent(Path(tmp), cfg)
    a.log = lambda m: None
    return a


class RunModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_endless_ignores_iteration_and_hour_limits(self):
        a = agent(self.tmp.name, "endless", max_iterations=1, max_hours=1)
        a.state["iteration"], a.state["elapsed"] = 99, 10 ** 9
        self.assertIsNone(a.should_stop(0.0))
        b = agent(self.tmp.name, "default", max_iterations=1)
        b.state["iteration"] = 99
        self.assertEqual(b.should_stop(0.0), "max_iterations reached")

    def test_endless_still_stops_on_stop_file_and_failures(self):
        a = agent(self.tmp.name, "endless")
        a.state["consecutive_failures"] = 99
        self.assertIn("failures", a.should_stop(0.0))
        (a.dir / "STOP").write_text("x")
        self.assertEqual(a.should_stop(0.0), "STOP file found")

    def test_fixed_rounds_stop_after_n_idea_rounds(self):
        a = agent(self.tmp.name, "rounds", rounds=2)
        calls = []
        for n in ("health_check", "sync_ideas", "sync_sources", "schedule_hygiene", "save_state"):
            setattr(a, n, lambda *x, **k: None)
        a.reflect_due = lambda: False
        a.git.hard_reset = lambda: None
        a.backlog.next = lambda *a_, **k_: None
        a.refill = lambda: (calls.append(1), a.state.__setitem__("rounds_run", a.state.get("rounds_run", 0) + 1), True)[-1]
        a._run_loop()
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
