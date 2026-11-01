#!/usr/bin/env python3
"""Updates CodeFactory itself from origin/main (standard library only).

  python3 update.py            check and apply a fast-forward update
  python3 update.py --check    only report whether an update is available

Safe by design: refuses with local changes or a diverged branch, tags the current version first
(`backup/pre-update-<time>`), only fast-forwards, installs changed Python requirements into .venv,
smoke-imports the new code and rolls back automatically if that fails.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESTART_SUFFIXES = (".py", ".html", ".sh", "Makefile")


def git(repo: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


BRANCH = os.environ.get("CODEFACTORY_UPDATE_BRANCH", "main")  # updates always come from this branch of origin
REMOTE = "origin"


def status(repo: Path = ROOT, fetch: bool = True) -> dict:
    """What is installed and whether origin/main has newer commits (whatever branch is checked out)."""
    if git(repo, "rev-parse", "--git-dir")[0] != 0:
        return {"available": False, "state": "no-git", "message": "Not a git checkout (installed from a zip?). Clone the repository to get updates."}
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1]
    local = git(repo, "rev-parse", "--short", "HEAD")[1]
    if fetch:
        rc, out = git(repo, "fetch", "--quiet", "--tags", REMOTE, BRANCH, timeout=30)
        if rc != 0:
            return {"available": False, "state": "offline", "branch": branch, "local": local, "message": "Cannot reach the remote: " + out[:200]}
    up = f"{REMOTE}/{BRANCH}"
    if git(repo, "rev-parse", "--verify", "-q", up)[0] != 0:
        return {"available": False, "state": "no-upstream", "branch": branch, "local": local,
                "message": f"No {up} found. Is the remote '{REMOTE}' set (git remote -v)?"}
    # Compare the local main (not a feature branch) with origin/main; without a local main everything is "behind".
    base = BRANCH if git(repo, "rev-parse", "--verify", "-q", BRANCH)[0] == 0 else "HEAD"
    rc, counts = git(repo, "rev-list", "--left-right", "--count", f"{base}...{up}")
    ahead, behind = (int(x) for x in counts.split()) if rc == 0 and len(counts.split()) == 2 else (0, 0)
    dirty = [l.split(None, 1)[-1] for l in git(repo, "status", "--porcelain", "--untracked-files=no")[1].splitlines()]
    log = git(repo, "log", "--format=%h %s", "-n", "15", f"{base}..{up}")[1].splitlines() if behind else []
    state = "diverged" if ahead and behind else "behind" if behind else "ahead" if ahead else "current"
    msg = {"current": f"Up to date with {up}.", "behind": f"{behind} new commit(s) on {up}.",
           "ahead": f"Local {BRANCH} has {ahead} commit(s) not on {up}; nothing to update.",
           "diverged": f"Local {BRANCH} and {up} have both changed; update manually (git pull --rebase)."}[state]
    if state == "behind" and branch != BRANCH:
        msg += f" You are on '{branch}'; updating switches to {BRANCH}."
    return {"available": state == "behind", "state": state, "branch": branch, "upstream": up, "local": local,
            "remote": git(repo, "rev-parse", "--short", up)[1], "behind": behind, "ahead": ahead, "dirty": dirty,
            "commits": log, "message": msg}


def apply(repo: Path = ROOT, log=print) -> dict:
    """Fast-forwards to the upstream, refreshes dependencies and rolls back if the new code does not import."""
    st = status(repo)
    if st["state"] != "behind":
        return {"ok": st["state"] == "current", "updated": False, "message": st["message"], "status": st}
    if st["dirty"]:
        return {"ok": False, "updated": False, "status": st,
                "message": "Local changes would be overwritten: " + ", ".join(st["dirty"][:6]) + ". Commit or stash them first."}
    start = git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1]
    if start != BRANCH:
        rc, out = git(repo, "checkout", "-q", BRANCH) if git(repo, "rev-parse", "--verify", "-q", BRANCH)[0] == 0 \
            else git(repo, "checkout", "-q", "-b", BRANCH, st["upstream"])
        if rc != 0:
            return {"ok": False, "updated": False, "status": st, "message": f"Could not switch to {BRANCH}: {out[:300]}"}
    old = git(repo, "rev-parse", "HEAD")[1]
    tag = "backup/pre-update-" + time.strftime("%Y%m%d-%H%M%S")
    git(repo, "tag", tag, old)
    rc, out = git(repo, "merge", "--ff-only", st["upstream"], timeout=120)
    if rc != 0:
        if start != BRANCH:
            git(repo, "checkout", "-q", start)
        return {"ok": False, "updated": False, "message": "Update failed: " + out[:300], "status": st}
    changed = git(repo, "diff", "--name-only", old, "HEAD")[1].splitlines()
    notes: list[str] = []
    pip = repo / ".venv" / "bin" / "pip"
    if "requirements.txt" in changed and pip.exists():
        log("Installing changed requirements ...")
        try:
            r = subprocess.run([str(pip), "install", "-q", "-r", "requirements.txt"], cwd=repo, capture_output=True, text=True, timeout=600)
            prc, pout = r.returncode, r.stderr[-300:]
        except (OSError, subprocess.SubprocessError) as e:
            prc, pout = 1, str(e)
        notes.append("requirements installed" if prc == 0 else "requirements install failed: " + pout)
    elif "requirements.txt" in changed:
        notes.append("requirements.txt changed: run `make setup` to refresh the virtualenv")
    try:
        chk = subprocess.run([sys.executable, "-c", "import codefactory, webui, security, chat, knowledge"], cwd=repo,
                             capture_output=True, text=True, timeout=60)
        healthy, detail = chk.returncode == 0 or not (repo / "codefactory.py").exists(), chk.stderr[-300:]
    except (OSError, subprocess.SubprocessError) as e:
        healthy, detail = False, str(e)
    if not healthy:
        git(repo, "reset", "--hard", old)
        if start != BRANCH:
            git(repo, "checkout", "-q", start)
        return {"ok": False, "updated": False, "status": st,
                "message": f"The new version failed its import check and was rolled back to {old[:7]}: {detail}"}
    restart = any(f.endswith(RESTART_SUFFIXES) for f in changed)
    return {"ok": True, "updated": True, "from": old[:7], "to": git(repo, "rev-parse", "--short", "HEAD")[1], "backup": tag,
            "commits": st["commits"], "changed": len(changed), "restart": restart, "notes": notes,
            "message": f"Updated {old[:7]} -> {git(repo, 'rev-parse', '--short', 'HEAD')[1]} ({len(changed)} files). "
                       f"Restore with: git reset --hard {tag}"}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    st = status()
    print(st["message"])
    if st.get("dirty") and st["state"] == "behind":
        print("Local changes: " + ", ".join(st["dirty"][:6]))
    for c in st.get("commits", []):
        print("  " + c)
    if "--check" in args or st["state"] != "behind":
        return 0 if st["state"] in ("current", "behind", "ahead") else 1
    res = apply()
    print(res["message"])
    for n in res.get("notes", []):
        print("  " + n)
    if res.get("restart"):
        print("Restart the web UI and any running agents (make stop / make start) to use the new code.")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
