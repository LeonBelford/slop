import tempfile
import unittest

from core.cfa_spike import SpikeMixin


class Agent(SpikeMixin):
    cur_attempt = 1

    def feat(self, n):
        return True

    def __init__(self, d, ctx=0):
        self.llm = type("L", (), {"server_ctx": ctx})()
        self.logs, self.splits = [], []

    def log(self, m):
        self.logs.append(m)

    def split_task(self, item, previous):
        self.splits.append(previous)
        return True


class SpikeSplitTests(unittest.TestCase):
    def test_spike_is_skipped_on_a_small_window_even_for_risky_tasks(self):
        with tempfile.TemporaryDirectory() as t:
            a = Agent(t, ctx=4096)
            self.assertFalse(a.spike_due({"id": "T1", "revives": 1}, {"risks": list("abcd")}))
            self.assertIn("blind", a.logs[-1])
            self.assertTrue(Agent(t, ctx=16384).spike_due({"revives": 1}, {}))
            self.assertTrue(Agent(t, ctx=0).spike_due({"revives": 1}, {}))

    def test_failed_spike_splits_at_once_but_inconclusive_or_sliced_tasks_do_not(self):
        with tempfile.TemporaryDirectory() as t:
            a = Agent(t)
            self.assertFalse(a.spike_failed_split({"id": "T1"}, "inconclusive"))
            self.assertFalse(a.spike_failed_split({"id": "T1", "split_depth": 1}, "approach did not hold: x"))
            self.assertEqual(a.splits, [])
            self.assertTrue(a.spike_failed_split({"id": "T1"}, "approach did not hold: boom"))
            self.assertIn("spike", a.splits[0][0])
            self.assertTrue(a._spike_split)
