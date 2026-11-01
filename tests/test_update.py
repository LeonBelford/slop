import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from infra import update  # noqa: E402


def g(cwd, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)


class UpdateTests(unittest.TestCase):
    def test_behind_then_apply(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            g(t, "init", "-q", "--bare", "-b", "main", "remote.git")
            g(t, "clone", "-q", "remote.git", "a")
            (t / "a" / "f.txt").write_text("1")
            g(t / "a", "add", "-A")
            g(t / "a", "commit", "-qm", "one")
            g(t / "a", "push", "-q", "origin", "HEAD:main")
            g(t, "clone", "-q", "remote.git", "b")
            self.assertEqual(update.status(t / "b", True)["state"], "current")
            (t / "a" / "f.txt").write_text("2")
            g(t / "a", "commit", "-qam", "two")
            g(t / "a", "push", "-q", "origin", "HEAD:main")
            self.assertEqual(update.status(t / "b", True)["state"], "behind")
            update.apply(t / "b")
            self.assertEqual((t / "b" / "f.txt").read_text(), "2")

    def test_edited_master_md_does_not_block_update(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            g(t, "init", "-q", "--bare", "-b", "main", "remote.git")
            g(t, "clone", "-q", "remote.git", "a")
            (t / "a" / "MASTER.md").write_text("template")
            (t / "a" / "f.txt").write_text("1")
            g(t / "a", "add", "-A")
            g(t / "a", "commit", "-qm", "one")
            g(t / "a", "push", "-q", "origin", "HEAD:main")
            g(t, "clone", "-q", "remote.git", "b")
            (t / "b" / "MASTER.md").write_text("my master prompt")
            g(t / "a", "rm", "-q", "--cached", "MASTER.md")
            (t / "a" / "f.txt").write_text("2")
            g(t / "a", "commit", "-qam", "untrack master")
            g(t / "a", "push", "-q", "origin", "HEAD:main")
            self.assertEqual(update.status(t / "b", True)["dirty"], [])
            res = update.apply(t / "b")
            self.assertTrue(res["updated"], res)
            self.assertEqual((t / "b" / "f.txt").read_text(), "2")
            self.assertEqual((t / "b" / "MASTER.md").read_text(), "my master prompt")

    def test_feature_branch_still_updates_from_main(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            g(t, "init", "-q", "--bare", "-b", "main", "remote.git")
            g(t, "clone", "-q", "remote.git", "a")
            (t / "a" / "f.txt").write_text("1")
            g(t / "a", "add", "-A")
            g(t / "a", "commit", "-qm", "one")
            g(t / "a", "push", "-q", "origin", "HEAD:main")
            g(t, "clone", "-q", "remote.git", "b")
            g(t / "b", "checkout", "-q", "-b", "feature")
            g(t / "b", "push", "-q", "-u", "origin", "feature")
            (t / "a" / "f.txt").write_text("2")
            g(t / "a", "commit", "-qam", "two")
            g(t / "a", "push", "-q", "origin", "HEAD:main")
            st = update.status(t / "b", True)
            self.assertEqual((st["state"], st["upstream"]), ("behind", "origin/main"))
            res = update.apply(t / "b")
            self.assertTrue(res["updated"], res)
            self.assertEqual((t / "b" / "f.txt").read_text(), "2")


class SmokeImportTests(unittest.TestCase):
    def test_old_and_new_module_names_import(self):
        for names in ("codefactory, webui, selfimprove, infra.security, dash.chat, infra.knowledge", "codefactory, webui, security, chat, knowledge"):
            r = subprocess.run([sys.executable, "-c", "import " + names], cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
