"""GPU scheduling between several agents on one machine.

Agents that are not "side" share the model server fairly (smart queue, no marking needed): at most `slots` model calls
run at once, in first-come-first-served order, and calls run side by side only when they use the same model (so a 12 GB
card never swaps models back and forth). While one project builds or tests, another one gets the GPU, so it idles less.

Among them one agent can be the MAIN agent that keeps priority. Agents set to "side" only use the model server while the main agent
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
import itertools
import json
import os
import threading
import time
from pathlib import Path

_COUNTER = itertools.count()
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
    def gpu(self, label: str = "", model: str = ""):
        yield self

    def check(self) -> None:
        return


class Scheduler:
    def __init__(self, name: str, priority: str, min_idle: float, log, status_file: Path | None = None, directory: Path | None = None, slots: int = 2):
        self.name, self.prio, self.min_idle, self.log = name, priority if priority in ("main", "side") else "main", float(min_idle), log
        self.dir = directory or shared_dir()
        self.me = self.dir / f"{os.getpid()}-{next(_COUNTER)}.json"
        self.status_file = status_file
        self.slots = max(1, int(slots))
        self.waiting = False
        self.s = {"pid": os.getpid(), "name": name, "prio": self.prio, "busy": False, "since": time.time(), "ts": time.time(),
                  "hold": False, "wait": None, "model": ""}
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

    # ---- everyone but side: fair queue with model affinity
    def _peers(self) -> list[dict]:
        out, now = [], time.time()
        for f in self.dir.glob("*.json"):
            try:
                s = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if f == self.me or s.get("prio") != "main" or not _alive(int(s.get("pid", 0))) or now - s.get("ts", 0) > STALE:
                continue
            out.append(s)
        return out

    def _slot_acquire(self, label: str, model: str) -> None:
        model, since, told = norm_model(model), time.time(), False
        lock = open(self.dir / "admit.lock", "w")
        try:
            while True:
                fcntl.flock(lock, fcntl.LOCK_EX)  # decide and register atomically, so two agents never both take the last slot
                try:
                    peers = self._peers()
                    holders = [p for p in peers if p.get("hold")]
                    older = [p for p in peers if p.get("wait") and not p.get("hold") and (p["wait"], p["pid"]) < (since, os.getpid())]
                    fits = not holders or (len(holders) < self.slots and all(p.get("model") == model for p in holders))
                    if not older and fits:
                        self.s.update(hold=True, wait=None, model=model)
                        self._write()
                        break
                    self.s.update(hold=False, wait=since, model=model)
                    self._write()
                    who = ", ".join(sorted({p.get("name", "?") for p in holders})) or "another agent"
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                if not told:
                    self.log(f"⏳ [{label}] queued: the GPU is used by {who}; runs next (up to {self.slots} calls at once, same model only)")
                    told = self.waiting = True
                self._status("queued", f"{label}: waiting for {who}")
                time.sleep(0.7)
        finally:
            lock.close()
        if told:
            self.log(f"▶ [{label}] GPU slot free, continuing")
        self.waiting = False
        self._status("running", label)

    def _slot_release(self) -> None:
        self.s.update(hold=False, wait=None)

    @contextlib.contextmanager
    def gpu(self, label: str = "", model: str = ""):
        if self.prio == "main":
            outer = self._depth == 0
            self._main_enter()
            try:
                if outer:
                    self._slot_acquire(label, model)
                yield self
            finally:
                if outer:
                    self._slot_release()
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


def norm_model(name: str) -> str:
    """Aider and the OpenAI API name the same Ollama model differently (ollama_chat/x, ollama/x, x)."""
    for p in ("ollama_chat/", "ollama/", "openai/"):
        if name.startswith(p):
            return name[len(p):]
    return name


def make(cfg: dict, name: str, log, status_file: Path | None = None) -> Scheduler:
    sc = cfg.get("scheduler", {})
    prio = os.getenv("CODEFACTORY_PRIORITY") or sc.get("priority", "main")
    return Scheduler(name, prio, sc.get("min_idle", 20), log, status_file, slots=sc.get("slots", 2))
