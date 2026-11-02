import tempfile
import unittest
from pathlib import Path

from core.cfgit import Backlog


class TaskDeleteTests(unittest.TestCase):
    def test_ids_stay_unique_after_a_delete(self):
        with tempfile.TemporaryDirectory() as d:
            bl = Backlog(Path(d) / "b.json")
            for t in "abc":
                bl.add(t, "x", force=True)
            bl.items = [i for i in bl.items if i["id"] != "T0002"]
            self.assertEqual(bl.add("d", "x", force=True)["id"], "T0004")


if __name__ == "__main__":
    unittest.main()
