import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codefactory as sa  # noqa: E402
import webui  # noqa: E402


class LiveRateTests(unittest.TestCase):
    def test_idle_without_stream(self):
        self.assertEqual(webui.Service.live_rate(None)["tps"], 0.0)
        self.assertFalse(webui.Service.live_rate(None)["active"])

    def test_tokens_per_second_while_streaming(self):
        r = webui.Service.live_rate({"tokens": 100, "chars": 400, "t0": 1000.0, "updated": 1010.0, "role": "coder"}, now_t=1011.0)
        self.assertEqual(r["tps"], 10.0)
        self.assertTrue(r["streaming"])

    def test_rate_drops_to_zero_when_stream_is_quiet(self):
        r = webui.Service.live_rate({"tokens": 100, "chars": 400, "t0": 1000.0, "updated": 1010.0}, now_t=1020.0)
        self.assertEqual(r["tps"], 0.0)
        self.assertTrue(r["active"])


class DashboardTests(unittest.TestCase):
    def test_dashboard_reports_running_agent_and_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "demo"
            (ws / "agent").mkdir(parents=True)
            (ws / "agent" / "GOAL.md").write_text("g")
            sa.write_json(ws / "agent" / "metrics.json", {"gen_seconds": 30, "wall_seconds": 90, "calls": 3, "tokens": 900, "chars": 3600, "day_calls": 1, "day_tokens": 300, "day_chars": 1200})
            svc = webui.Service(Path(tmp))
            webui.Service._gpu = (time.time(), None)
            svc.loaded_models = staticmethod(lambda: [])
            d = svc.dashboard()
            self.assertEqual(d["agents_running"], 0)
            self.assertEqual(d["totals"]["tokens"], 900)
            self.assertEqual(d["projects"][0]["name"], "demo")
            self.assertEqual(d["projects"][0]["perf"]["tps"], 30.0)
            self.assertEqual(d["projects"][0]["perf"]["tokens_per_min"], 600)
            json.dumps(d)


if __name__ == "__main__":
    unittest.main()


class CompactTests(unittest.TestCase):
    def test_old_thoughts_are_archived_compressed_and_seq_survives(self):
        import gzip
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "thoughts.jsonl"
            f.write_text("".join(json.dumps({"n": i, "prompt": "p" * 5000, "thinking": "t", "answer": "a"}) + "\n" for i in range(1, 401)))
            self.assertTrue(sa.compact_thoughts(f, keep=100))
            self.assertEqual(len(f.read_text().splitlines()), 100)
            self.assertEqual(sa.last_seq(f), 400)
            arch = list((Path(tmp) / "thoughts-archive").glob("*.gz"))
            rows = [json.loads(x) for x in gzip.open(arch[0], "rt").read().splitlines()]
            self.assertEqual((len(rows), len(rows[0]["prompt"])), (300, 1500))

    def test_aider_token_lines(self):
        self.assertEqual(sa.aider_tokens("Tokens: 1.2k sent, 345 received.\nTokens: 8,432 sent, 1,023 received. Cost"), (9632, 1368))


class PreviewTests(unittest.TestCase):
    def test_preview_rejects_unknown_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = webui.Service(Path(tmp))
            for bad in ("main", "../x", "self-improve/19700101-000000"):
                with self.assertRaises(webui.UiError):
                    svc.preview_start({"branch": bad})
            self.assertFalse(svc.preview_state()["running"])


class MergeTests(unittest.TestCase):
    def test_merge_and_undo_in_a_scratch_repo(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "r"
            repo.mkdir()
            run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, check=True, capture_output=True)
            run("init", "-q", "-b", "main")
            (repo / "a.txt").write_text("1")
            (repo / ".gitignore").write_text("self_improve/\n")
            run("add", "-A")
            run("commit", "-qm", "base")
            run("checkout", "-qb", "self-improve/20300101-000000")
            (repo / "b.txt").write_text("2")
            run("add", "-A")
            run("commit", "-qm", "feat")
            run("checkout", "-q", "main")
            old_root = webui.ROOT
            webui.ROOT = repo
            try:
                env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
                import os
                os.environ.update(env)
                svc = webui.Service(Path(tmp) / "projects")
                r = svc.tool_merge({"branch": "self-improve/20300101-000000"})
                self.assertTrue((repo / "b.txt").exists())
                self.assertTrue(r["tag"].startswith("pre-merge-"))
                svc.tool_undo_merge()
                self.assertFalse((repo / "b.txt").exists())
            finally:
                webui.ROOT = old_root


class IdleTimeoutTests(unittest.TestCase):
    def test_idle_timeout_only_counts_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "l.txt"
            cmd = ["python3", "-u", "-c", "import time\nfor i in range(5):\n print(i);time.sleep(0.8)"]
            rc, _ = sa.sh_live(cmd, Path(tmp), 3, live)  # absolute: 4 s of work > 3 s
            self.assertEqual(rc, 124)
            rc, out = sa.sh_live(cmd, Path(tmp), 3, live, idle=True)  # chatty: never silent for 3 s
            self.assertEqual(rc, 0)
            rc, _ = sa.sh_live(["python3", "-c", "import time;time.sleep(9)"], Path(tmp), 2, live, idle=True)
            self.assertEqual(rc, 124)
