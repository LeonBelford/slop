import tempfile
import unittest
from pathlib import Path

from core.cfa_conductor import ConductorMixin
from core.cfa_handoff import BLOCK_CHARS, HandoffMixin, render


class Agent(HandoffMixin, ConductorMixin):
    def __init__(self, rd):
        self.llm = type("L", (), {"session_note": ""})()
        self.run_dir = Path(rd)
        self.log = lambda m: None


class HandoffTests(unittest.TestCase):
    def test_render_has_sections_skips_empty_and_is_bounded(self):
        t = render("implementation", "planning", goal="T1 add parser", decided=["use json because stdlib"], do=["write parse()"], do_not=[])
        self.assertIn("from the planning step to the implementation step", t)
        self.assertIn("use json because stdlib", t)
        self.assertNotIn("Do not:", t)
        self.assertLessEqual(len(render("a", "b", goal="x" * 5000, do=["y" * 5000] * 20, open_=["z"] * 20)), BLOCK_CHARS + 40)

    def test_pinned_first_in_session_note_and_prompts_and_reset_per_task(self):
        with tempfile.TemporaryDirectory() as d:
            a = Agent(d)
            a.journal_start({"id": "T1", "title": "x", "acceptance": "y"})
            a.handoff("debug", "implementation", goal="G", do=["fix root cause"], do_not=["weaken tests"])
            self.assertTrue(a.llm.session_note.startswith("INSTRUCTIONS from the implementation step"))
            self.assertIn("weaken tests", (Path(d) / "journal.md").read_text())
            self.assertTrue(a.handoff_text().endswith("\n\n"))
            a.journal_start({"id": "T2", "title": "x", "acceptance": "y"})
            self.assertEqual(a.handoff_text(), "")
            self.assertNotIn("INSTRUCTIONS", a.llm.session_note)


if __name__ == "__main__":
    unittest.main()
