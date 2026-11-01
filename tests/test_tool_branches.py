import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dash import runledger
import webui
from webapi import common as webapi_common


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ToolBranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "a.txt").write_text("one\n")
        (self.repo / "b.txt").write_text("b\n")
        (self.repo / ".gitignore").write_text("self_improve/\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.old = (webapi_common.ROOT, webui.Service.PREVIEW)
        webapi_common.ROOT = self.repo
        webui.Service.PREVIEW = self.repo / "self_improve" / "preview"
        self.addCleanup(lambda: (setattr(webapi_common, "ROOT", self.old[0]), setattr(webui.Service, "PREVIEW", self.old[1])))
        self.svc = webui.Service(Path(self.tmp.name) / "projects")

    def branch(self, name, fname, text):
        git(self.repo, "checkout", "-qb", name)
        (self.repo / fname).write_text(text)
        git(self.repo, "commit", "-qam", "change")
        git(self.repo, "checkout", "-q", "main")

    def test_status_ahead_behind_and_conflict(self):
        self.branch("self-improve/20260101-000001", "b.txt", "branch\n")
        self.branch("self-improve/20260101-000002", "a.txt", "conflicting\n")
        (self.repo / "a.txt").write_text("main moved\n")
        git(self.repo, "commit", "-qam", "main moved")
        st = self.svc.branch_status("self-improve/20260101-000001")
        self.assertEqual((st["ahead"], st["behind"], st["merged"], st["mergeable"]), (1, 1, False, True))
        self.assertIs(self.svc.branch_status("self-improve/20260101-000002")["mergeable"], False)
        git(self.repo, "merge", "-q", "--no-edit", "self-improve/20260101-000001")
        self.assertTrue(self.svc.branch_status("self-improve/20260101-000001")["merged"])

    def test_update_branch_merges_main_in_and_reports_conflicts(self):
        self.branch("self-improve/20260101-000001", "b.txt", "branch\n")
        self.branch("self-improve/20260101-000002", "a.txt", "conflicting\n")
        (self.repo / "a.txt").write_text("main moved\n")
        git(self.repo, "commit", "-qam", "main moved")
        self.svc.tool_update_branch({"branch": "self-improve/20260101-000001"})
        self.assertEqual(self.svc.branch_status("self-improve/20260101-000001")["behind"], 0)
        before = git(self.repo, "rev-parse", "self-improve/20260101-000002")
        with self.assertRaises(webui.UiError) as cm:
            self.svc.tool_update_branch({"branch": "self-improve/20260101-000002"})
        self.assertIn("a.txt", str(cm.exception))
        self.assertEqual(git(self.repo, "rev-parse", "self-improve/20260101-000002"), before)
        self.assertFalse((self.repo / "self_improve" / "update-wt").exists())

    def test_discard(self):
        self.branch("self-improve/20260101-000001", "b.txt", "x\n")
        self.svc.tool_discard({"branch": "self-improve/20260101-000001"})
        self.assertEqual(git(self.repo, "branch", "--list", "self-improve/*"), "")
        with self.assertRaises(webui.UiError):
            self.svc.tool_discard({"branch": "main"})

    def test_cancel_cleans_up_unfinished_run(self):
        out = self.repo / "self_improve"
        led = runledger.Ledger(out, "20260101-000009", "directed", "do it")
        led.status("working")
        work = out / "work" / "20260101-000009"
        git(self.repo, "worktree", "add", "-q", "-b", "self-improve/20260101-000009", str(work), "HEAD")
        webui.PROCS["tool"] = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.addCleanup(lambda: webui.PROCS.pop("tool", None))
        res = self.svc.tool_cancel()
        self.assertTrue(res["ok"])
        self.assertFalse(work.exists())
        self.assertEqual(git(self.repo, "branch", "--list", "self-improve/*"), "")
        self.assertEqual(runledger.load_run(out, "20260101-000009")["status"], "cancelled")
        with self.assertRaises(webui.UiError):
            self.svc.tool_cancel()


if __name__ == "__main__":
    unittest.main()
