import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import saas_agent as sa  # noqa: E402


class ProjectFolderTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(sa.slugify("Rechnungs-Tool für Freelancer mit Mahnwesen!"), "rechnungs-tool-fuer-freelancer-mit")
        self.assertEqual(sa.slugify("!!!"), "projekt")

    def test_unique_slug_and_pick(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first = sa.unique_slug(root, "Meine Idee")
            (root / first / "agent").mkdir(parents=True)
            (root / first / "agent" / "GOAL.md").write_text("x")
            self.assertEqual(sa.unique_slug(root, "Meine Idee"), "meine-idee-2")
            self.assertEqual(sa.pick_project(root, None).name, "meine-idee")
            self.assertEqual([p.name for p in sa.list_projects(root)], ["meine-idee"])




class ReadIdeasTest(unittest.TestCase):
    def test_template_is_ignored_and_content_counts(self):
        import tempfile
        from pathlib import Path
        import saas_agent as sa
        p = Path(tempfile.mkdtemp()) / "IDEAS.md"
        p.write_text(sa.IDEAS_TEMPLATE.format(idea="x"), "utf-8")
        self.assertEqual(sa.read_ideas(p), "")
        p.write_text(sa.IDEAS_TEMPLATE.format(idea="x") + "\n- PDF Export\n", "utf-8")
        self.assertIn("PDF Export", sa.read_ideas(p))


if __name__ == "__main__":
    unittest.main()
