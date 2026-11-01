import http.client
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codefactory as sa  # noqa: E402
import models  # noqa: E402
import webui  # noqa: E402


class RoutingTests(unittest.TestCase):
    def test_nothing_installed_changes_nothing(self):
        r = models.resolve(set(), {"mode": "auto"}, "ollama_chat/qwen2.5-coder:14b")
        self.assertEqual(r, {"light": None, "heavy": None, "final_reviewer": None})

    def test_detects_installed_and_off_switch(self):
        inst = {"qwen2.5-coder:7b", "devstral:24b", "qwen2.5-coder:14b"}
        r = models.resolve(inst, {"mode": "auto"}, "ollama_chat/qwen2.5-coder:14b")
        self.assertEqual((r["light"], r["heavy"], r["final_reviewer"]), ("qwen2.5-coder:7b", "devstral:24b", "devstral:24b"))
        self.assertEqual(models.resolve(inst, {"mode": "off"}), {"light": None, "heavy": None, "final_reviewer": None})

    def test_light_equal_to_default_is_dropped(self):
        r = models.resolve({"qwen2.5-coder:7b"}, {}, "ollama_chat/qwen2.5-coder:7b")
        self.assertIsNone(r["light"])

    def test_classify_and_escalation(self):
        self.assertEqual(models.classify("docs", 3, 1), "light")
        self.assertEqual(models.classify("feature", 2, 1), "standard")
        self.assertEqual(models.classify("feature", 5, 1), "heavy")
        self.assertEqual(models.classify("feature", 1, 2), "heavy")

    def test_agent_uses_installed_models_only(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "p"
            (ws / "agent").mkdir(parents=True)
            (ws / "repo").mkdir()
            ag = sa.Agent(ws)
            self.assertEqual(ag.coder_for("implement"), ag.cfg["coder_model"])
            ag.llm.installed = {"qwen2.5-coder:7b", "qwen3-coder:30b"}
            ag.task_class = "light"
            self.assertEqual(ag.coder_for("implement"), "ollama_chat/qwen2.5-coder:7b")
            ag.task_class = "standard"
            self.assertEqual(ag.coder_for("implement"), ag.cfg["coder_model"])
            self.assertEqual(ag.coder_for("debug2"), "ollama_chat/qwen3-coder:30b")


class LanAuthTests(unittest.TestCase):
    def test_token_required_and_cookie_login(self):
        with tempfile.TemporaryDirectory() as d:
            svc = webui.Service(Path(d))
            srv = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(svc, 0, "secret-token"))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            port = srv.server_address[1]

            def req(path, headers=None, method="GET", body=None):
                c = http.client.HTTPConnection("127.0.0.1", port)
                c.request(method, path, body=body, headers=headers or {})
                r = c.getresponse()
                r.read()
                return r

            self.assertEqual(req("/api/projects").status, 401)
            self.assertEqual(req("/?token=wrong").status, 401)
            login = req("/?token=secret-token")
            self.assertEqual(login.status, 302)
            cookie = login.getheader("Set-Cookie").split(";")[0]
            self.assertEqual(req("/api/projects", {"Cookie": cookie}).status, 200)
            self.assertEqual(req("/api/master", {"Cookie": cookie, "X-UI": "1", "Origin": "http://evil.example"},
                                 "POST", "{}").status, 403)
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
