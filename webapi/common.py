"""Shared constants and small process/file helpers used by the webapi mixins and by webui.py itself."""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

import codefactory as sa

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"
MAX_BODY = 1_000_000
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,99}$")
FEATURE_KEYS = {"cleanup_pass": "bool", "require_tests": "bool", "plan_critique": "bool", "security": ("on", "off"), "e2e": ("auto", "on", "off"),
                "test_first": ("auto", "on", "off"), "step_by_step": ("auto", "on", "off"), "acceptance_check": "bool", "retry_replan": "bool", "protect_tests": "bool",
                "targeted_tests": "bool", "flaky_retry": "bool", "verify_cache": "bool", "coverage_gate": ("off", "50", "70", "90"), "mutation_check": "bool"}
SETTING_LIMITS = {"max_hours": (1, 720), "max_iterations": (1, 5000), "max_attempts_per_task": (1, 8),
                  "max_debug_rounds": (1, 8), "max_consecutive_failures": (1, 20), "min_review_score": (1, 10)}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_FILE = 400_000
SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")
BRANCH_RE = re.compile(r"^self-improve/[0-9]{8}-[0-9]{6}$")
MAX_DIFF = 200_000

PULLS: dict[str, dict] = {}  # model name -> progress of an `ollama pull` started from the UI
PULL_CANCEL: set[str] = set()
PROCS: dict[str, subprocess.Popen] = {}  # started agents, init and try jobs: "<kind>:<project>"
LOCK = threading.Lock()
UPDATE_LOCK = threading.Lock()


class UiError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def git_rc(cwd: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def head_sha() -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, timeout=10)
    return p.stdout.strip() if p.returncode == 0 else ""


BUILD = {"sha": "", "started": time.time()}  # the commit this process was started from (set in main())


def alive(key: str) -> bool:
    p = PROCS.get(key)
    return p is not None and p.poll() is None


def pid_alive(path: Path) -> bool:
    try:
        os.kill(int(path.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def tail(path: Path, lines: int = 120, max_bytes: int = 60_000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])


def spawn(key: str, args: list[str], out: Path, env: dict | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    f = open(out, "a", encoding="utf-8")
    PROCS[key] = subprocess.Popen(["bash", "scripts/run.sh", *args], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f,
                                  stderr=subprocess.STDOUT, start_new_session=True, env={**os.environ, **(env or {})})


def git(repo: Path, *args: str, limit: int = MAX_DIFF) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = p.stdout.decode("utf-8", "replace")
    return out[:limit] + ("\n... (truncated)" if len(out) > limit else "")


def read_live(path: Path, running: bool) -> dict | None:
    data = sa.read_json(path, None)
    return data if data and running else None
