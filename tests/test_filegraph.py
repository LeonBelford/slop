import unittest

from core import filegraph


class FileGraphTest(unittest.TestCase):
    def test_imports_and_tasks(self):
        files = ["app.py", "core/util.py", "web/a.js", "web/b.js"]
        src = {"app.py": "from core.util import x\nimport os\n", "web/a.js": "import {b} from './b.js'\n"}
        items = [{"id": f"T{n}", "title": "t", "status": "done", "files": ["core/util.py"]} for n in range(1, 7)]
        g = filegraph.build(files, lambda f: src.get(f, ""), items)
        self.assertIn(["app.py", "core/util.py"], g["edges"])
        self.assertIn(["web/a.js", "web/b.js"], g["edges"])
        u = next(n for n in g["nodes"] if n["path"] == "core/util.py")
        self.assertEqual([t["id"] for t in u["tasks"]], ["T6", "T5", "T4", "T3"])
        self.assertEqual((u["more"], u["total"], u["used_by"]), (2, 6, 1))


if __name__ == "__main__":
    unittest.main()
