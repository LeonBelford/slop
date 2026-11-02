import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import webui
from webapi.common import UiError


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "demo"
        (ws / "agent").mkdir(parents=True)
        (ws / "agent" / "GOAL.md").write_text("Build a demo")
        self.repo = ws / "repo"
        self.repo.mkdir()
        (self.repo / "app.py").write_text("def f():\n    return 1\n")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run(["git", *c], cwd=self.repo, check=True, capture_output=True)
        self.svc = webui.Service(Path(self.tmp.name))
        self.svc._running = lambda ws, name: False
        self.chat = self.svc.chat_create({"projects": ["demo"], "model": "m", "agent": True, "route": "direct"})

    def tearDown(self):
        self.tmp.cleanup()

    def card(self):
        block = '```action\n' + json.dumps({"tool": "edit_file", "project": "demo", "path": "app.py", "old": "return 1", "new": "return 2"}) + '\n```'
        c = self.svc.chats.load(self.chat["id"])
        c["messages"].append({"role": "assistant", "content": block, "t": 1})
        self.svc.chats.save(c)
        return len(c["messages"]) - 1

    def test_preview_run_show_and_undo(self):
        i = self.card()
        cp = lambda op: self.svc.chat_checkpoint(self.chat["id"], {"msg": i, "k": 0, "op": op})  # noqa: E731
        self.assertIn("+    return 2", cp("preview")["diff"])
        self.assertIn("return 1", (self.repo / "app.py").read_text())
        r = self.svc.chat_action(self.chat["id"], {"msg": i, "k": 0})
        self.assertTrue(r["ok"], r["result"])
        self.assertIn("return 2", (self.repo / "app.py").read_text())
        self.assertTrue(r["chat"]["messages"][i]["acts"]["0"]["sha"])
        self.assertIn("-    return 1", cp("show")["diff"])
        cp("undo")
        self.assertIn("return 1", (self.repo / "app.py").read_text())
        with self.assertRaises(UiError):
            cp("undo")


if __name__ == "__main__":
    unittest.main()
