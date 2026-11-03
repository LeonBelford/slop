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


class MissingDepTests(unittest.TestCase):
    def test_missing_module_gets_dependency_advice(self):
        rep = "[test] FAILED rc=2\nE   ModuleNotFoundError: No module named 'httpx'"
        self.assertEqual(failures.classify(rep), "missing_dep")
        self.assertIn("requirements.txt", failures.ADVICE["missing_dep"])


class EditFormatTests(unittest.TestCase):
    def test_default_task_time_limit_is_tighter_than_two_hours(self):
        from core.cfutil import DEFAULT_CONFIG
        self.assertLessEqual(DEFAULT_CONFIG["limits"]["max_task_minutes"], 60)


class WholeFirstTests(unittest.TestCase):
    def _agent(self, tmp):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from core.cfa_build import BuildMixin

        class A(BuildMixin):
            pass
        a = A()
        (tmp / "run").mkdir()
        (tmp / "x.py").write_text("a = 1\n")
        a.coder_mock, a.repo, a.dir, a.run_dir, a.state, a.calls, a.logs = None, tmp, tmp, tmp / "run", {}, [], []
        a.lim, a.log, a.note, a.save_state = {"coder_timeout": 5}, a.logs.append, lambda *x: None, lambda: None
        a.handoff_text, a.coder_for, a.feat, a.journal_file = lambda: "", lambda t: "ollama_chat/x", lambda n: False, lambda: []
        a.work_fingerprint = lambda files: (tmp / "x.py").read_text()
        a.llm = SimpleNamespace(sched=SimpleNamespace(gpu=lambda *x: nullcontext()), record=lambda *x: None)
        return a

    def test_model_that_only_writes_in_whole_mode_starts_with_it_after_two_wins(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            a = self._agent(Path(d))

            def fake(cmd, cwd, timeout, live, **kw):
                a.calls.append("whole" if "whole" in cmd else "diff")
                if "whole" in cmd:
                    (Path(d) / "x.py").write_text(f"a = {len(a.calls)}\n")
                return 0, "ok"
            with mock.patch("core.cfa_build.sh_live", fake), mock.patch("core.cfa_build.perf.fit_context", lambda e, r, p, repo: (e, r, [])):
                for _ in range(3):
                    a.code("implement", ["x.py"], [], "do it")
        self.assertEqual(a.calls, ["diff", "whole", "diff", "whole", "whole"])
