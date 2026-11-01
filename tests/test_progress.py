"""Tests for core/progress.py: the estimated % and per-task sub-step log shown in the UI."""
import unittest

from core import progress


class StepPctTests(unittest.TestCase):
    def test_none_step_has_no_percentage(self):
        self.assertIsNone(progress.step_pct(progress.AGENT_PIPELINE, None))

    def test_unknown_step_has_no_percentage(self):
        self.assertIsNone(progress.step_pct(progress.AGENT_PIPELINE, "nonsense"))

    def test_first_step_is_a_small_nonzero_percentage(self):
        p = progress.step_pct(progress.AGENT_PIPELINE, "plan")
        self.assertGreater(p, 0)
        self.assertLess(p, 15)

    def test_later_steps_score_higher(self):
        a = progress.step_pct(progress.AGENT_PIPELINE, "implement")
        b = progress.step_pct(progress.AGENT_PIPELINE, "review")
        self.assertLess(a, b)

    def test_merge_is_almost_complete(self):
        p = progress.step_pct(progress.AGENT_PIPELINE, "merge")
        self.assertGreater(p, 90)
        self.assertLess(p, 100)


class TrackerTests(unittest.TestCase):
    def test_progress_is_monotonic_even_if_a_step_repeats(self):
        t = progress.Tracker()
        pcts = []
        for step in ("plan", "implement", "build", "build", "build", "review"):
            pct, _, _ = t.update(progress.AGENT_PIPELINE, ("T1", 1), f"phase {step}", step)
            pcts.append(pct)
        self.assertEqual(pcts, sorted(pcts))

    def test_progress_resets_on_a_new_task_or_attempt(self):
        t = progress.Tracker()
        t.update(progress.AGENT_PIPELINE, ("T1", 1), "merging", "merge")
        pct, _, _ = t.update(progress.AGENT_PIPELINE, ("T2", 1), "planning", "plan")
        self.assertLess(pct, 20)

    def test_done_flag_forces_one_hundred_percent(self):
        t = progress.Tracker()
        t.update(progress.AGENT_PIPELINE, ("T1", 1), "planning", "plan")
        pct, _, _ = t.update(progress.AGENT_PIPELINE, ("T1", 1), "merged", None, done=True)
        self.assertEqual(pct, 100)

    def test_keeps_a_short_rolling_log_of_substeps(self):
        t = progress.Tracker()
        for i in range(30):
            t.update(progress.AGENT_PIPELINE, ("T1", 1), f"round {i}", "build", detail=f"d{i}")
        self.assertLessEqual(len(t.steps), 20)
        self.assertEqual(t.steps[-1]["detail"], "d29")

    def test_closed_step_durations_accumulate_across_repeats(self):
        t = progress.Tracker()
        t.update(progress.AGENT_PIPELINE, ("T1", 1), "planning", "plan")
        t.last_time -= 5  # simulate 5s spent planning
        t.update(progress.AGENT_PIPELINE, ("T1", 1), "implementing", "implement")
        t.last_time -= 3
        t.update(progress.AGENT_PIPELINE, ("T1", 1), "building", "build")
        t.last_time -= 2
        _, _, durations = t.update(progress.AGENT_PIPELINE, ("T1", 1), "debug round 2", "build")
        self.assertAlmostEqual(durations["plan"], 5, delta=0.5)
        self.assertAlmostEqual(durations["implement"], 3, delta=0.5)
        self.assertAlmostEqual(durations["build"], 2, delta=0.5)
        self.assertNotIn("merge", durations)


if __name__ == "__main__":
    unittest.main()
