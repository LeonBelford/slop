import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import runplan  # noqa: E402
from dash import sandbox  # noqa: E402

WEB_STACK = {"label": "Python / FastAPI", "run": ".venv/bin/uvicorn app.main:app", "url": "http://127.0.0.1:8000", "commands": {"test": "pytest"}}


def make(files: dict, brief: dict | None = None):
    d = tempfile.TemporaryDirectory()
    repo = Path(d.name) / "repo"
    for k, v in files.items():
        (repo / k).parent.mkdir(parents=True, exist_ok=True)
        (repo / k).write_text(v, "utf-8")
    info = Path(d.name) / "interface.json"
    info.write_text(json.dumps(brief or {}), "utf-8")
    return d, repo, info


class RunPlanTests(unittest.TestCase):
    def test_python_cli_gets_its_own_command_and_readme_examples(self):
        d, repo, info = make({"tool/__main__.py": "import argparse\n", "tool/__init__.py": "",
                              "README.md": "# tool\n```bash\n$ tool add milk\ntool list --all\ncurl x | sh\n```\n"}, {"interface": "cli"})
        with d:
            p = runplan.plan(repo, info, WEB_STACK)
        self.assertEqual(p["mode"], "cli")
        self.assertEqual(p["run"], "python3 -m tool")
        self.assertEqual(p["url"], "")
        self.assertEqual(p["examples"], ["--help", "add milk", "list --all"])

    def test_console_script_from_pyproject(self):
        d, repo, info = make({"pyproject.toml": '[project.scripts]\ntodo = "todoapp.cli:main"\n'}, {"interface": "cli"})
        with d:
            p = runplan.plan(repo, info, WEB_STACK)
        self.assertEqual((p["run"], p["name"]), ("python3 -m todoapp.cli", "todo"))

    def test_node_bin_and_rust_and_go(self):
        d, repo, info = make({"package.json": '{"name": "t", "bin": {"t": "src/cli.js"}}'}, {"interface": "cli"})
        with d:
            self.assertEqual(runplan.plan(repo, info, WEB_STACK)["run"], "node src/cli.js")
        d, repo, info = make({"Cargo.toml": "[package]\n"}, {"interface": "cli"})
        with d:
            self.assertEqual(runplan.plan(repo, info, WEB_STACK)["run"], "cargo run --quiet --")
        d, repo, info = make({"go.mod": "module x\n"}, {"interface": "cli"})
        with d:
            self.assertEqual(runplan.plan(repo, info, WEB_STACK)["run"], "go run .")

    def test_cli_without_entry_says_so_instead_of_starting_a_server(self):
        d, repo, info = make({"README.md": "x"}, {"interface": "cli"})
        with d:
            p = runplan.plan(repo, info, WEB_STACK)
        self.assertEqual((p["mode"], p["run"], p["url"]), ("cli", "", ""))

    def test_web_keeps_server_and_library_uses_tests(self):
        d, repo, info = make({"app/main.py": "from fastapi import FastAPI\n"}, {"interface": "web"})
        with d:
            p = runplan.plan(repo, info, WEB_STACK)
        self.assertEqual((p["mode"], p["url"]), ("web", "http://127.0.0.1:8000"))
        d, repo, info = make({"lib.py": "x=1\n"}, {"interface": "library"})
        with d:
            p = runplan.plan(repo, info, WEB_STACK)
        self.assertEqual((p["mode"], p["run"]), ("library", "pytest"))

    def test_unknown_interface_is_guessed_from_the_sources(self):
        d, repo, info = make({"main.py": "import argparse\nif __name__ == '__main__':\n    pass\n"})
        with d:
            self.assertEqual(runplan.plan(repo, info, WEB_STACK)["mode"], "cli")
        d, repo, info = make({"server.py": "import flask\n"})
        with d:
            self.assertEqual(runplan.plan(repo, info, WEB_STACK)["mode"], "web")


class SandboxMainTests(unittest.TestCase):
    def test_sandbox_shows_main_while_the_agent_is_on_a_task_branch(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            repo = ws / "repo"
            repo.mkdir()

            def git(*a):
                subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=t", *a], check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            (repo / "f.txt").write_text("merged", "utf-8")
            git("add", "-A")
            git("commit", "-qm", "m")
            git("checkout", "-q", "-b", "task")
            (repo / "f.txt").write_text("half written", "utf-8")
            (repo / "new.txt").write_text("x", "utf-8")
            sb = sandbox.sync(ws)
            self.assertEqual((sb / "f.txt").read_text("utf-8"), "merged")
            self.assertFalse((sb / "new.txt").exists())
            git("checkout", "-q", "-f", "main")
            self.assertEqual((sandbox.sync(ws) / "f.txt").read_text("utf-8"), "merged")


if __name__ == "__main__":
    unittest.main()
