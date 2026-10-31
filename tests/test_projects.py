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


if __name__ == "__main__":
    unittest.main()
