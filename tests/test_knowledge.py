import json
import tempfile
import time
import types
import unittest
from pathlib import Path

from dash import chat
import codefactory as sa
from infra import knowledge


def fake_embed(texts):
    """Tiny 'semantic' model: login/auth words share one dimension, so they match without sharing a word."""
    groups = [("login", "authentication", "auth", "password"), ("database", "sql", "migration"), ("css", "style", "layout")]
    return [[float(any(w in t.lower() for w in g)) + 0.01 for g in groups] for t in texts]


class KBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "k.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fts_incremental_remove_and_weight(self):
        kb = knowledge.KB(self.path)
        self.assertTrue(kb.upsert("lesson", "a", "Tests only run with --no-cache flag"))
        self.assertFalse(kb.upsert("lesson", "a", "Tests only run with --no-cache flag"))
        kb.upsert("lesson", "b", "cache invalidation bug in tests", weight=0.2)
        self.assertEqual([h["ref"] for h in kb.search("no-cache tests")], ["a", "b"])
        self.assertTrue(kb.upsert("lesson", "a", "totally different text"))
        self.assertEqual([h["ref"] for h in kb.search("cache tests")], ["b"])
        self.assertEqual(kb.remove_missing("lesson", {"a"}), 1)
        self.assertEqual(kb.search("cache tests", ("lesson",)), [])
        self.assertEqual(kb.search("")[:0], [])

    def test_embeddings_find_meaning_without_shared_words(self):
        kb = knowledge.KB(self.path, fake_embed)
        kb.upsert("decision", "d1", "Authentication uses signed cookies")
        kb.upsert("decision", "d2", "Layout uses CSS grid")
        kb.commit()
        self.assertEqual(kb.embed_pending(), 2)
        self.assertEqual(kb.search("login fails")[0]["ref"], "d1")
        kb.embed = None  # keyword-only fallback finds nothing for this query
        self.assertEqual(kb.search("login fails"), [])

    def test_special_characters_do_not_break_search(self):
        kb = knowledge.KB(self.path)
        kb.upsert("code", "x.py", "def f(): return 'a-b'")
        self.assertIsInstance(kb.search('"; DROP TABLE doc; -- ) (*'), list)


class MemoryKnowledge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "agent").mkdir()
        (root / "repo").mkdir()
        cfg = json.loads(json.dumps(sa.DEFAULT_CONFIG))
        cfg["knowledge"]["embeddings"] = "off"
        self.mem = sa.Memory(root / "agent", root / "repo", cfg, types.SimpleNamespace(installed=set(), json=None), lambda m: None)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_lesson_dedupe_feedback_and_expiry(self):
        self.mem.add_lesson("Run tests with --no-cache", ["tests/a.py"], "T1")
        self.mem.add_lesson("Run tests with --no-cache", [], "T2")
        rows = self.mem.lessons()
        self.assertEqual(len(rows), 1)
        self.mem.shown_lessons = [rows[0]["id"]]
        self.mem.lesson_feedback(True)
        self.assertEqual(self.mem.lessons()[0]["hits"], 1)
        self.mem.shown_lessons = [rows[0]["id"]]
        self.mem.lesson_feedback(False)
        self.assertEqual(self.mem.lessons()[0]["misses"], 1)
        old = self.mem.lessons()
        old[0]["confirmed"] = "2000-01-01 00:00:00"
        self.mem.save_lessons(old)
        self.assertEqual(self.mem.age_lessons(), 1)
        self.assertEqual(self.mem.lessons(), [])
        self.assertTrue((self.mem.dir / "lessons_archive.jsonl").read_text())

    def test_context_consequences_and_chat(self):
        self.mem.add_lesson("Changing login.py breaks the session cookie", ["login.py"], "T1")
        with open(self.mem.dir / "decisions.jsonl", "w") as f:
            f.write(json.dumps({"item": "T0", "title": "Cookie sessions", "decision": "login.py issues signed cookies",
                                "consequences": "all handlers read the cookie", "time": "2026-01-01 00:00:00"}) + "\n")
        (self.root / "agent" / "web" / "pages").mkdir(parents=True)
        (self.root / "agent" / "web" / "pages" / "p.json").write_text(json.dumps(
            {"url": "https://example.org/cookies", "title": "Cookie guide", "summary": "Use HttpOnly cookies", "fetched": time.time()}))
        self.mem.refresh_kb()
        cons = self.mem.consequences_for(["login.py"], "rework login")
        self.assertTrue(any("login.py" in c for c in cons))
        pack = self.mem.knowledge_sections("cookies for login", ["login.py"])
        text = "\n".join(pack)
        self.assertIn("Lessons learned", text)
        self.assertIn("https://example.org/cookies", text)
        ws = self.root
        (ws / "agent" / "GOAL.md").write_text("g")
        ctx = chat.knowledge_context(ws, "how do cookies work?", "http://127.0.0.1:1/v1", "k")
        self.assertIn("example.org/cookies", ctx)

    def test_broken_index_is_rebuilt(self):
        self.mem.add_lesson("Something to remember", [], "T1")
        (self.mem.dir / "knowledge.db").write_bytes(b"not a database")
        self.mem._kb = None
        self.mem.refresh_kb()
        self.assertEqual(self.mem.kb.stats()["lesson"]["entries"], 1)


if __name__ == "__main__":
    unittest.main()
