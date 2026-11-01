"""GPU scheduling between several agents on one machine.

One agent is the MAIN agent and keeps priority. Agents set to "side" only use the model server while the main agent
is idle (building, testing, reading, waiting for a human) and step aside again the moment main needs it: a running
model call is aborted (closing the connection stops generation in Ollama) or Aider is stopped, and the work is
retried once main is idle again. Only one side agent uses the model at a time.

State lives in a small shared folder (default ~/.codefactory/gpu, override with CODEFACTORY_GPU_DIR), one JSON file per
agent process; stale files of dead processes are ignored.
"""
from __future__ import annotations

import atexit
import contextlib
import fcntl
import json
import os
import threading
import time
from pathlib import Path

PREEMPTED_RC = 75  # exit code sh_live() returns when a side agent was stopped for main
HEARTBEAT = 3.0  # a busy main refreshes its file this often
STALE = 20.0  # a "busy" marker older than this is ignored (crashed agent)


class Preempted(Exception):
    """The main agent needs the GPU: abort the current model call."""


def shared_dir() -> Path:
    d = Path(os.getenv("CODEFACTORY_GPU_DIR") or Path.home() / ".codefactory" / "gpu")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main_state(directory: Path, now: float | None = None) -> tuple[bool, float]:
    """(main is using the GPU right now, seconds the GPU has been free of main). No live main agent: (False, inf)."""
    now = now or time.time()
    busy, idle = False, float("inf")
    for f in directory.glob("*.json"):
        try:
            s = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if s.get("prio") != "main" or not _alive(int(s.get("pid", 0))):
            continue
        if s.get("busy") and now - s.get("ts", 0) < STALE:
            busy = True
        else:
            idle = min(idle, now - s.get("since", now))
    return busy, (0.0 if busy else idle)


class NullScheduler:
    """Used when scheduling is not set up (tests, mocks): never waits, never preempts."""

    waiting = False

    @contextlib.contextmanager
    def gpu(self, label: str = ""):
        yield self

    def check(self) -> None:
        return


class Scheduler:
    def __init__(self, name: str, priority: str, min_idle: float, log, status_file: Path | None = None, directory: Path | None = None):
        self.name, self.prio, self.min_idle, self.log = name, priority if priority in ("main", "side") else "main", float(min_idle), log
        self.dir = directory or shared_dir()
        self.me = self.dir / f"{os.getpid()}.json"
        self.status_file = status_file
        self.waiting = False
        self.s = {"pid": os.getpid(), "name": name, "prio": self.prio, "busy": False, "since": time.time(), "ts": time.time()}
        self._depth = 0
        self._lock = threading.Lock()
        self._side_fd = None
        self._last_check = 0.0
        self._registered = False

    # ---- shared state
    def _write(self) -> None:
        self.s["ts"] = time.time()
        tmp = self.me.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s), "utf-8")
        os.replace(tmp, self.me)

    def _status(self, state: str, detail: str = "") -> None:
        if self.status_file:
            try:
                self.status_file.write_text(json.dumps({"priority": self.prio, "state": state, "detail": detail, "since": time.time()}), "utf-8")
            except OSError:
                pass

    def close(self) -> None:
        self.me.unlink(missing_ok=True)

    # ---- main: mark busy while a model is working
    def _beat(self) -> None:
        while True:
            time.sleep(HEARTBEAT)
            with self._lock:
                if self._depth == 0:
                    return
                self._write()

    def _main_enter(self) -> None:
        with self._lock:
            if not self._registered:  # only agents that really use the model show up as "main"
                self._registered = True
                atexit.register(self.close)
            self._depth += 1
            if self._depth == 1:
                self.s.update(busy=True, since=time.time())
                self._write()
                threading.Thread(target=self._beat, daemon=True).start()

    def _main_leave(self) -> None:
        with self._lock:
            self._depth -= 1
            if self._depth == 0:
                self.s.update(busy=False, since=time.time())
                self._write()

    # ---- side: wait for a free GPU, abort when main comes back
    def _acquire(self, label: str) -> None:
        fd = open(self.dir / "side.lock", "w")
        told = 0.0
        while True:
            busy, idle = main_state(self.dir)
            if not busy and idle >= self.min_idle:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    pass
            if not self.waiting:
                self.log(f"⏸ [{label}] waiting for a free GPU (main agent is working or another side agent is running)")
                self.waiting = True
            self._status("waiting", f"{label}: waiting until the main agent is idle for {self.min_idle:.0f}s")
            if time.time() - told > 120 and told:
                self.log(f"... [{label}] still waiting for the main agent to become idle")
            told = told or time.time()
            time.sleep(1.0)
        self._side_fd = fd
        if self.waiting:
            self.log(f"▶ [{label}] GPU is free, side work continues")
        self.waiting = False
        self._status("running", label)

    def _release(self) -> None:
        if self._side_fd:
            with contextlib.suppress(OSError):
                fcntl.flock(self._side_fd, fcntl.LOCK_UN)
                self._side_fd.close()
            self._side_fd = None

    def check(self) -> None:
        """Called while a model works: a side agent raises Preempted as soon as main needs the GPU."""
        if self.prio != "side" or time.time() - self._last_check < 0.4:
            return
        self._last_check = time.time()
        if main_state(self.dir)[0]:
            raise Preempted()

    @contextlib.contextmanager
    def gpu(self, label: str = ""):
        if self.prio == "main":
            self._main_enter()
            self._status("running", label)
            try:
                yield self
            finally:
                self._main_leave()
            return
        self._acquire(label)
        try:
            yield self
        except Preempted:
            self.log(f"⏸ [{label}] stopped: the main agent needs the GPU, will retry when it is idle")
            self._status("preempted", label)
            raise
        finally:
            self._release()


def make(cfg: dict, name: str, log, status_file: Path | None = None) -> Scheduler:
    sc = cfg.get("scheduler", {})
    prio = os.getenv("CODEFACTORY_PRIORITY") or sc.get("priority", "main")
    return Scheduler(name, prio, sc.get("min_idle", 20), log, status_file)
