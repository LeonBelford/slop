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
        changes = ["M\twebui.py", "M\twebaccess.py"]
        bad = selfimprove.protected_violations(changes, selfimprove.PROTECTED_DIRECTED)
        self.assertEqual(len(bad), 1)
        self.assertIn("webaccess.py", bad[0])

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
