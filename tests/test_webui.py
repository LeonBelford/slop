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
        changes = ["webui.py", "webaccess.py"]
        bad = selfimprove.protected_violations(changes, selfimprove.PROTECTED_DIRECTED)
        self.assertEqual(len(bad), 1)
        self.assertIn("webaccess.py", bad[0])

    def test_autonomous_blocks_webui(self):
        self.assertTrue(selfimprove.protected_violations(["webui.py"]))


class DiffValidation(unittest.TestCase):
    def test_sha_and_branch_patterns(self):
        self.assertTrue(webui.SHA_RE.fullmatch("abc1234"))
        self.assertFalse(webui.SHA_RE.fullmatch("--output=x"))
        self.assertTrue(webui.BRANCH_RE.fullmatch("self-improve/20260101"))
        self.assertFalse(webui.BRANCH_RE.fullmatch("../x"))


if __name__ == "__main__":
    unittest.main()
