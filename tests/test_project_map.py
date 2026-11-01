import re
import tempfile
import unittest
from pathlib import Path

import selfimprove

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = {"config.json", "state.json", "backlog.json", "metrics.json", "GOAL.md", "IDEAS.md", "ARCHITECTURE.md", "playbook.md"}


# Copies of the tree (self-improve fixtures) may not contain the map; the real repo always does.
@unittest.skipUnless((ROOT / selfimprove.MAP_FILE).exists(), "no map in this copy")
class ProjectMapTests(unittest.TestCase):
    def test_map_exists_with_sections(self):
        text = (ROOT / selfimprove.MAP_FILE).read_text("utf-8")
        for h in ("Repository layout", "Data flow", "Self-improve", "Protected files", "Testing rules"):
            self.assertIn(h, text)

    def test_referenced_files_exist(self):
        text = (ROOT / selfimprove.MAP_FILE).read_text("utf-8")
        for ref in set(re.findall(r"`([\w./-]+\.(?:py|md|html|sh))`", text)):
            if ref.startswith("agent/") or ref in RUNTIME:
                continue
            self.assertTrue(any((ROOT / d / ref).exists() for d in ("", "docs", "web", "scripts", "tests")), ref)

    def test_missing_map_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                selfimprove.project_map(Path(d))
            self.assertEqual(selfimprove.project_map(Path(d), required=False), "")

    def test_map_loaded(self):
        self.assertIn("CodeFactory project map", selfimprove.project_map(ROOT))


if __name__ == "__main__":
    unittest.main()
