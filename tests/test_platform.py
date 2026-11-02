import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core import templates
from dash import rollback
from infra import export, report, window


def git(d, *a):
    subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@x", *a], cwd=d, check=True, capture_output=True)


class PlatformTests(unittest.TestCase):
    def test_template_applies_and_rejects_unknown(self):
        self.assertIn("walking skeleton", templates.apply("idea", "cli"))
        self.assertEqual(templates.apply("idea", ""), "idea")
        with self.assertRaises(ValueError):
            templates.apply("idea", "x")

    def test_rollback_resets_to_layer_tag_with_backup(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            git(d, "init", "-q")
            (d / "a").write_text("1")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "one")
            git(d, "tag", "layer-0")
            (d / "a").write_text("2")
            git(d, "commit", "-qam", "two")
            self.assertEqual(rollback.layers(d), [0])
            r = rollback.rollback(d, 0)
            self.assertEqual((d / "a").read_text(), "1")
            self.assertTrue(r["backup"].startswith("backup-before"))
            with self.assertRaises(ValueError):
                rollback.rollback(d, 5)

    def test_window_wraps_midnight(self):
        self.assertTrue(window.open_now("22:00-07:00", datetime(2026, 1, 1, 23, 0)))
        self.assertTrue(window.open_now("22:00-07:00", datetime(2026, 1, 1, 3, 0)))
        self.assertFalse(window.open_now("22:00-07:00", datetime(2026, 1, 1, 12, 0)))
        self.assertTrue(window.open_now("", datetime(2026, 1, 1, 12, 0)))
        self.assertEqual(window.pick({"light": "s", "heavy": "b"}, "heavy", "22:00-07:00", datetime(2026, 1, 1, 12, 0)), "s")

    def test_report_lists_built_and_blocked(self):
        lines = report.project_lines("p", [{"id": "T1", "title": "a", "status": "done", "done_at": 100},
                                          {"id": "T2", "title": "b", "status": "blocked", "reason": "compile"}], 0)
        text = "\n".join(lines)
        self.assertIn("1 built, 1 blocked", text)
        self.assertIn("compile", text)

    def test_export_creates_clean_repo(self):
        with tempfile.TemporaryDirectory() as t:
            src, dst = Path(t) / "src", Path(t) / "out"
            src.mkdir()
            git(src, "init", "-q")
            (src / "m.py").write_text("x=1")
            git(src, "add", "-A")
            git(src, "commit", "-qm", "c")
            files = export.export(src, dst, "demo", "pytest")
            self.assertIn("LICENSE", files)
            self.assertIn(".github/workflows/ci.yml", files)
            self.assertIn("m.py", files)
            with self.assertRaises(ValueError):
                export.export(src, dst, "demo")


if __name__ == "__main__":
    unittest.main()
