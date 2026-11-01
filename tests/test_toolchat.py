import subprocess
import tempfile
import unittest
from pathlib import Path

from dash import chat as chatlib, toolchat


def git_repo(root: Path) -> None:
    (root / "core").mkdir()
    (root / "core" / "a.py").write_text("print('hi')\n")
    (root / "PROJECT_MAP.md").write_text("# Map\ncore/a.py does things\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)


class ToolChatTests(unittest.TestCase):
    def test_extract_tasks(self):
        text = "Plan:\n```task\nAdd a make target that prints status.\n```\nand ```task\nx\n```"
        self.assertEqual(toolchat.extract_tasks(text), ["Add a make target that prints status."])
        self.assertEqual(toolchat.extract_tasks("no block"), [])

    def test_context_has_map_state_and_mentioned_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            git_repo(root)
            ctx = toolchat.context(root, root / "self_improve", True, "why does core/a.py print hi? also /etc/passwd.md")
            self.assertIn("does things", ctx)
            self.assertIn("RUNNING", ctx)
            self.assertIn("File `core/a.py`", ctx)
            self.assertNotIn("passwd", ctx.split("File")[-1])
            self.assertEqual([p.name for p in toolchat.mentioned_files(root, "see a.py")], ["a.py"])
            self.assertEqual(toolchat.mentioned_files(root, "../../etc/shadow.md"), [])

    def test_tool_chat_is_single_and_uses_tool_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            store = chatlib.ChatStore(Path(d), lambda n: False)
            a, b = store.tool_chat("m1"), store.tool_chat("m2")
            self.assertEqual(a["id"], b["id"])
            self.assertTrue(a["tool"])
            self.assertTrue(store.list()["chats"][0]["tool"])
            a["messages"].append({"role": "user", "content": "hello"})
            msgs = chatlib.build_messages(a, lambda n: None, tool_ctx=lambda last: "CTX:" + last)
            self.assertIn("```task", msgs[0]["content"])
            self.assertIn("CTX:hello", msgs[0]["content"])


if __name__ == "__main__":
    unittest.main()
