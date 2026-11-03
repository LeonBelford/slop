import unittest
from pathlib import Path

from core.cfa_plan import PlanMixin
from dash import pdextra


class BL:
    def __init__(self, items):
        self.items = items

    def set(self, item, **kw):
        item.update(kw)


class Stub(PlanMixin):
    def __init__(self, items, on=True):
        self.backlog, self.on = BL(items), on

    def feat(self, name):
        return self.on


def mk():
    return [{"id": "T1", "status": "blocked", "notes": []},
            {"id": "T2", "status": "pending", "depends_on": ["T1"]},
            {"id": "T3", "status": "blocked", "notes": []},
            {"id": "T4", "status": "blocked", "source": "user", "notes": []}]


class Retire(unittest.TestCase):
    def run_it(self, stats, on=True, requeue=False, assign=("T3",)):
        items = mk()
        s = Stub(items, on)
        by = {i["id"]: i for i in items}
        n = s.retire_blocked(by, {"assign": {k: {} for k in assign}}, set(by), None, requeue, stats)
        return n, by

    def test_retires_replaced_blocked_only(self):
        n, by = self.run_it({"new": 1, "merged": 0, "split": 0})
        self.assertEqual(n, 1)
        self.assertEqual(by["T1"]["status"], "cancelled")
        self.assertTrue(by["T1"]["retired"])
        self.assertNotIn("depends_on", by["T2"])
        self.assertEqual(by["T3"]["status"], "blocked")  # still in the plan
        self.assertEqual(by["T4"]["status"], "blocked")  # user task stays

    def test_noop_cases(self):
        self.assertEqual(self.run_it({"new": 0, "merged": 0, "split": 0})[0], 0)
        self.assertEqual(self.run_it({"new": 1, "merged": 0, "split": 0}, on=False)[0], 0)
        self.assertEqual(self.run_it({"new": 1, "merged": 0, "split": 0}, requeue=True)[0], 0)

    def test_dashboard_lists_removed(self):
        rows = pdextra.removed([{"id": "A", "status": "cancelled", "retired": True, "title": "x"}, {"id": "B", "status": "done"}])
        self.assertEqual([r["id"] for r in rows], ["A"])


if __name__ == "__main__":
    unittest.main()
