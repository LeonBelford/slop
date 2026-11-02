"""Terminal and playground for a project: commands run in a COPY of the repo (`<project>/sandbox`), never in the agent's own repo.

This is a convenience copy, not a security boundary: a command still runs as your user and can reach the network and your home
folder. The UI is protected by the same login as the rest of the tool; use it only on a machine you trust.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

OUT_MAX = 20_000
TIMEOUT_MAX = 300


def sandbox_dir(ws: Path) -> Path:
    return ws / "sandbox"


def sync(ws: Path) -> Path:
    """Refreshes the sandbox from the repo's current state. Installed dependencies (.venv, node_modules) of the sandbox are kept."""
    sb = sandbox_dir(ws)
    keep = shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".pytest_cache", ".venv")
    shutil.copytree(ws / "repo", sb, ignore=keep, dirs_exist_ok=True, symlinks=True)
    return sb


def _env(sb: Path) -> dict:
    return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", str(sb)), "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"}


def run(ws: Path, cmd: str, timeout: int = 60) -> dict:
    """One command in the sandbox; returns rc, output (cut to 20k) and seconds. A timeout kills the whole process group."""
    cmd = cmd.strip()
    if not cmd or len(cmd) > 2000:
        raise ValueError("Command is empty or too long")
    sb = sync(ws)
    t0 = time.time()
    p = subprocess.Popen(["bash", "-c", cmd], cwd=sb, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=_env(sb))
    try:
        out, _ = p.communicate(timeout=min(int(timeout), TIMEOUT_MAX))
        rc, note = p.returncode, ""
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        rc, note = 124, f"\n[stopped after {timeout}s]"
    text = out.decode("utf-8", "replace")
    return {"rc": rc, "out": (text[-OUT_MAX:] if len(text) > OUT_MAX else text) + note, "seconds": round(time.time() - t0, 1)}


def _state(ws: Path) -> Path:
    return ws / "agent" / "play.json"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def play_start(ws: Path, cmd: str) -> dict:
    """Starts the app in the sandbox in its own process group; the log goes to agent/play.log."""
    if play_status(ws)["running"]:
        raise ValueError("The playground is already running; stop it first")
    cmd = cmd.strip()
    if not cmd or len(cmd) > 2000:
        raise ValueError("Start command is empty or too long")
    sb = sync(ws)
    log = ws / "agent" / "play.log"
    log.write_text(f"$ {cmd}\n", "utf-8")
    f = open(log, "a", encoding="utf-8")
    p = subprocess.Popen(["bash", "-c", cmd], cwd=sb, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=_env(sb))
    _state(ws).write_text(json.dumps({"pid": p.pid, "cmd": cmd, "started": time.time()}), "utf-8")
    return {"ok": True, "pid": p.pid}


def play_stop(ws: Path) -> None:
    st = play_status(ws)
    if st["running"]:
        try:
            os.killpg(st["pid"], signal.SIGTERM)
        except OSError:
            pass
    _state(ws).unlink(missing_ok=True)


def play_status(ws: Path, tail_chars: int = 6000) -> dict:
    try:
        s = json.loads(_state(ws).read_text("utf-8"))
    except (OSError, ValueError):
        s = {}
    running = bool(s) and _alive(int(s.get("pid", 0)))
    try:
        log = (ws / "agent" / "play.log").read_text("utf-8", "replace")[-tail_chars:]
    except OSError:
        log = ""
    return {"running": running, "pid": s.get("pid", 0), "cmd": s.get("cmd", ""), "log": log, "since": s.get("started", 0)}
