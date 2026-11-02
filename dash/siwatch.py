"""Post-merge watchdog: after a self-improve branch was merged, check that the tool still starts and offer a rollback.

`check` compiles every Python file, imports the web UI module and runs the selftest; the result goes to self_improve/watchdog.json
(shown in the dashboard). `rollback` reverts the last merge of a self-improve branch with a new commit (history stays intact).
`install_hook` adds a git post-merge hook that runs the check after every merge in this checkout.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HOOK = "#!/bin/sh\n# CodeFactory watchdog: verifies the tool after a merge\npython3 codefactory.py watchdog || true\n"


def _sh(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def last_selfimprove_merge(repo: Path) -> str:
    """Hash of the newest merge commit that merged a self-improve branch ('' if none)."""
    rc, out = _sh(["git", "log", "-1", "--merges", "--format=%H", "--grep=self-improve/"], repo, 30)
    return out.strip() if rc == 0 else ""


def check(repo: Path, state: Path | None = None) -> dict:
    repo = Path(repo)
    steps = [("compile", [sys.executable, "-m", "compileall", "-q", "-x", r"(\.venv|node_modules|projects|self_improve)", "."]),
             ("import web ui", [sys.executable, "-c", "import webui"]),
             ("selftest", [sys.executable, "codefactory.py", "selftest"])]
    failed = ""
    for name, cmd in steps:
        rc, out = _sh(cmd, repo)
        if rc != 0:
            failed = f"{name}: {out[-300:]}"
            break
    rc, head = _sh(["git", "rev-parse", "--short", "HEAD"], repo, 30)
    res = {"ok": not failed, "failed": failed, "head": head if rc == 0 else "", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "merge": last_selfimprove_merge(repo)[:10]}
    d = Path(state) if state else repo / "self_improve"
    d.mkdir(exist_ok=True)
    (d / "watchdog.json").write_text(json.dumps(res), "utf-8")
    return res


def last_result(state: Path) -> dict:
    try:
        return json.loads((Path(state) / "watchdog.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def rollback(repo: Path) -> tuple[bool, str]:
    """Reverts the last self-improve merge. Refuses with uncommitted changes (nothing is lost)."""
    repo = Path(repo)
    if _sh(["git", "status", "--porcelain", "--untracked-files=no"], repo, 30)[1]:
        return False, "uncommitted changes in the checkout: commit or stash them first"
    merge = last_selfimprove_merge(repo)
    if not merge:
        return False, "no merged self-improve branch found"
    rc, out = _sh(["git", "-c", "user.name=codefactory", "-c", "user.email=agent@localhost", "revert", "-m", "1", "--no-edit", merge], repo, 120)
    if rc != 0:
        _sh(["git", "revert", "--abort"], repo, 30)
        return False, out[-300:]
    return True, f"reverted merge {merge[:10]}"


def install_hook(repo: Path) -> bool:
    hooks = Path(repo) / ".git" / "hooks"
    if not hooks.is_dir():
        return False
    f = hooks / "post-merge"
    if f.exists() and "CodeFactory watchdog" not in f.read_text("utf-8", "replace"):
        return False  # somebody else's hook: do not overwrite
    f.write_text(HOOK, "utf-8")
    f.chmod(0o755)
    return True


def cli(a, here: Path) -> int:
    """`codefactory.py watchdog [--rollback|--install-hook]`; returns the exit code."""
    if a.install_hook:
        print("hook installed" if install_hook(here) else "no git checkout or a foreign post-merge hook exists")
        return 0
    if a.rollback:
        ok, msg = rollback(here)
        print(("rolled back: " if ok else "rollback refused: ") + msg)
        return 0 if ok else 1
    res = check(here)
    print("WATCHDOG OK" if res["ok"] else f"WATCHDOG FAILED: {res['failed']}\nRoll back with: python3 codefactory.py watchdog --rollback")
    return 0 if res["ok"] else 1
