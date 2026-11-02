import tempfile
import unittest
from pathlib import Path

from core import failures
from core.cfa_flow import removed_test_names, removed_tests
from core.cfa_write import extract_files, write_files

RUST_ASYNC = """--- a/tests/fs.rs
+++ b/tests/fs.rs
-    #[test]
-    fn test_search() {
-        assert!(true);
-    }
+    #[tokio::test]
+    async fn test_search() {
+        assert!(true);
+    }
"""


class DetectionTests(unittest.TestCase):
    def test_sync_to_async_rust_tests_is_not_a_deletion(self):
        self.assertEqual(removed_tests(RUST_ASYNC), 0)

    def test_really_removed_rust_test_is_found_and_named(self):
        d = "-    #[test]\n-    fn test_gone() {}\n"
        self.assertEqual(removed_tests(d), 2)
        self.assertEqual(removed_test_names(d), ["fn test_gone() {}"])

    def test_names_ignore_lines_that_were_added_back(self):
        d = "-def test_a():\n-def test_b():\n+def test_a():\n"
        self.assertEqual(removed_test_names(d), ["def test_b():"])


class FocusTests(unittest.TestCase):
    def test_root_cause_first_error_is_kept_not_the_cascade_tail(self):
        out = "warning: x\n" * 800 + "error: failed to select a version for `glob`\n  candidates: 0.3.1\n" + "noise\n" * 3000
        f = failures.focus(out, 3000)
        self.assertIn("failed to select a version", f)
        self.assertLess(len(f), 3200)

    def test_without_error_line_the_tail_is_kept(self):
        out = "a\n" * 5000 + "test result: FAILED. 1 failed\n"
        self.assertIn("test result: FAILED", failures.focus(out, 500))


class WriteSafetyTests(unittest.TestCase):
    def test_snippet_does_not_replace_a_big_file_but_complete_file_does(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "a.rs").write_text("line\n" * 100)
            self.assertEqual(write_files(repo, {"a.rs": "fn x() {}\n"}), [])
            self.assertEqual((repo / "a.rs").read_text(), "line\n" * 100)
            self.assertEqual(write_files(repo, {"a.rs": "line\n" * 90}), ["a.rs"])
            self.assertEqual(write_files(repo, {"new.rs": "x\n"}), ["new.rs"])

    def test_extract_needs_the_file_name_line(self):
        self.assertEqual(extract_files("a.rs\n```rust\nfn main() {}\n```", ["a.rs"]), {"a.rs": "fn main() {}\n"})


if __name__ == "__main__":
    unittest.main()
