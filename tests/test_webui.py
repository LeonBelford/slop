import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import webui  # noqa: E402
import selfimprove  # noqa: E402


class ProtectedTests(unittest.TestCase):
    def test_directed_allows_webui_but_not_webaccess(self):
        changes = ["M\twebui.py", "M\tinfra/webaccess.py"]
        bad = selfimprove.protected_violations(changes, selfimprove.PROTECTED_DIRECTED)
        self.assertEqual(len(bad), 1)
        self.assertIn("infra/webaccess.py", bad[0])

    def test_autonomous_blocks_webui(self):
        self.assertTrue(selfimprove.protected_violations(["M\twebui.py"]))


class DiffValidation(unittest.TestCase):
    def test_sha_and_branch_patterns(self):
        self.assertTrue(webui.SHA_RE.match("abc1234"))
        self.assertFalse(webui.SHA_RE.match("--output=x"))
        self.assertTrue(webui.BRANCH_RE.match("self-improve/20260101-120000"))
        self.assertFalse(webui.BRANCH_RE.fullmatch("../x"))


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "demo"
        (self.ws / "agent").mkdir(parents=True)
        (self.ws / "repo").mkdir()
        (self.ws / "agent" / "GOAL.md").write_text("g")
        webui.sa.write_json(self.ws / "agent" / "backlog.json", [
            {"id": "T0001", "title": "a", "priority": 5.0, "status": "pending", "type": "feature", "notes": []},
            {"id": "T0002", "title": "b", "priority": 7.0, "status": "pending", "type": "feature", "notes": []}])
        self.svc = webui.Service(root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_validated_and_saved(self):
        self.svc.act("demo", "settings", {"limits": {"max_hours": 5}, "web_mode": "off", "models": {"planner": "qwen2.5-coder:7b"}})
        conf = webui.sa.read_json(self.ws / "agent" / "config.json", {})
        self.assertEqual(conf["limits"]["max_hours"], 5)
        self.assertEqual(conf["web"]["mode"], "off")
        for bad in ({"limits": {"max_hours": 0}}, {"limits": {"evil": 1}}, {"web_mode": "x"}, {"models": {"planner": "a b;rm"}}):
            with self.assertRaises(webui.UiError):
                self.svc.act("demo", "settings", bad)

    def test_stream_endpoint_is_light_and_empty_when_idle(self):
        (self.ws / "agent" / "llm_stream.json").write_text('{"tag": "plan", "thinking": "x", "answer": ""}')
        r = self.svc.stream("demo")
        self.assertEqual(set(r), {"stream", "llm", "coder_live", "cmd_live"})
        self.assertIsNone(r["stream"])
        self.assertEqual(r["coder_live"], "")
        self.assertEqual(set(self.svc.tool_stream()), set(r))

    def test_run_mode_validated_and_saved(self):
        self.svc.act("demo", "settings", {"run": {"mode": "rounds", "rounds": 5}})
        self.assertEqual(webui.sa.read_json(self.ws / "agent" / "config.json", {})["run"], {"mode": "rounds", "rounds": 5})
        for bad in ({"mode": "x"}, {"mode": "rounds", "rounds": 0}, {"mode": "rounds", "rounds": "a"}):
            with self.assertRaises(webui.UiError):
                self.svc.act("demo", "settings", {"run": bad})

    def test_side_task_spawns_with_side_priority(self):
        seen = {}
        orig = webui.spawn
        webui.spawn = lambda key, args, out, env=None: seen.update(env=env, args=args)
        try:
            self.svc.act("demo", "do", {"task": "add a dark mode", "side": True})
        finally:
            webui.spawn = orig
        self.assertEqual(seen["env"], {"CODEFACTORY_PRIORITY": "side"})
        self.assertEqual(self.svc.summary(self.ws)["priority"], "main")

    def test_task_edit_and_reload_by_running_agent(self):
        self.svc.act("demo", "task-top", {"id": "T0001"})
        self.svc.act("demo", "task-cancel", {"id": "T0002"})
        bl = webui.sa.Backlog(self.ws / "agent" / "backlog.json")
        self.assertEqual(bl.next()["id"], "T0001")
        stale = webui.sa.Backlog(self.ws / "agent" / "backlog.json")
        item = stale.items[0]
        self.svc.act("demo", "task-cancel", {"id": "T0001"})
        self.assertIsNone(stale.next())
        self.assertIs(stale.items[0], item)

    def test_rename_and_delete(self):
        self.svc.act("demo", "rename", {"name": "other"})
        self.assertTrue((self.svc.projects / "other").exists())
        with self.assertRaises(webui.UiError):
            self.svc.act("other", "delete", {"confirm": "nope"})
        self.svc.act("other", "delete", {"confirm": "other"})
        self.assertFalse((self.svc.projects / "other").exists())


if __name__ == "__main__":
    unittest.main()


class ModelManagerTests(unittest.TestCase):
    def setUp(self):
        import http.server
        import json
        import threading
        self.models = {"a:1b": 1e9}

        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def body(self):
                return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"models": [{"name": n, "size": s} for n, s in outer.models.items()]}).encode())

            def do_DELETE(self):
                outer.models.pop(self.body()["model"], None)
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                b = self.body()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"pulling x","total":10,"completed":5}\n')
                outer.models[b["model"]] = 2e9
                self.wfile.write(b'{"status":"success"}\n')

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = webui.Service(Path(self.tmp.name))
        self.svc.ollama_base = lambda: f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()
        webui.PULLS.clear()

    def test_pull_then_delete(self):
        import time
        self.svc.model_pull({"name": "b:2b"})
        for _ in range(50):
            if not webui.PULLS["b:2b"]["active"]:
                break
            time.sleep(0.05)
        self.assertEqual(webui.PULLS["b:2b"]["error"], "")
        names = [m["name"] for m in self.svc.models_overview()["installed"]]
        self.assertIn("b:2b", names)
        self.svc.model_delete({"name": "b:2b"})
        self.assertNotIn("b:2b", [m["name"] for m in self.svc.models_overview()["installed"]])

    def test_invalid_name_rejected(self):
        with self.assertRaises(webui.UiError):
            self.svc.model_pull({"name": "x; rm -rf /"})
