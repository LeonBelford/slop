"""Checkpoints for direct chat edits: every write is its own git commit, so a change can be shown and undone by commit."""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

from dash.directedit import _safe

WRITE_TOOLS = ("write_file", "edit_file", "delete_file")
SHA = re.compile(r"[0-9a-f]{7,40}")


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", "-c", "user.name=CodeFactory", "-c", "user.email=codefactory@localhost", *a], cwd=repo, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise ValueError((r.stderr or r.stdout).strip()[:400])
    return r.stdout


def head(repo: Path) -> str:
    try:
        return _git(repo, "rev-parse", "HEAD").strip()
    except (ValueError, OSError):
        return ""


def preview(repo: Path, tool: str, args: dict) -> str:
    """Unified diff of what a write tool would do, without doing it."""
    path = str(args.get("path", ""))
    f = _safe(repo, path)
    old = f.read_text("utf-8", "replace") if f.is_file() else ""
    if tool == "write_file":
        new = str(args.get("content", ""))
    elif tool == "edit_file":
        o = str(args.get("old", ""))
        if not o or old.count(o) != 1:
            raise ValueError("'old' must match the file exactly once")
        new = old.replace(o, str(args.get("new", "")), 1)
    elif tool == "delete_file":
        new = ""
    else:
        raise ValueError("Nothing to preview for this tool")
    d = difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=3)
    return "\n".join(list(d)[:400]) or "(no changes)"


def show(repo: Path, sha: str) -> str:
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid checkpoint")
    return _git(repo, "show", "--format=%s%n", "--patch", sha)[:20000]


def undo(repo: Path, sha: str) -> str:
    """Reverts one checkpoint as a new commit (history stays intact). Needs a clean tree; conflicts are rolled back."""
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid checkpoint")
    if _git(repo, "status", "--porcelain").strip():
        raise ValueError("The working tree has uncommitted changes")
    try:
        _git(repo, "revert", "--no-edit", sha)
    except ValueError as e:
        try:
            _git(repo, "revert", "--abort")
        except ValueError:
            pass
        raise ValueError(f"Cannot undo cleanly (later edits touch the same lines): {e}") from None
    return "undone"
