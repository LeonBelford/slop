import http.server
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import webui

BLK = "```action\n%s\n```"
SCRIPT = [
    "Let me look.\n" + BLK % json.dumps({"tool": "list_dir", "project": "demo"}) + "\n" + BLK % json.dumps({"tool": "read_file", "project": "demo", "path": "app.py"}),
    "Now edit.\n" + BLK % json.dumps({"tool": "edit_file", "project": "demo", "path": "app.py", "old": "return 1", "new": "return 2"})
    + "\n" + BLK % json.dumps({"tool": "write_file", "project": "demo", "path": "src/new.py", "content": "X = 1\n"})
    + "\n" + BLK % json.dumps({"tool": "write_file", "project": "demo", "path": "../evil.py", "content": "x"}),
    "Done: app.py now returns 2 and src/new.py exists.",
]


class Fake(http.server.BaseHTTPRequestHandler):
    n = 0
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        Fake.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        text = SCRIPT[min(Fake.n, len(SCRIPT) - 1)]
        Fake.n += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": text}}]}).encode() + b"\n\ndata: [DONE]\n\n")


class DirectChatTests(unittest.TestCase):
    def setUp(self):
        Fake.n, Fake.seen = 0, []
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "demo"
        (ws / "agent").mkdir(parents=True)
        (ws / "agent" / "GOAL.md").write_text("Build a demo")
        repo = ws / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# Rules\nAlways return numbers.")
        (repo / "app.py").write_text("def f():\n    return 1\n")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):
            subprocess.run(["git", *c], cwd=repo, check=True, capture_output=True)
        self.repo = repo
        self.svc = webui.Service(Path(self.tmp.name))
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.old = webui.sa.DEFAULT_CONFIG["llm_api"]
        webui.sa.DEFAULT_CONFIG["llm_api"] = f"http://127.0.0.1:{self.srv.server_address[1]}/v1/chat/completions"
        self.svc._running = lambda ws, name: False

    def tearDown(self):
        webui.sa.DEFAULT_CONFIG["llm_api"] = self.old
        self.srv.shutdown()
        self.tmp.cleanup()

    def chat(self, route):
        c = self.svc.chat_create({"projects": ["demo"], "model": "m"})
        self.svc.chats.update(c["id"], {"agent": True, "route": route})
        return c["id"]

    def test_direct_run_reads_docs_then_edits_repo(self):
        cid = self.chat("direct")
        self.svc.chat_stream(cid, "make f return 2 and add src/new.py", lambda d: None)
        self.assertEqual((self.repo / "app.py").read_text(), "def f():\n    return 2\n")
        self.assertEqual((self.repo / "src" / "new.py").read_text(), "X = 1\n")
        self.assertFalse((self.repo.parent / "evil.py").exists())
        msgs = self.svc.chats.load(cid)["messages"]
        self.assertEqual(msgs[1]["tool"], "read_docs")
        self.assertIn("README.md", msgs[1]["content"])
        acts = msgs[3]["acts"]
        self.assertEqual([acts[k]["status"] for k in "012"], ["done", "done", "failed"])
        self.assertIn("1: def f()", msgs[2]["acts"]["1"]["result"])
        self.assertIn("Done:", msgs[-1]["content"])
        self.assertIn("Always return numbers", Fake.seen[0]["messages"][0]["content"])
        self.assertIn("DIRECT mode", Fake.seen[0]["messages"][0]["content"])
        self.assertIn("1: def f()", json.dumps(Fake.seen[1]["messages"]))
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertIn("chat: edit app.py", log)

    def test_direct_blocked_while_agent_runs(self):
        self.svc._running = lambda ws, name: True
        cid = self.chat("direct")
        self.svc.chat_stream(cid, "go", lambda d: None)
        self.assertEqual((self.repo / "app.py").read_text(), "def f():\n    return 1\n")
        acts = self.svc.chats.load(cid)["messages"][3]["acts"]
        self.assertEqual(acts["0"]["status"], "failed")
        self.assertIn("working on the repo", acts["0"]["result"])

    def test_pipeline_route_does_not_run_actions_itself(self):
        cid = self.chat("pipeline")
        self.svc.chat_stream(cid, "go", lambda d: None)
        self.assertEqual(Fake.n, 1)
        self.assertNotIn("acts", self.svc.chats.load(cid)["messages"][-1])
        self.assertNotIn("DIRECT mode", Fake.seen[0]["messages"][0]["content"])

    def test_edit_needs_unique_match(self):
        from dash import directtools
        with self.assertRaises(ValueError):
            directtools.edit_file(self.repo, "app.py", "zzz", "y")
        with self.assertRaises(ValueError):
            directtools.edit_file(self.repo, "../x", "a", "b")
