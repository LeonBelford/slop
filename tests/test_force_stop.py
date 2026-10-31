import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codefactory as sa  # noqa: E402


class ForceStopTest(unittest.TestCase):
    def test_kills_agent_and_children_mid_task(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "p"
            (ws / "agent").mkdir(parents=True)
            (ws / "repo").mkdir()
            proc = subprocess.Popen(["bash", "-c", "sleep 300 & sleep 300"], start_new_session=True)
            time.sleep(0.3)
            sa.write_text(ws / "agent" / "agent.pid", str(proc.pid))
            msg = sa.Agent(ws).force_stop()
            proc.wait(timeout=5)
            self.assertIn("Force-stopped", msg)
            self.assertFalse((ws / "agent" / "agent.pid").exists())
            self.assertTrue((ws / "agent" / "STOP").exists())
            self.assertNotEqual(proc.returncode, None)

    def test_no_running_agent(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "p"
            (ws / "agent").mkdir(parents=True)
            (ws / "repo").mkdir()
            self.assertIn("No running agent", sa.Agent(ws).force_stop())


if __name__ == "__main__":
    unittest.main()
