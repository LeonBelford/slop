"""Assistant chat about CodeFactory itself: the model knows the code layout, the current self-improve state and files the
user mentions. Changes are never applied from the chat: the model proposes a ```task block, the user sends it to the
self-improve pipeline, which builds and tests it on a separate branch that can be tried in a preview and then merged.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from dash import runledger

TASK_RE = re.compile(r"```task[ \t]*\n(.*?)```", re.S)
FILE_RE = re.compile(r"[\w./-]+\.(?:py|js|html|css|md|json|toml|yml|yaml)\b")
MAX_FILES = 3
FILE_CHARS = 4000
MAP_CHARS = 6000

SYSTEM = ("You are the assistant for CodeFactory itself (the program the user is running), in a chat. You discuss how the tool works, "
          "find problems and design improvements to it. You cannot change files directly. When the user wants a change, first "
          "discuss it briefly, then propose exactly one self-contained change as a fenced block that starts with ```task and ends "
          "with ```. Inside, write 2-6 concrete sentences: what to change, in which files, and how to test it. The user then "
          "presses 'Build on test branch': the self-improve pipeline implements it on a separate git branch, runs the tests, and "
          "the user can try the result in a preview and merge it. Never claim something is already changed. Answer in the "
          "user's language.")


def extract_tasks(text: str) -> list[str]:
    """Task descriptions proposed in an assistant message (```task blocks)."""
    return [m.strip() for m in TASK_RE.findall(text or "") if len(m.strip()) >= 10]


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def mentioned_files(root: Path, text: str) -> list[Path]:
    """Existing tracked files named in the text (inside the repo only), at most MAX_FILES."""
    tracked = set(_git(root, "ls-files").splitlines())
    out: list[Path] = []
    for name in FILE_RE.findall(text or ""):
        rel = name.lstrip("./")
        found = [rel] if rel in tracked else [t for t in sorted(tracked) if "/" not in name and t.rsplit("/", 1)[-1] == name]
        out += [root / t for t in found if (root / t) not in out]
    return out[:MAX_FILES]


def context(root: Path, out_dir: Path, running: bool, last_user: str = "") -> str:
    """Prompt context: code map, self-improve state, recent runs/branches and the files the user mentioned."""
    parts = []
    pm = root / "PROJECT_MAP.md"
    if pm.exists():
        parts.append("Code map (PROJECT_MAP.md):\n" + pm.read_text("utf-8", "replace")[:MAP_CHARS])
    runs = runledger.list_runs(out_dir, 5)
    parts.append(f"Self-improve is {'RUNNING a task right now' if running else 'idle'}.")
    if runs:
        parts.append("Recent runs:\n" + "\n".join(
            f"- [{r['status']}] {r['title']} ({r['done']}/{r['total']} requirements)" + (f" -> branch {r['branch']}" if r["branch"] else "")
            for r in runs))
    branches = _git(root, "for-each-ref", "refs/heads/self-improve/", "--sort=-creatordate", "--format=%(refname:short) %(subject)").splitlines()[:5]
    if branches:
        parts.append("Review branches waiting for the user:\n" + "\n".join("- " + b for b in branches))
    for f in mentioned_files(root, last_user):
        body = f.read_text("utf-8", "replace")
        parts.append(f"File `{f.relative_to(root)}`:\n```\n{body[:FILE_CHARS]}{chr(10) + '[...]' if len(body) > FILE_CHARS else ''}\n```")
    return "\n\n".join(parts)
