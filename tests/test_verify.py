import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import cfa_verify as v  # noqa: E402

DIFF = """diff --git a/app/calc.py b/app/calc.py
--- a/app/calc.py
+++ b/app/calc.py
@@ -1,3 +1,5 @@
 def add(a, b):
-    return a - b
+    if a > b:
+        return a + b
+    return b
 x = 1
"""


class HelperTests(unittest.TestCase):
    def test_added_lines(self):
        self.assertEqual(v.added_lines(DIFF), {"app/calc.py": [2, 3, 4]})

    def test_targeted_cmd_for_pytest_and_unittest(self):
        self.assertEqual(v.targeted_cmd(".venv/bin/python -m pytest -q -x", ["tests/test_a.py"]), ".venv/bin/python -m pytest -q -x tests/test_a.py")
        self.assertEqual(v.targeted_cmd("python3 -m unittest discover -s tests -q", ["tests/test_a.py"]), "python3 -m unittest -q tests.test_a")
        self.assertIsNone(v.targeted_cmd("go test ./...", ["a_test.go"]))
        self.assertIsNone(v.targeted_cmd("python3 -m pytest", []))

    def test_related_tests_by_name_import_and_change(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / "app").mkdir()
            (r / "tests").mkdir()
            (r / "app" / "calc.py").write_text("x=1")
            (r / "app" / "util.py").write_text("x=1")
            (r / "tests" / "test_calc.py").write_text("pass")
            (r / "tests" / "test_other.py").write_text("from app import util\n")
            (r / "tests" / "test_unrelated.py").write_text("pass")
            got = v.related_tests(r, ["app/calc.py", "app/util.py"])
            self.assertEqual(sorted(got), ["tests/test_calc.py", "tests/test_other.py"])
            self.assertEqual(v.related_tests(r, ["tests/test_unrelated.py"]), ["tests/test_unrelated.py"])

    def test_mutants_skip_comments_and_asserts(self):
        lines = ["# a > b", "assert a == b", "if a > b:", "    return a + b", "return True"]
        got = v.make_mutants(lines, [1, 2, 3, 4, 5], 5)
        self.assertEqual([n for n, _ in got], [3, 4, 5])
        self.assertIn("a >= b", got[0][1])

    def test_covered_share(self):
        cov = {"files": {"app/calc.py": {"executed_lines": [2, 3], "missing_lines": [4]}}}
        share, missing = v.covered_share(cov, {"app/calc.py": [2, 3, 4, 99]})
        self.assertAlmostEqual(share, 2 / 3)
        self.assertEqual(missing, {"app/calc.py": [4]})
        self.assertEqual(v.covered_share({}, {"x.py": [1]})[0], 1.0)


class Fake(v.VerifyMixin):
    def __init__(self, tmp, feats=None):
        self.dir, self.repo = Path(tmp), Path(tmp)
        self.cfg = {"features": dict(feats or {}), "commands": {"test": "t"}}
        self.logs, self.metrics, self.n = [], [], 0

        class G:
            def run(s, *a):
                return 0, ""

            def out(s, *a):
                return "tree1"
        self.git = G()

    def log(self, m):
        self.logs.append(m)

    def metric(self, **kw):
        self.metrics.append(kw)


class RunCmdTests(unittest.TestCase):
    def run_with(self, rcs, feats=None):
        with tempfile.TemporaryDirectory() as t:
            f = Fake(t, feats)
            calls = []

            def fake_live(cmd, *a, **k):
                calls.append(cmd)
                return rcs[min(len(calls) - 1, len(rcs) - 1)], f"out{len(calls)}"
            real = v.sh_live
            v.sh_live = fake_live
            try:
                first = f.run_cmd("test", "t", 5)
                second = f.run_cmd("test", "t", 5)
            finally:
                v.sh_live = real
            return f, calls, first, second

    def test_green_result_is_cached(self):
        f, calls, first, second = self.run_with([0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(second, (0, "cached ok"))
        self.assertEqual(f.metrics[-1]["kind"], "verify_cache_hit")

    def test_flaky_test_passes_on_retry(self):
        f, calls, first, _ = self.run_with([1, 0])
        self.assertEqual(first[0], 0)
        self.assertTrue(any(m["kind"] == "flaky_test" for m in f.metrics))

    def test_real_failure_stays_red_and_is_not_cached(self):
        f, calls, first, second = self.run_with([1])
        self.assertEqual(first[0], 1)
        self.assertEqual(second[0], 1)
        self.assertEqual(len(calls), 4)

    def test_features_can_be_switched_off(self):
        f, calls, first, second = self.run_with([1, 0], {"flaky_retry": False, "verify_cache": False})
        self.assertEqual(first[0], 1)
        self.assertEqual(json.dumps(len(calls)), "2")


if __name__ == "__main__":
    unittest.main()
