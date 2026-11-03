import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import cfa_verify  # noqa: E402


class RustSpeedTests(unittest.TestCase):
    def test_cargo_build_becomes_a_type_check_and_cargo_gets_fast_env(self):
        self.assertEqual(cfa_verify.quick_build("cargo build"), "cargo check --all-targets --quiet")
        self.assertEqual(cfa_verify.quick_build("npm run build"), "npm run build")
        self.assertEqual(cfa_verify.cmd_env("cargo test")["CARGO_PROFILE_DEV_DEBUG"], "0")
        self.assertEqual(cfa_verify.cmd_env("pytest"), {})
