import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codefactory as sa  # noqa: E402
import selfimprove  # noqa: E402


class SelfImproveTest(unittest.TestCase):
    def test_protected_paths(self):
        self.assertEqual(selfimprove.protected_violations(["M\twebaccess.py", "M\ttests/test_x.py", "A\ttests/test_new.py",
                                                           "M\tcodefactory.py"]), ["webaccess.py", "tests/test_x.py"])

    def test_branch_keeps_green_changes_and_reverts_protected(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp / "repo"
            repo.mkdir()
            for f in ROOT.glob("*.py"):
                shutil.copy(f, repo / f.name)
            shutil.copytree(ROOT / "tests", repo / "tests", ignore=shutil.ignore_patterns("test_selfimprove.py", "__pycache__"))
            shutil.copytree(ROOT / "scripts", repo / "scripts")
            shutil.copy(ROOT / "MASTER.md", repo / "MASTER.md")
            shutil.copy(ROOT / ".gitignore", repo / ".gitignore")
            (repo / "web").mkdir()
            shutil.copy(ROOT / "web" / "index.html", repo / "web" / "index.html")
            for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
                subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
            ws = tmp / "projects" / "demo"
            (ws / "agent").mkdir(parents=True)
            sa.write_text(ws / "agent" / "GOAL.md", "goal")
            sa.write_text(ws / "agent" / "tool_ideas.jsonl", json.dumps({"idea": "review prompt too lenient"}) + "\n")

            def llm_json(tag, role, system, user):
                return json.dumps({"proposals": [
                    {"title": "Good", "rationale": "r", "files": ["codefactory.py"], "change": "add comment"},
                    {"title": "Evil", "rationale": "r", "files": ["codefactory.py"], "change": "weaken guard"}]})

            def coder(work, files, prompt):
                if prompt.startswith("Documentation update"):
                    with open(work / "README.md", "a") as f:
                        f.write("\nSelf-improved.\n")
                    return
                if "weaken" in prompt:
                    (work / "webaccess.py").write_text("# weakened\n")
                else:
                    with open(work / "codefactory.py", "a") as f:
                        f.write("\n# improved\n")

            sa.LLM.mock = llm_json
            try:
                res = selfimprove.main(tmp / "projects", repo=repo, coder=coder, log=lambda m: None)
            finally:
                sa.LLM.mock = None
            self.assertEqual(res["kept"], ["Good"])
            diff = subprocess.run(["git", "diff", "--name-only", f"HEAD...{res['branch']}"], cwd=repo, capture_output=True, text=True).stdout
            self.assertEqual(sorted(diff.split()), ["README.md", "codefactory.py", "docs/SELF_IMPROVEMENTS.md"])
            self.assertTrue((repo / "self_improve").glob("proposals-*.md"))
            tags = subprocess.run(["git", "tag", "-l", "backup/pre-self-improve-*"], cwd=repo, capture_output=True, text=True).stdout
            self.assertTrue(tags.strip())
            self.assertTrue(list((repo / "self_improve" / "backups").glob("*.bundle")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
