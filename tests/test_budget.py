import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import failures  # noqa: E402
from core.cfa_budget import adaptive_steps, attempt_over, real_paths, should_cut  # noqa: E402


class BudgetTests(unittest.TestCase):
    def test_cheap_failure_is_retried_expensive_one_is_cut(self):
        self.assertFalse(should_cut(6 * 60, 6 * 60))
        self.assertTrue(should_cut(20 * 60, 20 * 60))

    def test_attempt_keeps_going_while_it_progresses(self):
        self.assertTrue(attempt_over(27 * 60, stuck=True))
        self.assertFalse(attempt_over(27 * 60, stuck=False))
        self.assertTrue(attempt_over(36 * 60, stuck=False))
        self.assertFalse(attempt_over(10 * 60, stuck=True))

    def test_plan_size_follows_the_real_pace(self):
        self.assertEqual(adaptive_steps(4, [50 * 60] * 5), 3)
        self.assertEqual(adaptive_steps(4, [5 * 60] * 5), 5)
        self.assertEqual(adaptive_steps(4, [20 * 60] * 5), 4)
        self.assertEqual(adaptive_steps(4, [50 * 60]), 4)

    def test_placeholder_paths_are_dropped(self):
        self.assertEqual(real_paths(["path/to/filename.js", "src/app.py", "<file>", "tests/test_a.py"]), ["src/app.py", "tests/test_a.py"])

    def test_real_files_with_common_names_are_kept(self):
        self.assertEqual(real_paths(["src/foo.py", "bar.py", "examples/a.py"]), ["src/foo.py", "bar.py", "examples/a.py"])

    def test_out_of_time_only_inside_an_attempt_and_with_the_feature_on(self):
        import time
        from types import SimpleNamespace
        from core.cfa_budget import BudgetMixin

        class A(BudgetMixin):
            cfg = {"features": {}}
            feat = staticmethod(lambda n: True)
        a = A()
        self.assertFalse(a.out_of_time(True))
        a._attempt_t0 = time.time() - 40 * 60
        self.assertTrue(a.out_of_time(False))
        a._attempt_t0 = time.time() - 5 * 60
        self.assertFalse(a.out_of_time(True))
        a.backlog = SimpleNamespace(items=[{"status": "done", "runtime_s": 3000}] * 4)
        self.assertEqual(a.plan_step_limit(4), 3)

    def test_budget_failure_has_its_own_class(self):
        self.assertEqual(failures.classify("[budget] The attempt ran out of its time budget\n[test] FAILED"), "too_slow")
        self.assertTrue(failures.advice("too_slow"))


if __name__ == "__main__":
    unittest.main()
