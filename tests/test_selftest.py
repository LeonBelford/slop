"""Führt den eingebauten Mechanik-Test aus (Stub-LLM, kein Ollama nötig)."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SelftestTest(unittest.TestCase):
    def test_selftest_passes(self):
        p = subprocess.run([sys.executable, "saas_agent.py", "selftest"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("SELFTEST OK", p.stdout)


if __name__ == "__main__":
    unittest.main()
