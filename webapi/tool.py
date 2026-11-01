"""Self-improve "tool" API: review branches, previews, merges and the tool's own activity/diff views.

This is CodeFactory improving itself: every change lands on a `self-improve/<ts>` branch, is built and tested in a
preview worktree, and is only merged into `main` once it looks safe (see selfimprove.py for the pipeline itself).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import codefactory as sa
from dash import diagnostics, runledger, throughput, toolmodels
from infra import models

from webapi import common
from webapi.common import (BRANCH_RE, BUILD, EMPTY_TREE, MAX_FILE, PROCS, SHA_RE, UiError, alive, git, git_rc,
                            head_sha, read_live, spawn, tail)


class ToolMixin:
    """Service mixin: self-improve review branches, preview builds, merges and the tool's own activity/diff views."""

    PREVIEW = common.ROOT / "self_improve" / "preview"

    def tool_stream(self) -> dict:
        d, running = common.ROOT / "self_improve", alive("tool")
        return {"stream": read_live(d / "llm_stream.json", running), "llm": read_live(d / "llm_now.json", running),
                "coder_live": tail(d / "coder_live.txt", 400, 120_000) if running else "",
                "cmd_live": tail(d / "cmd_live.txt", 400, 120_000) if running else ""}

    def _base(self, repo: Path, base: str) -> str:
        """Maps the UI's comparison choice to a git revision ('head' = uncommitted work, 'last:N' = last N commits)."""
        if not git(repo, "rev-parse", "--verify", "-q", "HEAD").strip():
            return EMPTY_TREE
        m = re.fullmatch(r"last:(\d{1,3})", base or "")
        if not m:
            return "HEAD"
        n = int(m.group(1))
        return f"HEAD~{n}" if git(repo, "rev-parse", "--verify", "-q", f"HEAD~{n}").strip() else EMPTY_TREE

    def tree(self, name: str, base: str = "head") -> dict:
        """All files of the product repo with what changed relative to the chosen base."""
        repo = self.ws(name) / "repo"
        if not (repo / ".git").exists():
            return {"files": [], "base": base}
        rev = self._base(repo, base)
        names = git(repo, "ls-files", "-z", "-c", "-o", "--exclude-standard", limit=4_000_000).split("\0")
        files = {n: {"path": n, "status": "", "add": 0, "del": 0} for n in names if n and not n.startswith(".git/")}
        for line in git(repo, "diff", "--numstat", "--no-renames", rev, limit=4_000_000).splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] in files:
                f = files[parts[2]]
                f["add"] = int(parts[0]) if parts[0].isdigit() else 0
                f["del"] = int(parts[1]) if parts[1].isdigit() else 0
                f["status"] = "M"
        tracked = set(git(repo, "ls-files", "-z", limit=4_000_000).split("\0"))
        for line in git(repo, "diff", "--name-status", "--no-renames", rev, limit=4_000_000).splitlines():
            st, _, path = line.partition("\t")
            if path in files and st.startswith("A"):
                files[path]["status"] = "A"
        for path, f in files.items():
            if path not in tracked:
                f["status"] = "A"
                f["add"] = -1  # untracked: the whole file is new
        return {"files": sorted(files.values(), key=lambda f: f["path"])[:5000], "base": base}

    def file(self, name: str, path: str, base: str = "head") -> dict:
        """Contents of one file with the line numbers that are new or changed relative to the base."""
        repo = (self.ws(name) / "repo").resolve()
        target = (repo / path).resolve()
        if not path or target == repo or repo not in target.parents or ".git" in Path(path).parts or not target.is_file():
            raise UiError("File not found")
        size = target.stat().st_size
        data = target.read_bytes()[:MAX_FILE]
        if b"\0" in data[:8000]:
            return {"path": path, "binary": True, "size": size}
        text = data.decode("utf-8", "replace")
        rel = target.relative_to(repo).as_posix()
        tracked = bool(git(repo, "ls-files", "--", rel).strip())
        changed: list[int] = []
        if not tracked:
            changed = list(range(1, text.count("\n") + 2))
        else:
            for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git(repo, "diff", "-U0", "--no-renames", self._base(repo, base), "--", rel), re.M):
                start, cnt = int(m.group(1)), int(m.group(2) or 1)
                changed.extend(range(start, start + cnt))
        return {"path": path, "text": text, "size": size, "truncated": size > MAX_FILE, "changed": changed, "new_file": not tracked}

    def changes(self, name: str) -> dict:
        repo = self.ws(name) / "repo"
        if not (repo / ".git").exists():
            return {"commits": [], "files": [], "uncommitted": []}
        raw = git(repo, "log", "-n", "80", "--numstat", "--format=%x1e%h%x1f%s%x1f%cI")
        commits, totals = [], {}
        for block in raw.split("\x1e")[1:]:
            head, _, stats = block.partition("\n")
            sha, subject, date = (head.split("\x1f") + ["", ""])[:3]
            files = []
            for line in stats.splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    add = int(parts[0]) if parts[0].isdigit() else 0
                    rem = int(parts[1]) if parts[1].isdigit() else 0
                    files.append({"path": parts[2], "add": add, "del": rem})
                    t = totals.setdefault(parts[2], {"path": parts[2], "add": 0, "del": 0, "commits": 0})
                    t["add"] += add
                    t["del"] += rem
                    t["commits"] += 1
            commits.append({"sha": sha, "subject": subject, "date": date, "files": files})
        status = [l for l in git(repo, "status", "--porcelain").splitlines() if l.strip()]
        return {"commits": commits, "files": sorted(totals.values(), key=lambda t: -(t["add"] + t["del"])),
                "uncommitted": status[:100]}

    def diff(self, name: str, sha: str, path: str) -> dict:
        repo = self.ws(name) / "repo"
        if not SHA_RE.match(sha or "") or path.startswith("-") or ".." in Path(path).parts:
            raise UiError("Invalid commit or path")
        args = ["show", "--format=%h %s%n%cI", "--patch", sha]
        return {"diff": git(repo, *args, *(["--", path] if path else []))}

    def tool(self) -> dict:
        """Tasks for the tool itself (loop, UI, Makefile ...), executed on review branches, never merged automatically."""
        branches = []
        for line in git(common.ROOT, "for-each-ref", "refs/heads/self-improve/", "--sort=-creatordate",
                        "--format=%(refname:short)\x1f%(creatordate:iso8601)\x1f%(subject)").splitlines()[:15]:
            name, date, subject = (line.split("\x1f") + ["", ""])[:3]
            stat = git(common.ROOT, "diff", "--stat", f"HEAD...{name}").strip().splitlines()
            branches.append({"name": name, "date": date, "subject": subject, "stat": stat[-12:], **self.branch_status(name)})
        head = head_sha()
        perf = throughput.perf(sa.read_json(common.ROOT / "self_improve" / "metrics.json", {}))
        live = self.live_rate(read_live(common.ROOT / "self_improve" / "llm_stream.json", alive("tool")))
        return {"perf": perf, "live": live, "gpu": self.gpu_stats(), "tool_durations": diagnostics.tool_durations(common.ROOT / "self_improve"),
                "running": alive("tool"), "log": tail(common.ROOT / "self_improve" / "ui.log", 60), "branches": branches, "preview": self.preview_state(),
                "runs": runledger.list_runs(common.ROOT / "self_improve"), "stats": runledger.stats(common.ROOT / "self_improve"), "build": {"sha": BUILD["sha"], "head": head, "restart_needed": bool(BUILD["sha"] and head != BUILD["sha"])},
                "last_merge": sa.read_json(common.ROOT / "self_improve" / "last_merge.json", None)}

    def branch_status(self, name: str) -> dict:
        """How a result branch relates to the current code: commits ahead/behind, merged already, merges cleanly, what was asked."""
        def count(rng: str) -> int:
            out = git(common.ROOT, "rev-list", "--count", rng).strip()
            return int(out) if out.isdigit() else 0

        ahead, behind = count(f"HEAD..{name}"), count(f"{name}..HEAD")
        mergeable = None
        if ahead:
            rc, _ = git_rc(common.ROOT, "merge-tree", "--write-tree", "HEAD", name)
            mergeable = True if rc == 0 else False if rc == 1 else None
        run = runledger.run_for_branch(common.ROOT / "self_improve", name) or {}
        reqs = run.get("requirements", [])
        return {"ahead": ahead, "behind": behind, "merged": ahead == 0, "mergeable": mergeable, "run_id": run.get("id"),
                "req_done": sum(1 for r in reqs if r.get("status") == "done"), "req_total": len(reqs),
                "tests_added": len(run.get("tests_added", [])), "task": (run.get("instruction") or "").strip()[:300]}

    def _check_branch(self, body: dict) -> str:
        branch = str(body.get("branch", ""))
        if not BRANCH_RE.match(branch) or not git(common.ROOT, "rev-parse", "--verify", "-q", branch).strip():
            raise UiError("Unknown branch")
        return branch

    def tool_cancel(self) -> dict:
        """Stops the running self-change and cleans up: worktree removed, unfinished branch deleted, run marked cancelled."""
        if not alive("tool"):
            raise UiError("No self-change is running", 409)
        sa.force_stop_pid(PROCS["tool"].pid, grace=3.0)
        out = common.ROOT / "self_improve"
        for f in ("phase.json", "coder_now.json", "cmd_now.json", "llm_now.json", "llm_stream.json"):
            (out / f).unlink(missing_ok=True)
        run = runledger.active_run(out)
        kept = False
        if run:
            wt = out / "work" / run["id"]
            if wt.exists():
                git_rc(common.ROOT, "worktree", "remove", "--force", str(wt))
                shutil.rmtree(wt, ignore_errors=True)
            git_rc(common.ROOT, "worktree", "prune")
            branch = f"self-improve/{run['id']}"
            if git(common.ROOT, "rev-parse", "--verify", "-q", branch).strip():
                kept = git(common.ROOT, "rev-list", "--count", f"HEAD..{branch}").strip() not in ("", "0")
                if not kept:
                    git_rc(common.ROOT, "branch", "-D", branch)
            runledger.mark_run(out, run["id"], "cancelled", "Cancelled by you" + ("; commits made so far are kept on the branch" if kept else ""))
        with open(out / "ui.log", "a", encoding="utf-8") as f:
            f.write("Cancelled by user.\n")
        return {"ok": True, "kept_branch": kept}

    def tool_discard(self, body: dict) -> dict:
        branch = self._check_branch(body)
        if alive("tool"):
            raise UiError("Wait for the running self-change or cancel it first", 409)
        if sa.read_json(self.PREVIEW.parent / "preview.json", {}).get("branch") == branch:
            self.preview_stop()
        rc, out = git_rc(common.ROOT, "branch", "-D", branch)
        if rc != 0:
            raise UiError(out[-300:])
        run = runledger.run_for_branch(common.ROOT / "self_improve", branch)
        if run:
            runledger.mark_run(common.ROOT / "self_improve", run["id"], "discarded")
        return {"ok": True, "message": f"Deleted {branch}"}

    def tool_update_branch(self, body: dict) -> dict:
        """Merges the current code into a result branch (in a temporary checkout) so it stays mergeable when main moved on."""
        branch = self._check_branch(body)
        if alive("tool"):
            raise UiError("Wait for the running self-change or cancel it first", 409)
        if sa.read_json(self.PREVIEW.parent / "preview.json", {}).get("branch") == branch:
            self.preview_stop()
        head = head_sha()
        tmp = common.ROOT / "self_improve" / "update-wt"
        git_rc(common.ROOT, "worktree", "remove", "--force", str(tmp))
        shutil.rmtree(tmp, ignore_errors=True)
        rc, out = git_rc(common.ROOT, "worktree", "add", "--detach", str(tmp), branch)
        if rc != 0:
            raise UiError("Could not check the branch out: " + out[-200:])
        try:
            rc, out = git_rc(tmp, "-c", "user.name=codefactory", "-c", "user.email=agent@localhost", "merge", "--no-edit", head)
            if rc != 0:
                files = git_rc(tmp, "diff", "--name-only", "--diff-filter=U")[1].splitlines()
                git_rc(tmp, "merge", "--abort")
                raise UiError("Conflicts with the current code in: " + ", ".join(files[:6]) + ". The branch was not changed. "
                              "Run the task again on the current code or resolve it by hand.", 409)
            new = git_rc(tmp, "rev-parse", "HEAD")[1]
            git_rc(common.ROOT, "update-ref", f"refs/heads/{branch}", new)
        finally:
            git_rc(common.ROOT, "worktree", "remove", "--force", str(tmp))
            shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": True, "message": f"{branch} now contains the current code. Try it again before you merge."}

    def preview_state(self) -> dict:
        info = sa.read_json(self.PREVIEW.parent / "preview.json", {})
        rc = self.PREVIEW.parent / "preview-tests.rc"
        if not alive("ptest") and rc.exists() and "tests_rc" not in info:
            info["tests_rc"] = int(rc.read_text().strip() or "1")
            rc.unlink(missing_ok=True)
            sa.write_json(self.PREVIEW.parent / "preview.json", info)
        up = alive("preview")
        return {"branch": info.get("branch") if up or alive("ptest") else None, "running": up, "port": info.get("port") if up else None,
                "verify": info.get("verify") if up else None, "tests_running": alive("ptest"), "tests_rc": None if alive("ptest") else info.get("tests_rc"),
                "log": tail(self.PREVIEW.parent / "preview.log", 40), "tests_log": tail(self.PREVIEW.parent / "preview-tests.log", 40)}

    def tool_run(self, run_id: str, branch: str = "") -> dict:
        """The plan/todo ledger of one run (latest if none given; the run that produced `branch` if a branch is given)."""
        out = common.ROOT / "self_improve"
        d = runledger.run_for_branch(out, branch) if branch else None
        if d is None:
            runs = runledger.list_runs(out, 1)
            d = runledger.load_run(out, run_id or (runs[0]["id"] if runs else ""))
        return {"run": d, "phase": sa.read_json(out / "phase.json", None), "running": alive("tool")}

    def _verify_preview(self, branch: str, port: int, out: Path) -> None:
        info = sa.read_json(out / "preview.json", {})
        info["verify"] = {"state": "running", "checks": []}
        sa.write_json(out / "preview.json", info)
        checks = runledger.verify_checkout(common.ROOT, self.PREVIEW, branch, port, alive=lambda: alive("preview"))
        info = sa.read_json(out / "preview.json", {})
        if info.get("branch") == branch and info.get("port") == port:  # skip if the preview was replaced meanwhile
            info["verify"] = {"state": "done", "ok": all(c["ok"] for c in checks), "checks": checks, "at": time.time()}
            sa.write_json(out / "preview.json", info)

    def preview_stop(self) -> dict:
        for key in ("preview", "ptest"):
            p = PROCS.get(key)
            if p and p.poll() is None:
                sa.force_stop_pid(p.pid, grace=2.0)
        if self.PREVIEW.exists():
            git(common.ROOT, "worktree", "remove", "--force", str(self.PREVIEW))
            shutil.rmtree(self.PREVIEW, ignore_errors=True)
        return self.preview_state()

    def preview_start(self, body: dict) -> dict:
        """Checks the branch out next to the running tool and starts its UI on a free port (never touches your projects or main)."""
        branch = str(body.get("branch", ""))
        if not BRANCH_RE.match(branch) or not git(common.ROOT, "rev-parse", "--verify", "-q", branch).strip():
            raise UiError("Unknown branch")
        self.preview_stop()
        out = self.PREVIEW.parent
        out.mkdir(parents=True, exist_ok=True)
        git(common.ROOT, "worktree", "prune")
        if "error" in git(common.ROOT, "worktree", "add", "--detach", str(self.PREVIEW), branch).lower() and not self.PREVIEW.exists():
            raise UiError("Could not check the branch out")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        sandbox = out / "preview-projects"
        sandbox.mkdir(exist_ok=True)
        log = open(out / "preview.log", "w", encoding="utf-8")
        sa.write_json(out / "preview.json", {"branch": branch, "port": port})
        PROCS["preview"] = subprocess.Popen([sys.executable, "webui.py", "--port", str(port), "--projects-dir", str(sandbox), "--no-browser"],
                                            cwd=str(self.PREVIEW), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=True, env={**os.environ, "CODEFACTORY_GPU_DIR": str(out / "preview-gpu")})
        if body.get("tests"):
            self.preview_tests()
        threading.Thread(target=self._verify_preview, args=(branch, port, out), daemon=True).start()
        time.sleep(1.2)
        return self.preview_state()

    @staticmethod
    def _git_rc(*args: str) -> tuple[int, str]:
        p = subprocess.run(["git", *args], cwd=str(common.ROOT), capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()

    def tool_merge(self, body: dict) -> dict:
        """Merges a tested self-improve branch into the checked-out branch of the tool repo (with a backup tag and an undo)."""
        branch = str(body.get("branch", ""))
        if not BRANCH_RE.match(branch) or not git(common.ROOT, "rev-parse", "--verify", "-q", branch).strip():
            raise UiError("Unknown branch")
        busy = [p["name"] for p in self.list()["projects"] if p["running"]] + (["the tool itself"] if alive("tool") else [])
        if busy:
            raise UiError("Stop the running agent(s) first: " + ", ".join(busy), 409)
        if git(common.ROOT, "status", "--porcelain").strip():
            raise UiError("The tool folder has uncommitted changes; commit or stash them first", 409)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tag = f"pre-merge-{stamp}"
        self._git_rc("tag", tag)
        rc, out = self._git_rc("merge", "--no-ff", "-m", f"Merge {branch} (tested in preview)", branch)
        if rc != 0:
            self._git_rc("merge", "--abort")
            self._git_rc("tag", "-d", tag)
            raise UiError("Merge not possible (conflict, main moved on). Nothing was changed.\n" + out[-300:], 409)
        sa.write_json(common.ROOT / "self_improve" / "last_merge.json", {"tag": tag, "sha": git(common.ROOT, "rev-parse", "HEAD").strip(), "branch": branch})
        return {"ok": True, "tag": tag, "message": f"Merged {branch}. Restart the UI (make ui) to use it. Undo: the Undo button or git reset --hard {tag}"}

    def tool_undo_merge(self) -> dict:
        info = sa.read_json(common.ROOT / "self_improve" / "last_merge.json", None)
        if not info:
            raise UiError("No merge to undo", 404)
        if git(common.ROOT, "status", "--porcelain").strip():
            raise UiError("The tool folder has uncommitted changes", 409)
        if git(common.ROOT, "rev-parse", "HEAD").strip() != info["sha"]:
            raise UiError("The repository has moved on since that merge; undo it with git revert -m 1 " + info["sha"][:8], 409)
        rc, out = self._git_rc("reset", "--hard", info["tag"])
        if rc != 0:
            raise UiError(out[-300:])
        (common.ROOT / "self_improve" / "last_merge.json").unlink(missing_ok=True)
        return {"ok": True, "message": f"Undone: back at {info['tag']}. Restart the UI (make ui)."}

    def preview_tests(self) -> dict:
        if not self.PREVIEW.exists():
            raise UiError("Start the preview first", 409)
        if alive("ptest"):
            return self.preview_state()
        out = self.PREVIEW.parent
        info = sa.read_json(out / "preview.json", {})
        info.pop("tests_rc", None)
        sa.write_json(out / "preview.json", info)
        f = open(out / "preview-tests.log", "w", encoding="utf-8")
        script = (f"{shlex.quote(sys.executable)} -m unittest discover -s tests && {shlex.quote(sys.executable)} codefactory.py selftest; "
                  f"echo $? > {shlex.quote(str(out / 'preview-tests.rc'))}")
        PROCS["ptest"] = subprocess.Popen(["bash", "-c", script], cwd=str(self.PREVIEW), stdin=subprocess.DEVNULL, stdout=f,
                                          stderr=subprocess.STDOUT, start_new_session=True,
                                          env={**os.environ, "CODEFACTORY_GPU_DIR": str(out / "preview-gpu")})
        return self.preview_state()

    def tool_activity(self) -> dict:
        """Live state of a self-change: stage, streamed model output, Aider/test output and every recorded thought."""
        d = common.ROOT / "self_improve"
        running = alive("tool")
        rows = []
        for line in tail(d / "thoughts.jsonl", 400, 3_000_000).splitlines()[-60:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return {"running": running, "phase": read_live(d / "phase.json", running), "llm": read_live(d / "llm_now.json", running),
                "coder": read_live(d / "coder_now.json", running), "cmd": read_live(d / "cmd_now.json", running),
                "stream": read_live(d / "llm_stream.json", running), "entries": rows[::-1],
                "coder_live": tail(d / "coder_live.txt", 120, 60_000) if running else "",
                "cmd_live": tail(d / "cmd_live.txt", 120, 60_000) if running else ""}

    def tool_tree(self, branch: str) -> dict:
        """Files a result branch changes compared with the running version."""
        if not BRANCH_RE.match(branch or ""):
            raise UiError("Invalid branch")
        files = {}
        for line in git(common.ROOT, "diff", "--numstat", "--no-renames", f"HEAD...{branch}", limit=2_000_000).splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                files[parts[2]] = {"path": parts[2], "status": "M", "add": int(parts[0]) if parts[0].isdigit() else 0,
                                   "del": int(parts[1]) if parts[1].isdigit() else 0}
        for line in git(common.ROOT, "diff", "--name-status", "--no-renames", f"HEAD...{branch}").splitlines():
            st, _, path = line.partition("\t")
            if path in files and st.startswith("A"):
                files[path]["status"] = "A"
        return {"files": sorted(files.values(), key=lambda f: f["path"]), "base": branch}

    def tool_file(self, branch: str, path: str) -> dict:
        if not BRANCH_RE.match(branch or "") or not path or path.startswith("-") or ".." in Path(path).parts:
            raise UiError("File not found")
        ref = f"{branch}:{path}"
        if git(common.ROOT, "cat-file", "-t", ref).strip() != "blob":
            raise UiError("File not found")
        text = git(common.ROOT, "show", ref, limit=MAX_FILE)
        changed: list[int] = []
        for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git(common.ROOT, "diff", "-U0", "--no-renames", f"HEAD...{branch}", "--", path), re.M):
            start, cnt = int(m.group(1)), int(m.group(2) or 1)
            changed.extend(range(start, start + cnt))
        return {"path": path, "text": text, "size": len(text), "truncated": False, "changed": changed,
                "new_file": not git(common.ROOT, "cat-file", "-t", f"HEAD:{path}").strip()}

    def tool_diff(self, branch: str) -> dict:
        if not BRANCH_RE.match(branch or ""):
            raise UiError("Invalid branch")
        return {"diff": git(common.ROOT, "diff", f"HEAD...{branch}")}

    def tool_models(self) -> dict:
        """Saved model choice for self-changes plus what is installed and what automatic routing would pick now."""
        state = common.ROOT / "self_improve"
        saved = toolmodels.load(state)
        installed = self.installed_models(sa.DEFAULT_CONFIG["llm_api"])
        cfg = toolmodels.apply(json.loads(json.dumps(sa.DEFAULT_CONFIG)), state)
        resolved = models.resolve(set(installed), cfg["routing"], cfg["coder_model"])
        return {"saved": saved, "installed": installed, "resolved": resolved, "defaults": {
            "planner": sa.DEFAULT_CONFIG["models"]["planner"], "reviewer": sa.DEFAULT_CONFIG["models"]["reviewer"],
            "summarizer": sa.DEFAULT_CONFIG["models"]["summarizer"], "coder": sa.DEFAULT_CONFIG["coder_model"].split("/", 1)[-1]}}

    def tool_models_save(self, body: dict) -> dict:
        try:
            toolmodels.save(common.ROOT / "self_improve", body)
        except ValueError as e:
            raise UiError(str(e)) from None
        return self.tool_models()

    def tool_task(self, body: dict) -> dict:
        task = str(body.get("task", "")).strip()
        if len(task) < 10:
            raise UiError("Describe what the tool should do in a sentence or two")
        model = str(body.get("model", "")).strip()
        if model and not toolmodels.MODEL_RE.match(model):
            raise UiError(f"Invalid model name: {model!r}")
        if alive("tool"):
            raise UiError("A tool task is already running", 409)
        out = common.ROOT / "self_improve" / "ui.log"
        out.parent.mkdir(exist_ok=True)
        out.write_text("", "utf-8")
        spawn("tool", ["improve", "--task", task, *(["--model", model] if model else [])], out)
        return {"ok": True}

