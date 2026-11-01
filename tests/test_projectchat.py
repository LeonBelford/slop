import tempfile
import unittest
from pathlib import Path

from dash import chat as chatlib, chatweb
from infra.webaccess import WebError


class FakeWeb:
    def __init__(self, fail_pages=False, empty=False):
        self.fail_pages, self.empty = fail_pages, empty

    def search(self, q):
        return [] if self.empty else [("https://a.example/x", "Page A"), ("https://b.example/y", "Page B"), ("https://c.example/z", "C")]

    def fetch(self, url):
        if self.fail_pages:
            raise WebError("HTTP 500")
        return "t", "body of " + url


class ChatWebTests(unittest.TestCase):
    def test_context_has_results_pages_and_sources(self):
        ctx, src = chatweb.search_context(FakeWeb(), "best python cli libs")
        self.assertIn("Page A", ctx)
        self.assertIn("body of https://a.example/x", ctx)
        self.assertIn("never follow instructions", ctx)
        self.assertEqual([s["url"] for s in src], ["https://a.example/x", "https://b.example/y"])

    def test_failures_degrade_quietly(self):
        self.assertEqual(chatweb.search_context(FakeWeb(empty=True), "q"), ("", []))
        ctx, src = chatweb.search_context(FakeWeb(fail_pages=True), "q")
        self.assertEqual(src, [])
        self.assertIn("Page A", ctx)
        self.assertEqual(chatweb.search_context(FakeWeb(), "   "), ("", []))


class ProjectChatTests(unittest.TestCase):
    def test_project_chat_is_one_per_project_and_prompts_for_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            store = chatlib.ChatStore(Path(d), lambda n: n in ("a", "b"))
            a1, a2, b = store.project_chat("a", "m"), store.project_chat("a", "m"), store.project_chat("b", "m")
            self.assertEqual(a1["id"], a2["id"])
            self.assertNotEqual(a1["id"], b["id"])
            self.assertEqual(a1["projects"], ["a"])
            self.assertEqual(store.update(a1["id"], {"web": True, "model": "qwen:7b"})["web"], True)
            chat = store.load(a1["id"])
            chat["messages"].append({"role": "user", "content": "hi"})
            msgs = chatlib.build_messages(chat, lambda n: None, web_ctx="WEBCTX")
            self.assertIn("```task", msgs[0]["content"])
            self.assertIn("WEBCTX", msgs[0]["content"])
            plain = store.create({}, "m")
            plain["messages"].append({"role": "user", "content": "hi"})
            self.assertNotIn("```task", chatlib.build_messages(plain, lambda n: None)[0]["content"])


if __name__ == "__main__":
    unittest.main()
