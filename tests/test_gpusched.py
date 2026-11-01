import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gpusched  # noqa: E402
import codefactory as cf  # noqa: E402


def make(d, prio, idle=0.0):
    return gpusched.Scheduler("t", prio, idle, lambda m: None, directory=Path(d))


class SchedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_side_runs_freely_without_main(self):
        side = make(self.d, "side")
        with side.gpu("x"):
            side.check()

    def test_main_busy_preempts_side(self):
        main, side = make(self.d, "main"), make(self.d, "side")
        with side.gpu("x"):
            side._last_check = 0
            side.check()  # main not registered: fine
            with main.gpu("y"):
                side._last_check = 0
                with self.assertRaises(gpusched.Preempted):
                    side.check()

    def test_side_waits_for_idle_main_then_one_side_at_a_time(self):
        main, side, side2 = make(self.d, "main"), make(self.d, "side", 0.0), make(self.d, "side", 0.0)
        started = threading.Event()

        def run():
            with side.gpu("a"):
                started.set()

        with main.gpu("busy"):
            t = threading.Thread(target=run)
            t.start()
            self.assertFalse(started.wait(1.5))  # blocked while main works
        t.join(5)
        self.assertTrue(started.is_set())
        with side.gpu("a"):
            got = threading.Event()
            t2 = threading.Thread(target=lambda: (side2.gpu("b").__enter__(), got.set()), daemon=True)
            t2.start()
            self.assertFalse(got.wait(1.5))  # second side agent has to wait for the first

    def test_min_idle_respected_and_dead_main_ignored(self):
        main = make(self.d, "main")
        with main.gpu("y"):
            pass
        busy, idle = gpusched.main_state(Path(self.d))
        self.assertFalse(busy)
        self.assertLess(idle, 5)
        (Path(self.d) / f"{main.me.stem}.json").write_text(json.dumps({"pid": 2 ** 22 + 5, "prio": "main", "busy": True, "ts": time.time(), "since": 0}))
        self.assertEqual(gpusched.main_state(Path(self.d)), (False, float("inf")))

    def test_sh_live_stops_preempted_side_process(self):
        main, side = make(self.d, "main"), make(self.d, "side")
        with tempfile.TemporaryDirectory() as w:
            with side.gpu("coder") as gate:
                threading.Timer(1.0, lambda: main._main_enter()).start()
                rc, out = cf.sh_live(["sleep", "30"], Path(w), 60, Path(w) / "l.txt", gate=gate)
            self.assertEqual(rc, gpusched.PREEMPTED_RC)
            main._main_leave()

    def test_llm_chat_side_aborts_and_retries(self):
        import http.server
        calls = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(time.time())
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for i in range(12):
                        self.wfile.write(b'data: {"choices":[{"delta":{"content":"ab"}}]}\n\n')
                        self.wfile.flush()
                        time.sleep(0.25)
                    self.wfile.write(b"data: [DONE]\n\n")
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cfg = json.loads(json.dumps(cf.DEFAULT_CONFIG))
        cfg["llm_api"] = f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"
        llm = cf.LLM(cfg, lambda m: None)
        llm.sched = make(self.d, "side")
        main = make(self.d, "main")
        threading.Timer(0.8, main._main_enter).start()
        threading.Timer(2.5, main._main_leave).start()
        out = llm.chat("t", "planner", "s", "u")
        srv.shutdown()
        self.assertEqual(out, "ab" * 12)
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreater(calls[-1] - calls[0], 1.5)  # the retry only started after main became idle


if __name__ == "__main__":
    unittest.main()
