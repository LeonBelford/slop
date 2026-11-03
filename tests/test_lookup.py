import tempfile
import unittest
from pathlib import Path

from core.cfa_lookup import LookupMixin, error_query, passages


class Stub(LookupMixin):
    def __init__(self, mode="open"):
        from infra.webaccess import Web
        self.cfg = {"web": {"mode": mode, "lookups_per_task": 2}, "features": {}}
        self.web = Web(self.cfg["web"])
        self.web_dir = Path(tempfile.mkdtemp())
        self.state, self.logs = {}, []
        self.log = self.logs.append
        self.note = lambda *a, **k: None
        self.fetched = 0
        self.web.mock_search = lambda q: [("https://docs.example.org/a", "Docs")]

        def fetch(url):
            self.fetched += 1
            return "Docs", "httpx.ConnectError is raised when the connection to the server cannot be opened\nunrelated marketing text about our product and pricing plans"
        self.web.mock_fetch = fetch


class LookupTests(unittest.TestCase):
    def test_error_query_drops_paths_numbers_and_keeps_the_error(self):
        rep = "[test] FAILED rc=1\n$ pytest\nFile \"/home/u/app/main.py\", line 12\nE   httpx.ConnectError: cannot open 'host' port 8080"
        q = error_query(rep, "Python 3, FastAPI")
        self.assertIn("httpx.ConnectError", q)
        self.assertNotIn("/home", q)
        self.assertNotIn("8080", q)

    def test_passages_pick_relevant_lines_only(self):
        text = "httpx.ConnectError is raised when the connection to the server cannot be opened\nunrelated marketing text about our product and pricing plans"
        self.assertIn("ConnectError", passages(text, "httpx.ConnectError connection cannot opened"))
        self.assertNotIn("marketing", passages(text, "httpx.ConnectError connection cannot opened"))

    def test_lookup_is_marked_untrusted_cached_and_budgeted(self):
        a = Stub()
        item = {"id": "T1", "attempts": 1}
        out = a.lookup("httpx.ConnectError connection cannot opened", "debug", item)
        self.assertIn("untrusted", out)
        self.assertIn("ConnectError", out)
        a.lookup("httpx.ConnectError connection cannot opened", "debug", item)
        self.assertEqual(a.fetched, 1)  # second call from the cache
        a.web.mock_search = lambda q: [("https://docs.example.org/b", "B")]
        a.lookup("another httpx.ConnectError query connection", "debug", item)
        self.assertEqual(a.lookup("a third httpx.ConnectError query connection", "debug", item), "")  # budget of 2 per attempt

    def test_off_by_default_and_pauses_when_offline(self):
        self.assertEqual(Stub("sources").lookup("anything useful here", "x"), "")
        a = Stub()
        a.web.mock_search = lambda q: (_ for _ in ()).throw(__import__("infra.webaccess", fromlist=["WebError"]).WebError("offline"))
        self.assertEqual(a.lookup("anything useful here", "x"), "")
        self.assertFalse(a.lookup_enabled())
