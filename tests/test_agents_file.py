import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class AgentsFileTests(unittest.TestCase):
    def test_paths_named_in_agents_md_exist(self):
        text = (ROOT / "AGENTS.md").read_text("utf-8")
        for rel in set(re.findall(r"`((?:core|webapi|dash|infra|docs|tests|web)/[\w./-]+\.(?:py|md|js))`", text)):
            self.assertTrue((ROOT / rel).is_file(), f"AGENTS.md names a missing file: {rel}")

    def test_learned_facts_are_one_dated_line_each(self):
        lines = [x for x in (ROOT / "docs" / "LEARNED.md").read_text("utf-8").splitlines() if x.startswith("- ")]
        self.assertTrue(lines)
        for x in lines:
            self.assertRegex(x, r"^- \d{4}-\d{2}-\d{2} [\w-]+: ")

    def test_copilot_instructions_point_to_agents_md(self):
        self.assertIn("AGENTS.md", (ROOT / ".github" / "copilot-instructions.md").read_text("utf-8"))
