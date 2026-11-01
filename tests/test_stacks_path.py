import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stacks  # noqa: E402


class ToolDetection(unittest.TestCase):
    def test_cargo_in_home_is_found_without_profile_path(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = Path(home) / ".cargo" / "bin"
            bin_dir.mkdir(parents=True)
            exe = bin_dir / "cargo"
            exe.write_text("#!/bin/sh\n")
            exe.chmod(0o755)
            env = {"PATH": "/usr/bin:/bin", "HOME": home}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(Path, "home", return_value=Path(home)):
                self.assertNotIn(str(bin_dir), os.environ["PATH"])
                missing = [m["tool"] for m in stacks.missing_tools("rust")]
                self.assertNotIn("cargo", missing)
                self.assertIn(str(bin_dir), os.environ["PATH"])


if __name__ == "__main__":
    unittest.main()
