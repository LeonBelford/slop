import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stacks  # noqa: E402
import saas_agent as sa  # noqa: E402
from webaccess import Web, WebError  # noqa: E402


class StacksTest(unittest.TestCase):
    def test_every_public_stack_is_complete(self):
        for k in stacks.public_stacks():
            s = stacks.STACKS[k]
            for field in ("label", "language", "stack", "commands", "scaffold"):
                self.assertIn(field, s, k)
            self.assertTrue(stacks.scaffold(k), k)

    def test_missing_tool_gives_install_help(self):
        orig = stacks.shutil.which
        stacks.shutil.which = lambda name: None
        try:
            self.assertTrue(stacks.missing_tools("go"))
            self.assertTrue(stacks.install_help("go"))
        finally:
            stacks.shutil.which = orig


class WebTest(unittest.TestCase):
    def test_refuses_local_and_non_http(self):
        w = Web({"timeout": 3, "max_chars": 1000, "blocked_domains": [], "search_url": ""})
        for url in ("http://127.0.0.1:8000/", "file:///etc/passwd", "http://localhost/", "http://169.254.169.254/"):
            with self.assertRaises((WebError, ValueError, OSError), msg=url):
                w.fetch(url)

    def test_sources_parsing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "SOURCES.md"
            p.write_text("# t\n<!-- https://hidden.example -->\nhttps://a.example/x  use for billing\nhttps://a.example/x\n")
            self.assertEqual(sa.read_sources(p), [("https://a.example/x", "use for billing")])


if __name__ == "__main__":
    unittest.main()
