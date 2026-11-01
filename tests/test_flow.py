import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cfa_flow import FEATURE_DEFAULTS, FlowMixin, removed_tests, split_steps  # noqa: E402


class Fake(FlowMixin):
    def __init__(self, feats=None):
        self.cfg = {"features": dict(feats or {}), "routing": {"complex_files": 4}}
        self.logs, self.calls = [], []

    def log(self, m):
        self.logs.append(m)

    def metric(self, **kw):
        pass

    class llm:  # noqa: N801
        answer = {"cause": "c", "change": "use the helper"}

        @classmethod
        def json(cls, *a):
            return cls.answer


class FlowTests(unittest.TestCase):
    def test_removed_tests(self):
        d = "-def test_a():\n-    pass\n+def test_b():\n+    pass\n+def test_c():\n+    pass\n"
        self.assertEqual(removed_tests(d), -1)
        self.assertEqual(removed_tests("-def test_a():\n-def test_z():\n+def helper():\n"), 2)
        self.assertEqual(removed_tests("--- a/x\n+++ b/x\n-it('x', () => {})\n"), 1)

    def test_split_steps(self):
        self.assertEqual(split_steps(["a"]), [["a"]])
        self.assertEqual(split_steps(list("abcde")), [["a", "b"], ["c", "d"], ["e"]])
        self.assertEqual(split_steps(list("ab")), [["a"], ["b"]])

    def test_wants_auto(self):
        f = Fake()
        small = {"files_to_change": ["a.py"], "new_files": [], "steps": ["s"], "test_files": ["tests/test_a.py"]}
        big = {"files_to_change": list("abcd"), "new_files": [], "steps": ["s"] * 5}
        self.assertTrue(f.wants("test_first", {"type": "feature"}, small))
        self.assertFalse(f.wants("test_first", {"type": "docs"}, small))
        self.assertFalse(f.wants("test_first", {"type": "feature"}, big))  # no test file named by the plan
        self.assertFalse(f.wants("step_by_step", {"type": "feature"}, small))
        self.assertTrue(f.wants("step_by_step", {"type": "feature"}, big))
        self.assertFalse(Fake({"step_by_step": "off"}).wants("step_by_step", {"type": "feature"}, big))
        self.assertTrue(Fake({"step_by_step": "on"}).wants("step_by_step", {"type": "bug"}, small))

    def test_test_paths_sanitised(self):
        paths = Fake().test_paths({"test_files": ["tests/test_a.py", "../evil_test.py", "/etc/test", "src/app.py", "tests/test_a.py"]})
        self.assertEqual(paths, ["tests/test_a.py"])

    def test_retry_strategy(self):
        prev = ["Attempt 1: failed"]
        Fake().retry_strategy({"title": "t", "description": "d"}, prev)
        self.assertIn("use the helper", prev[-1])
        none: list[str] = []
        Fake().retry_strategy({"title": "t", "description": "d"}, none)
        self.assertEqual(none, [])
        off = ["x"]
        Fake({"retry_replan": False}).retry_strategy({"title": "t", "description": "d"}, off)
        self.assertEqual(off, ["x"])

    def test_defaults_present(self):
        from core.cfutil import DEFAULT_CONFIG
        for k, v in FEATURE_DEFAULTS.items():
            self.assertEqual(DEFAULT_CONFIG["features"][k], v)


if __name__ == "__main__":
    unittest.main()
