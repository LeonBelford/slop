"""The self-improve (tool) chat's direct route: it reads CodeFactory's own docs and edits the repo itself, like the project agent."""
import http.server
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import webui
from webapi import common as webapi_common

BLK = "```action\n%s\n```"
SCRIPT = [
    "Let me look.\n" + BLK % json.dumps({"tool": "list_dir"}) + "\n" + BLK % json.dumps({"tool": "read_file", "path": "app.py"}),
    "Now edit.\n" + BLK % json.dumps({"tool": "edit_file", "path": "app.py", "old": "return 1", "new": "return 2"})
    + "\n" + BLK % json.dumps({"tool": "write_file", "path": "src/new.py", "content": "X = 1\n"})
    + "\n" + BLK % json.dumps({"tool": "write_file", "path": "../evil.py", "content": "x"}),
    "Done: app.py now returns 2 and src/new.py exists.",
]


def git(cwd, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)


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


class SelfDirectTests(unittest.TestCase):
    def setUp(self):
        Fake.n, Fake.seen = 0, []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "PROJECT_MAP.md").write_text("# Map\nAlways return numbers.")
        (self.repo / "app.py").write_text("def f():\n    return 1\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.old = webapi_common.ROOT
        webapi_common.ROOT = self.repo
        self.addCleanup(lambda: setattr(webapi_common, "ROOT", self.old))
        self.svc = webui.Service(Path(self.tmp.name) / "projects")
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.old_api = webui.sa.DEFAULT_CONFIG["llm_api"]
        webui.sa.DEFAULT_CONFIG["llm_api"] = f"http://127.0.0.1:{self.srv.server_address[1]}/v1/chat/completions"

    def tearDown(self):
        webui.sa.DEFAULT_CONFIG["llm_api"] = self.old_api
        self.srv.shutdown()

    def chat(self):
        c = self.svc.chat_tool()
        self.svc.chats.update(c["id"], {"agent": True, "route": "direct"})
        return c["id"]

    def test_direct_run_reads_codefactorys_own_docs_then_edits_the_repo(self):
        cid = self.chat()
        self.svc.chat_stream(cid, "make f return 2 and add src/new.py", lambda d: None)
        self.assertEqual((self.repo / "app.py").read_text(), "def f():\n    return 2\n")
        self.assertEqual((self.repo / "src" / "new.py").read_text(), "X = 1\n")
        self.assertFalse((self.repo.parent / "evil.py").exists())
        msgs = self.svc.chats.load(cid)["messages"]
        self.assertEqual(msgs[1]["tool"], "read_docs")
        self.assertIn("PROJECT_MAP.md", msgs[1]["content"])
        acts = msgs[3]["acts"]
        self.assertEqual([acts[k]["status"] for k in "012"], ["done", "done", "failed"])

    def test_self_direct_edit_is_blocked_while_self_improve_runs(self):
        from webapi import common as common_mod
        common_mod.PROCS["tool"] = subprocess.Popen(["sleep", "5"])
        self.addCleanup(lambda: common_mod.PROCS["tool"].kill())
        cid = self.chat()
        self.svc.chat_stream(cid, "make f return 2", lambda d: None)
        self.assertEqual((self.repo / "app.py").read_text(), "def f():\n    return 1\n")
        msgs = self.svc.chats.load(cid)["messages"]
        acts = msgs[3]["acts"]
        self.assertEqual(acts["0"]["status"], "failed")
        self.assertIn("working on the repo", acts["0"]["result"])

    def test_pipeline_tool_tools_are_not_offered_in_self_mode(self):
        from dash import chatagent
        with self.assertRaises(ValueError):
            chatagent.validate({"tool": "add_task", "text": "x"}, None, self_mode=True)
        with self.assertRaises(ValueError):
            chatagent.validate({"tool": "status"}, None, self_mode=True)


if __name__ == "__main__":
    unittest.main()
