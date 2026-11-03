import sys
import tempfile
import time
import unittest
from pathlib import Path

from core import failures
from core.cfutil import sh_live
from infra import perf


class CoderLoopTests(unittest.TestCase):
    def test_looping_detects_repeats_but_not_normal_output(self):
        loop = ["File app/main.py needs to be added to the chat\n", "Please add it and I will repeat\n"] * 40
        normal = [f"line number {i} of a normal answer\n" for i in range(150)]
        self.assertTrue(perf.looping(loop))
        self.assertFalse(perf.looping(normal))
        self.assertFalse(perf.looping(["short\n"] * 50))

    def test_sh_live_stops_a_looping_command(self):
        code = "import time\nwhile True:\n    print('File xy needs xy and then repeat', flush=True)\n    time.sleep(0.01)\n"
        with tempfile.TemporaryDirectory() as d:
            t0 = time.time()
            rc, out = sh_live([sys.executable, "-c", code], Path(d), 60, Path(d) / "live.txt", idle=True, stuck=perf.looping)
        self.assertEqual(rc, perf.LOOP_RC)
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(failures.classify(out), "coder_loop")
        self.assertIn("coder_loop", failures.ADVICE)
