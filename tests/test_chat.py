import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path

import chat
import webui


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen = body
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for w in ("Hel", "lo"):
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": w}}]}).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "demo"
        (ws / "agent").mkdir(parents=True)
        (ws / "agent" / "GOAL.md").write_text("Build a pomodoro timer")
        self.svc = webui.Service(Path(self.tmp.name))
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.old = webui.sa.DEFAULT_CONFIG["llm_api"]
        webui.sa.DEFAULT_CONFIG["llm_api"] = f"http://127.0.0.1:{self.srv.server_address[1]}/v1/chat/completions"

    def tearDown(self):
        webui.sa.DEFAULT_CONFIG["llm_api"] = self.old
        self.srv.shutdown()
        self.tmp.cleanup()

    def test_links_validated(self):
        with self.assertRaises(chat.ChatError):
            self.svc.chat_create({"projects": ["nope"]})
        c = self.svc.chat_create({"projects": ["demo"]})
        self.assertEqual(self.svc.chats.list()["chats"][0]["projects"], ["demo"])
        self.svc.chats.update(c["id"], {"projects": []})
        self.assertEqual(self.svc.chats.load(c["id"])["projects"], [])
        self.svc.chats.delete(c["id"])
        self.assertEqual(self.svc.chats.list()["chats"], [])

    def test_stream_saves_answer_and_sends_project_context(self):
        c = self.svc.chat_create({"projects": ["demo"], "model": "m"})
        out = []
        self.svc.chat_stream(c["id"], "What is the goal?", out.append)
        self.assertEqual("".join(d["delta"] for d in out), "Hello")
        saved = self.svc.chats.load(c["id"])
        self.assertEqual([m["role"] for m in saved["messages"]], ["user", "assistant"])
        self.assertEqual(saved["messages"][1]["content"], "Hello")
        self.assertEqual(saved["title"], "What is the goal?")
        self.assertIn("pomodoro", Fake.seen["messages"][0]["content"])
        self.assertTrue(Fake.seen["stream"])

    def test_bad_id(self):
        with self.assertRaises(chat.ChatError):
            self.svc.chats.load("../../etc")


if __name__ == "__main__":
    unittest.main()
