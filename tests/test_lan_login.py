import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import webui  # noqa: E402


class LanLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(webui.Service(Path(self.tmp.name)), 8765, "tok123"))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_token_link_sets_lax_cookie_so_phone_scanner_redirect_works(self):
        st, h, _ = self.get("/?token=tok123")
        self.assertEqual(st, 302)
        self.assertIn("SameSite=Lax", h["Set-Cookie"])  # Strict would be dropped on the redirect after a cross-site open (QR scanner)
        self.assertEqual(self.get("/api/lan")[0], 401)

    def test_qr_link_uses_the_address_you_reached_the_ui_by(self):
        st, _, body = self.get("/api/lan", {"Cookie": "cf_token=tok123", "Host": "192.168.1.50:8765"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["url"], "http://192.168.1.50:8765/?token=tok123")


if __name__ == "__main__":
    unittest.main()
