"""Real-browser testing helpers for the agent (standard library only). The browser work itself is done by e2e_runner.py
in a subprocess with an interpreter that has Playwright installed (`make e2e-setup`)."""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = Path(__file__).resolve().parent / "e2e_runner.py"
INSTALL_HELP = ("Browser tests need Playwright: run `make e2e-setup` "
                "(installs it into .venv with Chromium; on a fresh Linux also `sudo .venv/bin/playwright install-deps chromium`).")


def find_python() -> str | None:
    """First interpreter that can import Playwright with an installed Chromium, else None."""
    for py in (sys.executable, str(ROOT / ".venv" / "bin" / "python")):
        if not Path(py).exists():
            continue
        code = "from playwright.sync_api import sync_playwright as s\nwith s() as p:\n    import os; print(os.path.exists(p.chromium.executable_path))"
        try:
            r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip().endswith("True"):
            return py
    return None


def extract_url(text: str) -> str:
    m = re.search(r"https?://[^\s)]+", text or "")
    return m.group(0).replace("localhost", "127.0.0.1") if m else ""


def port_busy(url: str) -> bool:
    m = re.match(r"https?://([^:/]+):(\d+)", url)
    if not m:
        return False
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((m.group(1), int(m.group(2)))) == 0


def start_app(cmd: str, cwd: Path, url: str, timeout: int = 45) -> tuple[subprocess.Popen | None, str]:
    """Starts the product in its own process group and waits until the URL answers. Returns (process, error)."""
    log = cwd.parent / "agent" / "e2e_app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(cwd), shell=True, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return None, f"The app exited with code {proc.returncode}:\n{log.read_text('utf-8', 'replace')[-1500:]}"
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return proc, ""
        except urllib.error.HTTPError:
            return proc, ""  # any HTTP answer means the server is up
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    stop_app(proc)
    return None, f"The app did not answer at {url} within {timeout}s:\n{log.read_text('utf-8', 'replace')[-1500:]}"


def stop_app(proc: subprocess.Popen | None) -> None:
    if not proc:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=4)
            return
        except subprocess.TimeoutExpired:
            continue


def _runner(py: str, args: list[str], timeout: int) -> dict:
    try:
        r = subprocess.run([py, str(RUNNER), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Browser test timed out after {timeout}s", "problems": [], "snapshot": ""}
    lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
    if not lines:
        return {"ok": False, "error": f"Runner crashed: {(r.stderr or r.stdout)[-600:]}", "problems": [], "snapshot": ""}
    return json.loads(lines[-1])


def smoke(py: str, url: str, outdir: Path) -> dict:
    return _runner(py, ["smoke", url, str(outdir)], 120)


def run_script(py: str, script: Path, url: str, outdir: Path) -> dict:
    return _runner(py, ["script", str(script), url, str(outdir)], 120)


def extract_script(text: str) -> str:
    """Python code of a model answer: the first fenced block, else the whole text. Must define run(page, base)."""
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    code = (m.group(1) if m else text).strip()
    return code + "\n" if "def run(" in code else ""
