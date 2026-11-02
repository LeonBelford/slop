"""Direct file operations the chat agent can run without going through the full plan/build/test pipeline.

Notes and folders are always available: they never touch tracked source, so there is nothing to review or test.
Writing a file straight into the repo is opt-in (config.json -> chat.direct_edit) because it skips review and tests.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise ValueError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def _tests_ok(repo: Path, cmd: str, timeout: int = 600) -> bool:
    try:
        return subprocess.run(["bash", "-c", cmd], cwd=repo, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _safe(root: Path, rel: str) -> Path:
    p = (root / str(rel)).resolve()
    root = root.resolve()
    if not p.is_relative_to(root) or p == root or ".git" in p.relative_to(root).parts:
        raise ValueError("Path must stay inside the project")
    return p


def write_note(ws: Path, file: str, text: str) -> str:
    """Appends a timestamped note to ws/notes/<file>, outside the repo: the pipeline and tests never see it."""
    name = Path(str(file or "notes.md")).name
    if not name or not name.endswith((".md", ".txt")):
        name = (name or "notes") + ".md"
    p = _safe(ws / "notes", name)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')}\n{str(text).strip()}\n")
    return f"added to notes/{name}"


def make_folder(repo: Path, path: str) -> str:
    """Creates an empty folder (with a .gitkeep so git tracks it) and commits it."""
    p = _safe(repo, path)
    if p.exists() and any(p.iterdir()):
        raise ValueError("That folder already exists and is not empty")
    p.mkdir(parents=True, exist_ok=True)
    (p / ".gitkeep").write_text("", "utf-8")
    try:
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.name=CodeFactory", "-c", "user.email=codefactory@localhost", "commit", "-qm", f"chat: create folder {path}")
    except ValueError:
        pass  # not a git repo, or nothing new to commit - the folder is still there
    return f"created {path}/"


def write_file(repo: Path, path: str, content: str, test_cmd: str = "") -> str:
    """Writes a file straight into the repo and commits it. Refuses when the tree's tests are already red, so a
    direct write never lands silently on top of an already broken project."""
    p = _safe(repo, path)
    if test_cmd and not _tests_ok(repo, test_cmd):
        raise ValueError("The tests already fail on this tree: fix them first, direct writes are blocked")
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    p.write_text(str(content), "utf-8")
    try:
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.name=CodeFactory", "-c", "user.email=codefactory@localhost", "commit", "-qm", f"chat: {'update' if existed else 'create'} {path}")
    except ValueError:
        pass
    return f"{'updated' if existed else 'created'} {path}"
