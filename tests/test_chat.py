import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dash import chat
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


class FakeThink(http.server.BaseHTTPRequestHandler):
    """Reasons via an inline <think> block, then answers - like a local reasoning model."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for w in ("<think>I should", " say hi</think>", "Hi there"):
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

    def test_thinking_is_split_from_the_answer_and_streamed_separately(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeThink)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = webui.sa.DEFAULT_CONFIG["llm_api"]
        webui.sa.DEFAULT_CONFIG["llm_api"] = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
        try:
            c = self.svc.chat_create({"projects": ["demo"], "model": "m"})
            out = []
            self.svc.chat_stream(c["id"], "Hi", out.append)
        finally:
            webui.sa.DEFAULT_CONFIG["llm_api"] = old
            srv.shutdown()
        self.assertEqual("".join(d["think"] for d in out if "think" in d), "I should say hi")
        self.assertEqual("".join(d["delta"] for d in out if "delta" in d), "Hi there")
        saved = self.svc.chats.load(c["id"])
        self.assertEqual(saved["messages"][1]["content"], "Hi there")
        self.assertEqual(saved["messages"][1]["thinking"], "I should say hi")

    def test_model_warm_sends_an_empty_prompt_with_keep_alive(self):
        seen = []

        class Ollama(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = webui.sa.DEFAULT_CONFIG["llm_api"]
        webui.sa.DEFAULT_CONFIG["llm_api"] = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
        try:
            self.assertTrue(self.svc.model_warm({"name": "qwen2.5-coder:14b", "keep_alive": "10m"})["ok"])
            for _ in range(50):
                if seen:
                    break
                time.sleep(0.05)
            with self.assertRaises(webui.UiError):
                self.svc.model_warm({"name": "m", "keep_alive": "forever; rm"})
        finally:
            webui.sa.DEFAULT_CONFIG["llm_api"] = old
            srv.shutdown()
        self.assertEqual(seen[0][0], "/api/generate")
        self.assertEqual((seen[0][1]["prompt"], seen[0][1]["keep_alive"]), ("", "10m"))

    def test_at_mentions_inline_repo_files_and_block_traversal(self):
        ws = Path(tempfile.mkdtemp())
        (ws / "repo").mkdir()
        (ws / "repo" / "a.py").write_text("x=1")
        out = chat.mention_context("see @a.py and @../../etc/passwd.x", [ws])
        self.assertIn("x=1", out)
        self.assertNotIn("root:", out)

    def test_history_is_sanitised_for_strict_servers(self):
        out = chat.merge_turns([{"role": "user", "content": "a"}, {"role": "user", "content": " "}, {"role": "user", "content": "b"},
                                {"role": "assistant", "content": "c"}])
        self.assertEqual([m["role"] for m in out], ["user", "assistant"])
        self.assertEqual(out[0]["content"], "a\n\nb")

    def test_http_errors_show_the_server_message(self):
        class Bad(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"context too long"}')
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Bad)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(RuntimeError) as cm:
                list(chat.stream_completion(f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions", "k", "m", []))
        finally:
            srv.shutdown()
        self.assertIn("400", str(cm.exception))
        self.assertIn("context too long", str(cm.exception))

    def test_task_mentions_and_mention_list(self):
        ws = Path(tempfile.mkdtemp())
        (ws / "agent").mkdir()
        (ws / "agent" / "backlog.json").write_text('[{"id": "T0007", "title": "Fix login", "status": "pending", "description": "Users cannot log in"}]')
        out = chat.mention_context("please look at @T0007", [ws])
        self.assertIn("Fix login", out)
        self.assertIn("cannot log in", out)

    def test_repo_map_ranks_files_matching_the_question(self):
        import subprocess
        ws = Path(tempfile.mkdtemp())
        (ws / "repo").mkdir()
        (ws / "repo" / "auth.py").write_text("def login():\n    pass\n\nclass Session:\n    pass\n")
        (ws / "repo" / "util.py").write_text("def helper():\n    pass\n")
        subprocess.run(["git", "init", "-q"], cwd=ws / "repo", check=True)
        subprocess.run(["git", "add", "-A"], cwd=ws / "repo", check=True)
        m = chat.repo_map_context(ws, "fix the login flow")
        self.assertTrue(m.splitlines()[1].startswith("auth.py: login, Session"))
        self.assertIn("util.py: helper", m)

    def test_models_status(self):
        st = self.svc.models_status()
        self.assertIn("up", st)
        self.assertIn("loaded", st)
        self.assertIn("gpu", st)


if __name__ == "__main__":
    unittest.main()
