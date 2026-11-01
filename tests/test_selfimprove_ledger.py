import http.server
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import codefactory as sa
from dash import runledger
import selfimprove


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "web").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "app.py").write_text("X = 1\n")
    (repo / "web" / "index.html").write_text("<html>v1</html>")
    (repo / ".gitignore").write_text("self_improve/\n__pycache__/\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


class LedgerTests(unittest.TestCase):
    def test_ledger_stages_and_listing(self):
        with tempfile.TemporaryDirectory() as d:
            led = runledger.Ledger(Path(d), "20260101-000000", "directed", "Add a thing\nand another")
            led.set_requirements(["a", "b", " "])
            i = led.add_proposal("P")
            led.stage(i, "test", "failed", "boom")
            led.mark_requirement(1, "done", "app.py")
            led.finish("done", branch="self-improve/x")
            row = runledger.list_runs(Path(d))[0]
            self.assertEqual((row["done"], row["total"], row["title"], row["branch"]), (1, 2, "Add a thing", "self-improve/x"))
            run = runledger.load_run(Path(d), "20260101-000000")
            self.assertEqual(run["proposals"][0]["note"], "boom")
            self.assertIsNone(runledger.load_run(Path(d), "../etc/passwd"))
            self.assertEqual(runledger.run_for_branch(Path(d), "self-improve/x")["id"], "20260101-000000")

    def test_directed_run_records_plan_requirements_and_extra_tests_round(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            prompts = []

            def llm(tag, role, system, user):
                if tag == "tool_requirements":
                    return json.dumps({"requirements": ["change X", "show it in the UI"]})
                if tag == "tool_verify":
                    return json.dumps({"results": [{"id": 1, "status": "done", "evidence": "app.py"}, {"id": 2, "status": "partial", "evidence": "no UI"}]})
                if tag == "tool_files":
                    return json.dumps({"files": ["app.py"]})
                return "{}"

            def coder(work, files, prompt):
                prompts.append(prompt)
                if prompt.startswith("Add tests"):
                    (work / "tests" / "test_x.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test(self): self.assertTrue(True)\n")
                elif prompt.startswith("Documentation update"):
                    return
                else:
                    (work / "app.py").write_text("X = 2\n")

            sa.LLM.mock = llm
            real = selfimprove._tests_green
            selfimprove._tests_green = lambda work, t: (True, "ok")
            try:
                res = selfimprove.directed("Change X to 2 and show it in the UI", repo=repo, coder=coder, log=lambda m: None)
            finally:
                sa.LLM.mock = None
                selfimprove._tests_green = real
            self.assertTrue(res["branch"])
            run = runledger.load_run(repo / "self_improve", res["branch"].split("/")[-1])
            self.assertEqual(run["status"], "done")
            self.assertEqual([r["status"] for r in run["requirements"]], ["done", "partial"])
            self.assertEqual(run["tests_added"], ["tests/test_x.py"])
            st = run["proposals"][0]["stages"]
            self.assertEqual((st["implement"], st["tests_added"], st["security"], st["test"]), ("ok", "ok", "ok", "ok"))
            self.assertTrue(any(p.startswith("Add tests") for p in prompts))

    def test_failed_run_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            sa.LLM.mock = lambda *a: "{}"
            try:
                res = selfimprove.directed("Do something that changes nothing", repo=repo, coder=lambda w, f, p: None, log=lambda m: None)
            finally:
                sa.LLM.mock = None
            self.assertIsNone(res["branch"])
            run = runledger.list_runs(repo / "self_improve")[0]
            self.assertEqual(run["status"], "failed")


class VerifyCheckoutTests(unittest.TestCase):
    def serve(self, sha, page):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/build":
                    body = json.dumps({"sha": sha}).encode()
                elif self.path == "/":
                    body = page
                elif self.path.startswith("/api/"):
                    body = b"{}"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_verifies_right_and_wrong_preview(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            git(repo, "checkout", "-qb", "self-improve/t")
            (repo / "app.py").write_text('X = 2\nROUTE = "/api/newthing"\n')
            (repo / "web" / "index.html").write_text("<html>v2</html>")
            git(repo, "commit", "-qam", "change")
            tip = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "-")
            tree = Path(d) / "tree"
            git(repo, "worktree", "add", "--detach", str(tree), "self-improve/t")
            good = runledger.verify_checkout(repo, tree, "self-improve/t", self.serve(tip, b"<html>v2</html>"), wait=2)
            self.assertTrue(all(c["ok"] for c in good), good)
            self.assertTrue(any("/api/newthing" in c["detail"] for c in good))
            stale = runledger.verify_checkout(repo, tree, "self-improve/t", self.serve("0" * 40, b"<html>v1</html>"), wait=2)
            failed = {c["name"] for c in stale if not c["ok"]}
            self.assertEqual(failed, {"server runs exactly this commit", "served UI is the branch's UI"})
            (tree / "app.py").write_text("X = 3\n")
            tampered = runledger.verify_checkout(repo, tree, "self-improve/t", self.serve(tip, b"<html>v2</html>"), wait=2)
            self.assertFalse(next(c for c in tampered if c["name"].startswith("no stray"))["ok"])

    def test_dead_server_reported(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            git(repo, "branch", "self-improve/t")
            checks = runledger.verify_checkout(repo, repo, "self-improve/t", 1, wait=0.5, alive=lambda: False)
            self.assertFalse(checks[-1]["ok"])
            self.assertEqual(checks[-1]["name"], "preview server answers")


if __name__ == "__main__":
    unittest.main()
