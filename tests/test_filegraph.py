import unittest

from core import filegraph


class FileGraphTest(unittest.TestCase):
    def test_imports_and_tasks(self):
        files = ["app.py", "core/util.py", "web/a.js", "web/b.js"]
        src = {"app.py": "from core.util import x\nimport os\n", "web/a.js": "import {b} from './b.js'\n"}
        items = [{"id": f"T{n}", "title": "t", "status": "done", "files": ["core/util.py"]} for n in range(1, 7)]
        g = filegraph.build(files, lambda f: src.get(f, ""), items)
        self.assertIn(["app.py", "core/util.py", "import"], g["edges"])
        self.assertIn(["web/a.js", "web/b.js", "import"], g["edges"])
        u = next(n for n in g["nodes"] if n["path"] == "core/util.py")
        self.assertEqual([t["id"] for t in u["tasks"]], ["T6", "T5", "T4", "T3"])
        self.assertEqual((u["more"], u["total"], u["used_by"]), (2, 6, 1))


if __name__ == "__main__":
    unittest.main()


class PackHistoryTest(unittest.TestCase):
    def test_pack_names_earlier_tasks_on_the_same_files(self):
        from core import graphctx
        mk = lambda n, st, f: {"id": f"T{n}", "title": f"task {n}", "status": st, "files": f, "depends_on": [], "type": "feature"}  # noqa: E731
        items = [mk(1, "done", ["a.py"]), mk(2, "done", ["a.py"]), mk(3, "pending", ["a.py"])]
        text = graphctx.context_pack(items, items[2])["text"]
        self.assertIn("Earlier tasks that touched the same files", text)
        self.assertIn("T1", text)
        self.assertIn("T2", text)

    def test_pack_neighbourhood_and_hotspot(self):
        from core import graphctx
        cm = {"a.py": {"imports": ["b.py"], "imported_by": ["c.py", "d.py"]}, "b.py": {}}
        it = {"id": "T1", "title": "t", "status": "pending", "files": ["a.py"], "depends_on": [], "type": "feature"}
        text = graphctx.context_pack([it], it, codemap=cm)["text"]
        self.assertIn("your files use b.py", text)
        self.assertIn("used by c.py, d.py", text)
        busy = graphctx.hotspot_score("a.py", 100, 2, [it], cm)
        self.assertGreater(busy, graphctx.hotspot_score("b.py", 100, 2, [it], cm))

    def test_budget_trims_only_the_builds_on_list(self):
        from core import graphctx
        items = [{"id": f"T{n}", "title": "x" * 70, "status": "done", "files": ["a.py"], "result": "r" * 100, "depends_on": [f"T{n - 1}"] if n > 1 else [], "type": "feature"} for n in range(1, 9)]
        cur = {"id": "T9", "title": "t", "status": "pending", "files": ["a.py"], "depends_on": ["T8"], "type": "feature"}
        cm = {"a.py": {"imports": ["b.py"], "imported_by": ["c.py"]}}
        text = graphctx.context_pack(items + [cur], cur, codemap=cm)["text"]
        self.assertLessEqual(len(text), graphctx.PACK_CHARS)
        self.assertIn("File neighbourhood", text)

    def test_python_import_forms(self):
        files = {"core/graphctx.py", "core/__init__.py", "core/cfa_build.py", "tests/json.py", "pkg/util.py"}
        text = "from core import graphctx\nfrom . import graphctx as g\nfrom .graphctx import x\nimport json\nfrom pkg.util import y\n"
        self.assertEqual(filegraph.imports_of("core/cfa_build.py", text, files), {"core/graphctx.py", "pkg/util.py"})

    def test_blocked_process_step_does_not_hold_back_successors(self):
        import tempfile
        from pathlib import Path

        from core.cfgit import Backlog
        bl = Backlog(Path(tempfile.mkdtemp()) / "b.json")
        step = bl.add("Docs pass", "d", force=True)
        bl.set(step, status="blocked", process="p1")
        nxt = bl.add("Feature", "d", force=True, depends_on=[step["id"]])
        self.assertTrue(bl.ready([nxt]))

    def test_html_css_and_served_paths(self):
        files = {"web/index.html": "", "web/app.js": "", "web/style.css": "", "web/chat.js": "", "api/server.py": "", "api/models.py": ""}
        html = '<link href="/static/style.css"><script src="/static/app.js"></script><script src="chat.js"></script><script src="https://x.y/z.js">'
        self.assertEqual(filegraph.imports_of("web/index.html", html, files), {"web/style.css", "web/app.js", "web/chat.js"})
        self.assertEqual(filegraph.imports_of("api/server.py", "import models\n", files), {"api/models.py"})

    def test_other_languages(self):
        f = {"src/main.c", "src/util.h", "go/app/main.go", "go/app/pkg/db/db.go", "src/lib.rs", "src/net.rs", "src/net/mod.rs",
             "app/src/com/x/Repo.java", "app/src/com/x/Main.java", "lib/helper.rb", "lib/app.rb", "web/components/Button.tsx", "web/page.tsx"}
        g = filegraph.imports_of
        self.assertEqual(g("src/main.c", '#include "util.h"\n#include <stdio.h>', f), {"src/util.h"})
        self.assertEqual(g("go/app/main.go", 'import (\n\t"fmt"\n\t"example.com/me/app/pkg/db"\n)', f), {"go/app/pkg/db/db.go"})
        self.assertEqual(g("src/lib.rs", "mod net;\nuse crate::net::Conn;", f), {"src/net.rs"})
        self.assertEqual(g("app/src/com/x/Main.java", "import com.x.Repo;\nimport java.util.List;", f), {"app/src/com/x/Repo.java"})
        self.assertEqual(g("lib/app.rb", "require_relative 'helper'", f), {"lib/helper.rb"})
        self.assertEqual(g("web/page.tsx", "import B from 'components/Button'", f), {"web/components/Button.tsx"})

    def test_keep_prefers_code(self):
        fs = ["a.png", "node_modules/x.js", "z/app.go", "README.md"]
        self.assertEqual(filegraph.keep(fs), ["z/app.go", "README.md"])

    def test_no_false_links(self):
        g = filegraph.imports_of
        self.assertEqual(g("web/app.js", "import c from 'react-dom/client'", {"web/app.js", "web/client.js", "src/client.js"}), set())
        go = {"main.go", "log/log.go", "cmd/x.go"}
        self.assertEqual(g("main.go", 'import "log"\nvar names = []string{\n\t"cmd"\n}\n', go), set())
        self.assertEqual(g("main.go", 'import "me/app/cmd"', go), {"cmd/x.go"})

    def test_rust_crate_paths(self):
        f = {"src/lib.rs", "src/core/mod.rs", "src/core/file_system/mod.rs", "src/core/file_system/observer.rs", "tests/observer_test.rs", "src/main.rs"}
        g = filegraph.imports_of
        self.assertEqual(g("src/lib.rs", "pub mod core;", f), {"src/core/mod.rs"})
        self.assertEqual(g("src/core/mod.rs", "pub mod file_system;", f), {"src/core/file_system/mod.rs"})
        self.assertEqual(g("src/core/file_system/mod.rs", "pub mod observer;\nuse std::fs;", f), {"src/core/file_system/observer.rs"})
        self.assertEqual(g("tests/observer_test.rs", "use rustexplorer::core::file_system::observer::Watch;", f), {"src/core/file_system/observer.rs"})
        self.assertEqual(g("src/main.rs", "use rustexplorer::core::{file_system, other};", f), {"src/core/mod.rs", "src/core/file_system/mod.rs"})

    def test_smart_connection_kinds(self):
        from core import fileconn
        files = ["src/lib.rs", "src/core/mod.rs", "src/core/file_system/mod.rs", "src/core/file_system/observer.rs", "src/core/file_system/entry.rs",
                 "tests/file_system_observer_test.rs", "ARCHITECTURE.md", "templates/search_results.html", "src/web.rs", "README.md"]
        texts = {"ARCHITECTURE.md": "See `src/core/file_system/observer.rs` and entry.rs", "src/web.rs": 'include_str!("../templates/search_results.html")'}
        by = {(a, b): k for a, b, k, *_ in filegraph.edges_of(files, lambda f: texts.get(f, ""))}
        self.assertEqual(by[("tests/file_system_observer_test.rs", "src/core/file_system/observer.rs")], "test")
        self.assertEqual(by[("ARCHITECTURE.md", "src/core/file_system/observer.rs")], "mention")
        self.assertEqual(by[("ARCHITECTURE.md", "src/core/file_system/entry.rs")], "mention")
        self.assertEqual(by[("src/web.rs", "templates/search_results.html")], "mention")
        self.assertEqual(by[("src/core/file_system/mod.rs", "src/core/file_system/observer.rs")], "module")
        self.assertEqual(by[("src/core/mod.rs", "src/core/file_system/mod.rs")], "module")
        items = [{"id": "T1", "files": ["src/web.rs", "src/lib.rs"]}, {"id": "T2", "files": ["src/web.rs", "src/lib.rs", "README.md"]}]
        t = {(a, b): w for a, b, k, w in fileconn.together(items, set(files))}
        self.assertEqual(t[("src/lib.rs", "src/web.rs")], 2)
        self.assertEqual(t[("README.md", "src/web.rs")], 1)

    def test_import_beats_weaker_kinds_and_keeps_direction(self):
        from core import fileconn
        e = fileconn.merge([["a.py", "b.py", "import"]], [["b.py", "a.py", "mention"], ["a.py", "c.py", "module"]], [["c.py", "a.py", "task", 3]])
        self.assertEqual(e, [["a.py", "b.py", "import"], ["a.py", "c.py", "module"]])

    def test_long_minified_line_stays_fast(self):
        import time
        from core import fileconn
        t = time.time()
        fileconn.mentions_of(["a.js", "b.js"], lambda f: "x" * 60000 if f == "a.js" else "")
        self.assertLess(time.time() - t, 2)
