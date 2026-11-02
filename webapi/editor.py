"""Backend of the project code editor: save/create/rename/delete files, search, git status/commit/revert and the test run.

Reads come in as GET /api/project/<name>/editor/<what>, actions as POST /api/project/<name>/editor. Writes are refused
while the agent works on the project (it would fight with the editor over the same files).
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import codefactory as sa
from core import filegraph
from dash.directedit import _safe
from webapi.common import MAX_FILE, UiError, git

OPS = ("save", "create", "mkdir", "delete", "rename", "revert", "commit", "run-tests", "exec")
IDENT = ("-c", "user.name=CodeFactory", "-c", "user.email=codefactory@localhost")


def _git_rc(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class EditorMixin:
    def _repo(self, name: str) -> Path:
        repo = self.ws(name) / "repo"
        if not repo.is_dir():
            raise UiError("This project has no repo yet")
        return repo

    @staticmethod
    def _line_counts(repo, files: list[str]) -> dict[str, int]:
        out = {}
        for f in files:
            try:
                data = (repo / f).read_bytes()
            except OSError:
                continue
            if b"\0" not in data[:2048]:
                out[f] = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        return out

    def _rel(self, repo: Path, path: str) -> tuple[Path, str]:
        try:
            p = _safe(repo, path)
        except ValueError as e:
            raise UiError(str(e)) from None
        return p, p.relative_to(repo.resolve()).as_posix()

    def editor_get(self, name: str, what: str, q: dict) -> dict:
        repo = self._repo(name)
        if what == "search":
            return self._editor_search(repo, q.get("q", ""), q.get("regex") == "1", q.get("case") == "1")
        if what == "git":
            lines = git(repo, "status", "--porcelain=v1", "-uall").splitlines()
            files = [{"status": ln[:2].strip() or "?", "path": ln[3:].strip().strip('"')} for ln in lines if len(ln) > 3]
            branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
            log = [dict(zip(("sha", "subject", "date"), ln.split("\x1f"), strict=False))
                   for ln in git(repo, "log", "-n", "15", "--format=%h\x1f%s\x1f%cI").splitlines()]
            return {"branch": branch, "files": files, "log": log}
        if what == "mentions":
            files = [f for f in git(repo, "ls-files").splitlines() if f][:3000]
            tasks = [{"id": t["id"], "title": t["title"], "status": t["status"]}
                     for t in sa.read_json(repo.parent / "agent" / "backlog.json", [])][:300]
            return {"files": files, "tasks": tasks}
        if what == "filegraph":
            files = filegraph.keep([f for f in git(repo, "ls-files").splitlines() if f])

            def read(f: str) -> str:
                try:
                    return (repo / f).read_text("utf-8")[:60000]
                except (OSError, UnicodeDecodeError):
                    return ""

            def stamp(f: str) -> int:
                try:
                    return (repo / f).stat().st_mtime_ns
                except OSError:
                    return 0
            sig = tuple((f, stamp(f)) for f in files)  # the page polls: rescan the imports only when a file changed
            cache = self.__dict__.setdefault("_fg_cache", {})
            hit = cache.get(name)
            if not hit or hit[0] != sig:
                hit = cache[name] = (sig, filegraph.edges_of(files, read), self._line_counts(repo, files))
            return filegraph.build(files, read, sa.read_json(repo.parent / "agent" / "backlog.json", []), hit[1], hit[2])
        if what == "head":
            _, rel = self._rel(repo, q.get("path", ""))
            rc, out = _git_rc(repo, "show", f"HEAD:{rel}")
            return {"path": rel, "text": out if rc == 0 else None}
        raise UiError("Not found", 404)

    def _editor_search(self, repo: Path, query: str, regex: bool, case: bool) -> dict:
        if len(query) < 2:
            return {"hits": [], "truncated": False}
        if regex:
            try:
                re.compile(query)
            except re.error as e:
                raise UiError(f"Invalid regex: {e}") from None
        args = ["grep", "-n", "-I", "--untracked", "--no-color", "-e", query]
        args[1:1] = ["-E" if regex else "-F"] + ([] if case else ["-i"])
        out = git(repo, *args, limit=400_000)
        hits = []
        for ln in out.splitlines():
            m = re.match(r"^(.*?):(\d+):(.*)$", ln)
            if m:
                hits.append({"path": m.group(1), "line": int(m.group(2)), "text": m.group(3)[:240]})
        return {"hits": hits[:300], "truncated": len(hits) > 300}

    def editor_act(self, ws: Path, name: str, body: dict) -> dict:
        op = str(body.get("op", ""))
        if op not in OPS:
            raise UiError("Unknown editor action")
        repo = self._repo(name)
        if op == "run-tests":
            return self._editor_tests(ws, repo)
        if self._running(ws, name):
            raise UiError("The agent is working on this project. Stop it first, or edit when it is idle.", 409)
        if op == "exec":
            return self._editor_exec(repo, str(body.get("cmd", "")))
        if op == "commit":
            msg = str(body.get("message", "")).strip() or "Edit in the code editor"
            _git_rc(repo, "add", "-A")
            rc, out = _git_rc(repo, *IDENT, "commit", "-qm", msg)
            if rc:
                raise UiError("Nothing to commit" if "nothing to commit" in out or not out.strip() else out.strip()[:300])
            return {"ok": True}
        p, rel = self._rel(repo, body.get("path", ""))
        if op == "save":
            text = str(body.get("text", ""))
            if len(text.encode("utf-8")) > MAX_FILE:
                raise UiError("File too large for the editor")
            if p.is_dir():
                raise UiError("That is a folder")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, "utf-8", newline="")
            return {"ok": True, "path": rel, "size": p.stat().st_size}
        if op == "create":
            if p.exists():
                raise UiError("That file already exists", 409)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("", "utf-8")
            return {"ok": True, "path": rel}
        if op == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            (p / ".gitkeep").touch()
            return {"ok": True, "path": rel}
        if op == "delete":
            if not p.exists():
                raise UiError("File not found", 404)
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            return {"ok": True}
        if op == "rename":
            dest, drel = self._rel(repo, body.get("to", ""))
            if not p.exists() or dest.exists():
                raise UiError("Source missing or target already exists", 409)
            dest.parent.mkdir(parents=True, exist_ok=True)
            p.rename(dest)
            return {"ok": True, "path": drel}
        tracked = bool(git(repo, "ls-files", "--", rel).strip())  # revert
        if tracked:
            rc, out = _git_rc(repo, "checkout", "HEAD", "--", rel)
            if rc:
                raise UiError(out.strip()[:300])
        elif p.is_file():
            p.unlink()
        return {"ok": True, "path": rel, "removed": not tracked}

    @staticmethod
    def _editor_exec(repo: Path, cmd: str) -> dict:
        """Terminal panel: one shell command in the project repo (no state between commands, 120 s limit)."""
        cmd = cmd.strip()
        if not cmd or len(cmd) > 2000:
            raise UiError("Type a command")
        try:
            p = subprocess.run(cmd, shell=True, cwd=str(repo), capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"cmd": cmd, "rc": -1, "out": "Timed out after 120 s"}
        return {"cmd": cmd, "rc": p.returncode, "out": (p.stdout + p.stderr).decode("utf-8", "replace")[-20000:]}

    def _editor_tests(self, ws: Path, repo: Path) -> dict:
        cmd = sa.Agent(ws).cfg.get("commands", {}).get("test") or ""
        if not cmd:
            raise UiError("No test command is set for this project yet")
        try:
            p = subprocess.run(shlex.split(cmd), cwd=str(repo), capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            return {"cmd": cmd, "rc": -1, "out": "Timed out after 300 s"}
        except (OSError, ValueError) as e:
            return {"cmd": cmd, "rc": -1, "out": str(e)}
        out = (p.stdout + p.stderr).decode("utf-8", "replace")
        return {"cmd": cmd, "rc": p.returncode, "out": out[-20000:]}
