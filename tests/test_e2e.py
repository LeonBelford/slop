import http.server
import tempfile
import threading
import unittest
from pathlib import Path

import e2e

PY = e2e.find_python()
if not PY and Path("/tmp/pwenv/bin/python").exists():
    PY = "/tmp/pwenv/bin/python"


class Helpers(unittest.TestCase):
    def test_extract_url(self):
        self.assertEqual(e2e.extract_url("http://localhost:5000 (docs at /docs)"), "http://127.0.0.1:5000")
        self.assertEqual(e2e.extract_url(""), "")

    def test_extract_script(self):
        self.assertEqual(e2e.extract_script("x\n```python\ndef run(page, base):\n    pass\n```"), "def run(page, base):\n    pass\n")
        self.assertEqual(e2e.extract_script("no code"), "")


@unittest.skipUnless(PY, "Playwright not installed")
class Browser(unittest.TestCase):
    def test_smoke_and_script(self):
        d = tempfile.mkdtemp()
        Path(d, "index.html").write_text("<button onclick=\"o.textContent='ok'\">Go</button><p id=o></p><script>console.error('boom')</script>")
        h = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=d, **k)  # noqa: E731
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        try:
            rep = e2e.smoke(PY, url, Path(d, "out"))
            self.assertFalse(rep["ok"])
            self.assertTrue(any("boom" in p for p in rep["problems"]))
            good = Path(d, "good.py")
            good.write_text("def run(page, base):\n    page.goto(base)\n    page.get_by_role('button', name='Go').click()\n    expect(page.locator('#o')).to_have_text('ok')\n")
            self.assertTrue(e2e.run_script(PY, good, url, Path(d, "out"))["ok"])
            bad = Path(d, "bad.py")
            bad.write_text("def run(page, base):\n    page.goto(base)\n    expect(page.locator('#o')).to_have_text('never')\n")
            self.assertFalse(e2e.run_script(PY, bad, url, Path(d, "out"))["ok"])
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
