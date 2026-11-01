#!/usr/bin/env python3
"""Local web UI for CodeFactory (standard library only, 127.0.0.1 only).

Start: `make ui` or `python3 webui.py [--port 8765] [--projects-dir projects]`.
The CLI stays fully usable; the UI calls the same commands (scripts/run.sh) and reads/writes the same files.
"""
from __future__ import annotations

import argparse
import hmac
import http.cookies
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dash import throughput, chat as chatlib
import codefactory as sa
from core import cfprofile
from dash import diagnostics
from infra import models
from infra import perf
from infra import qr
from dash import runledger
from infra import update

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "web" / "index.html"
MAX_BODY = 1_000_000
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,99}$")
FEATURE_KEYS = {"cleanup_pass": "bool", "require_tests": "bool", "plan_critique": "bool", "security": ("on", "off"), "e2e": ("auto", "on", "off"),
                "test_first": ("auto", "on", "off"), "step_by_step": ("auto", "on", "off"), "acceptance_check": "bool", "retry_replan": "bool", "protect_tests": "bool"}
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


class Service:
    def __init__(self, projects: Path):
        self.projects = projects.resolve()
        self.settings_path = self.projects / ".ui" / "settings.json"
        self.update_cache: dict | None = None
        self.master = ROOT / "MASTER.md"
        self.chats = chatlib.ChatStore(self.projects, lambda n: bool(NAME_RE.match(n or "")) and (self.projects / n / "agent" / "GOAL.md").exists())

    def ws(self, name: str) -> Path:
        if not NAME_RE.match(name or "") or not (self.projects / name / "agent" / "GOAL.md").exists():
            raise UiError("Project not found", 404)
        return self.projects / name

    def summary(self, ws: Path) -> dict:
        a = ws / "agent"
        state, backlog = sa.read_json(a / "state.json", {}), sa.read_json(a / "backlog.json", [])
        counts: dict[str, int] = {}
        for i in backlog:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        ideas = (ws / "IDEAS.md").read_text("utf-8") if (ws / "IDEAS.md").exists() else ""
        m = re.search(r"^Start idea:\s*(.+)$", ideas, re.M)
        running = pid_alive(a / "agent.pid") or alive(f"run:{ws.name}")
        log = tail(a / "agent.log", 1).strip()
        return {"name": ws.name, "idea": m.group(1) if m else "", "running": running,
                "stopping": running and (a / "STOP").exists(), "try_running": alive(f"try:{ws.name}"),
                "iteration": state.get("iteration", 0), "merged": state.get("merged", 0),
                "hours": round(state.get("elapsed", 0) / 3600, 2), "backlog": counts,
                "failures": state.get("consecutive_failures", 0), "last_log": log[-220:],
                "priority": os.getenv("CODEFACTORY_PRIORITY") or sa.read_json(a / "config.json", {}).get("scheduler", {}).get("priority", "main")}

    def list(self) -> dict:
        projects = [self.summary(p) for p in sa.list_projects(self.projects)]
        jobs = []
        for key, p in list(PROCS.items()):
            if key.startswith("init:") and not (self.projects / key[5:] / "agent" / "GOAL.md").exists():
                jobs.append({"name": key[5:], "running": p.poll() is None, "code": p.poll(),
                             "log": tail(self.projects / ".ui" / f"init-{key[5:]}.log", 8)})
        return {"projects": projects, "init_jobs": jobs, "master": bool(sa.read_master(self.master)), "model_server": self.model_server(),
                "chat_enabled": self.chat_enabled()}

    def chat_enabled(self) -> bool:
        return bool(sa.read_json(self.settings_path, {}).get("chat_enabled", True))

    def set_ui_settings(self, body: dict) -> dict:
        """UI preferences kept next to the chats. Turning the chat off frees the model server for the agent."""
        cur = sa.read_json(self.settings_path, {})
        if "chat_enabled" in body:
            cur["chat_enabled"] = bool(body["chat_enabled"])
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        sa.write_json(self.settings_path, cur)
        return {"chat_enabled": self.chat_enabled()}

    def require_chat(self) -> None:
        if not self.chat_enabled():
            raise UiError("The chat is turned off (it can compete with the agent for the model). Turn it on in the sidebar.", 403)

    def update_status(self, fetch: bool = False) -> dict:
        if fetch or self.update_cache is None:
            self.update_cache = update.status(ROOT, fetch=True)
            self.update_cache["checked"] = time.time()
        return self.update_cache

    def update_apply(self) -> dict:
        busy = [p["name"] for p in self.list()["projects"] if p["running"]]
        if busy:
            raise UiError("Stop the running agent(s) first: " + ", ".join(busy), 409)
        res = update.apply(ROOT)
        self.update_cache = None
        if res.get("updated") and res.get("restart"):
            threading.Timer(1.0, restart_self).start()
        return res

    def model_server(self) -> dict:
        """Is the model server reachable? Cached for 10 s because the UI polls."""
        c = getattr(self, "_ms", None)
        if c and time.time() - c[0] < 10:
            return c[1]
        api = sa.DEFAULT_CONFIG["llm_api"]
        models = self.installed_models(api)
        res = {"ok": bool(models), "models": len(models), "url": urllib.parse.urlparse(api).netloc}
        self._ms = (time.time(), res)
        return res

    def detail(self, name: str) -> dict:
        ws = self.ws(name)
        a = ws / "agent"
        feats = sa.read_json(a / "memory" / "features.json", {})
        backlog = sorted(sa.read_json(a / "backlog.json", []), key=lambda i: -i["priority"])
        read = lambda p: p.read_text("utf-8") if p.exists() else ""
        sm = self.summary(ws)
        live = self.live_rate(read_live(a / "llm_stream.json", sm["running"]))
        if not live["active"] and sm["running"]:
            live = self.coder_rate(name, a) or live
        return {**sm, "perf": throughput.perf(sa.read_json(a / "metrics.json", {})), "live": live, "gpu": self.gpu_stats(), "ideas": read(ws / "IDEAS.md"), "goal": read(a / "GOAL.md"), "testing": read(ws / "TESTING.md"), "sources": read(ws / "SOURCES.md"), "web_mode": sa.read_json(a / "config.json", {}).get("web", {}).get("mode", "sources"),
                "activity": self.activity(ws), "log": tail(a / "agent.log"), "run_out": tail(a / "run.out", 15), "do_out": tail(a / "do.out", 15), "try_out": tail(a / "try.out", 15),
                "features": [{"id": k, "title": v.get("title", k)} if isinstance(v, dict) else {"id": k, "title": str(v)}
                             for k, v in feats.items()],
                "backlog_items": [{k: i.get(k) for k in ("id", "title", "type", "priority", "status", "source", "attempts")}
                                  for i in backlog]}

    def activity(self, ws: Path) -> dict:
        """What the agent is doing right now: phase, task, current model/Aider call and the recent event timeline."""
        a = ws / "agent"
        running = pid_alive(a / "agent.pid") or alive(f"run:{ws.name}") or alive(f"do:{ws.name}")
        events = []
        ep = a / "events.jsonl"
        if ep.exists():
            for line in ep.read_text("utf-8").splitlines()[-40:]:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
        return {"running": running, "now": sa.read_json(a / "activity.json", None) if running else None,
                "llm": read_live(a / "llm_now.json", running), "coder": read_live(a / "coder_now.json", running), "cmd": read_live(a / "cmd_now.json", running), "sched": read_live(a / "sched.json", running),
                "events": events, "queued_user_tasks": [i["title"] for i in sa.read_json(a / "backlog.json", [])
                                                          if i.get("source") == "user" and i["status"] in ("pending", "in_progress")]}

    def llm_log(self, name: str) -> dict:
        text = tail(self.ws(name) / "agent" / "llm.log", 4000, 400_000)
        entries = []
        for chunk in text.split("\n===== ")[1:][-25:]:
            head, _, rest = chunk.partition(" =====\n")
            prompt, _, answer = rest.partition("\n-- answer --\n")
            entries.append({"head": head, "prompt": prompt.replace("-- prompt (end) --\n", "", 1), "answer": answer.strip()})
        return {"entries": entries[::-1]}

    def thoughts(self, name: str, limit: int = 60) -> dict:
        """Every model call in full (prompt, reasoning, answer), plus what is being streamed right now."""
        a = self.ws(name) / "agent"
        rows = []
        for line in tail(a / "thoughts.jsonl", 400, 3_000_000).splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        running = pid_alive(a / "agent.pid") or alive(f"run:{name}") or alive(f"do:{name}")
        live = tail(a / "coder_live.txt", 120, 60_000) if running else ""
        archived = 0
        for f in (a / "thoughts-archive").glob("*.jsonl.gz"):
            lo, _, hi = f.name.split(".")[0].partition("-")
            archived += int(hi) - int(lo) + 1 if lo.isdigit() and hi.isdigit() else 0
        return {"entries": rows[::-1], "archived": archived, "stream": read_live(a / "llm_stream.json", running), "coder_live": live,
                "cmd": read_live(a / "cmd_now.json", running), "cmd_live": tail(a / "cmd_live.txt", 120, 60_000) if running else ""}

    def stream(self, name: str) -> dict:
        """Lightweight live state for the fast poll: only what is being written right now."""
        a = self.ws(name) / "agent"
        running = pid_alive(a / "agent.pid") or alive(f"run:{name}") or alive(f"do:{name}")
        return {"stream": read_live(a / "llm_stream.json", running), "llm": read_live(a / "llm_now.json", running),
                "coder_live": tail(a / "coder_live.txt", 400, 120_000) if running else "",
                "cmd_live": tail(a / "cmd_live.txt", 400, 120_000) if running else ""}

    @staticmethod
    def live_rate(st: dict | None, now_t: float | None = None) -> dict:
        """Live numbers of one LLM call from llm_stream.json; tokens/s is 0 once the stream has been quiet for 3 s."""
        now_t = now_t or time.time()
        if not st:
            return {"active": False, "tokens": 0, "chars": 0, "tps": 0.0}
        tokens, chars, t0, upd = st.get("tokens", 0), st.get("chars", 0), st.get("t0"), st.get("updated", 0)
        fresh = now_t - upd < 3 and bool(t0)
        tps = round(tokens / max(upd - t0, 0.5), 1) if fresh else 0.0
        return {"active": True, "tokens": tokens, "chars": chars, "tps": tps, "role": st.get("role", ""), "tag": st.get("tag", ""),
                "model": st.get("model", ""), "since": st.get("since"), "streaming": fresh}

    def coder_rate(self, name: str, a: Path) -> dict | None:
        """Aider gives no token counter while it works: estimate tokens/s from how fast its output file grows (~4 chars per token)."""
        now_t, now_j = time.time(), sa.read_json(a / "coder_now.json", None)
        try:
            size = (a / "coder_live.txt").stat().st_size
        except OSError:
            size = None
        if not now_j or size is None:
            return None
        hist = self.__dict__.setdefault("_cgrow", {})
        prev = hist.get(name)
        if not prev or prev[2] != now_j.get("since") or size < prev[0]:
            prev = (size, now_t, now_j.get("since"), 0.0, now_t)
        dt = now_t - prev[1]
        rate = prev[3]
        if dt >= 1.0:
            rate = 0.6 * prev[3] + 0.4 * (size - prev[0]) / 4 / dt
            prev = (size, now_t, now_j.get("since"), rate, now_t)
        elif size != prev[0]:
            pass
        hist[name] = prev
        return {"active": True, "tokens": size // 4, "chars": size, "tps": round(rate, 1), "role": "coder", "tag": now_j.get("tag", ""),
                "model": now_j.get("model", ""), "since": now_j.get("since"), "streaming": rate > 0, "estimated": True}

    @staticmethod
    def gpu_stats() -> dict | None:
        c = getattr(Service, "_gpu", None)
        if c and time.time() - c[0] < 1.5:
            return c[1]
        res = None
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2).stdout.strip().splitlines()[0]
            n, u, mu, mt, t = [x.strip() for x in out.split(",")]
            res = {"name": n, "util": int(u), "mem_used": int(mu), "mem_total": int(mt), "temp": int(t)}
        except Exception:  # noqa: BLE001
            res = None
        Service._gpu = (time.time(), res)
        return res

    @staticmethod
    def loaded_models() -> list[dict]:
        u = urllib.parse.urlparse(sa.DEFAULT_CONFIG["llm_api"])
        try:
            with urllib.request.urlopen(f"{u.scheme}://{u.netloc}/api/ps", timeout=1.5) as r:
                return [{"name": m.get("name", ""), "vram_gb": round(m.get("size_vram", 0) / 1e9, 1)} for m in json.loads(r.read()).get("models", [])]
        except Exception:  # noqa: BLE001
            return []

    def dashboard(self) -> dict:
        """Everything the Dashboard shows: running agents, live token/char rates, totals, GPU and loaded models."""
        rows, tot = [], {"calls": 0, "tokens": 0, "chars": 0, "day_calls": 0, "day_tokens": 0, "day_chars": 0}
        sources = [(p.name, p, self.summary(p)) for p in sa.list_projects(self.projects)]
        for name, ws, summ in sources:
            a = ws / "agent"
            live = self.live_rate(read_live(a / "llm_stream.json", summ["running"]))
            if not live["active"] and summ["running"]:
                live = self.coder_rate(name, a) or live
            m = sa.read_json(a / "metrics.json", {})
            for k in tot:
                tot[k] += m.get(k, 0)
            sched = sa.read_json(a / "sched.json", {}) if summ["running"] else {}
            rows.append({"name": name, "running": summ["running"], "priority": summ["priority"], "iteration": summ["iteration"],
                         "state": sched.get("state", ""), "live": live, "perf": throughput.perf(m), "calls": m.get("calls", 0), "tokens": m.get("tokens", 0),
                         "chars": m.get("chars", 0), "coder_chars": (a / "coder_live.txt").stat().st_size if summ["running"] and (a / "coder_live.txt").exists() else 0})
        tool_on = alive("tool")
        if tool_on:
            rows.append({"name": "Self-improve", "tool": True, "running": True, "priority": "main", "iteration": 0, "state": "", "perf": throughput.perf(sa.read_json(ROOT / "self_improve" / "metrics.json", {})),
                         "live": self.live_rate(read_live(ROOT / "self_improve" / "llm_stream.json", True)), **{k: sa.read_json(ROOT / "self_improve" / "metrics.json", {}).get(k, 0) for k in ("calls", "tokens", "chars")}, "coder_chars": 0})
        running = [r for r in rows if r["running"]]
        return {"time": time.time(), "projects": rows,
                "agents_running": len(running), "calls_active": sum(1 for r in running if r["live"]["active"]),
                "tps": round(sum(r["live"]["tps"] for r in running), 1),
                "live_tokens": sum(r["live"]["tokens"] for r in running), "live_chars": sum(r["live"]["chars"] for r in running),
                "totals": tot, "gpu": self.gpu_stats(), "models": self.loaded_models()}

    def diagnostics(self) -> dict:
        """Slow-moving figures for the Dashboard (flow, quality, model time, health, repo, system), cached for a few seconds."""
        c = getattr(self, "_diag", None)
        if c and time.time() - c[0] < 10:
            return c[1]
        projects = []
        for p in sa.list_projects(self.projects):
            summ = self.summary(p)
            projects.append({**diagnostics.project(p, summ["running"], summ["hours"]), "running": summ["running"], "merged": summ["merged"]})
        oc = getattr(Service, "_oll", None)
        if not oc or time.time() - oc[0] > 30:
            Service._oll = oc = (time.time(), perf.diagnose())
        sysinfo = diagnostics.system(oc[1])
        res = {"time": time.time(), "projects": projects, "system": sysinfo, "findings": diagnostics.findings(projects, sysinfo),
               "tool": {**runledger.stats(ROOT / "self_improve"), "durations": diagnostics.tool_durations(ROOT / "self_improve")}}
        self._diag = (time.time(), res)
        return res

    def tool_stream(self) -> dict:
        d, running = ROOT / "self_improve", alive("tool")
        return {"stream": read_live(d / "llm_stream.json", running), "llm": read_live(d / "llm_now.json", running),
                "coder_live": tail(d / "coder_live.txt", 400, 120_000) if running else "",
                "cmd_live": tail(d / "cmd_live.txt", 400, 120_000) if running else ""}

    def _base(self, repo: Path, base: str) -> str:
        """Maps the UI's comparison choice to a git revision ('head' = uncommitted work, 'last:N' = last N commits)."""
        if not git(repo, "rev-parse", "--verify", "-q", "HEAD").strip():
            return EMPTY_TREE
        m = re.fullmatch(r"last:(\d{1,3})", base or "")
        if not m:
            return "HEAD"
        n = int(m.group(1))
        return f"HEAD~{n}" if git(repo, "rev-parse", "--verify", "-q", f"HEAD~{n}").strip() else EMPTY_TREE

    def tree(self, name: str, base: str = "head") -> dict:
        """All files of the product repo with what changed relative to the chosen base."""
        repo = self.ws(name) / "repo"
        if not (repo / ".git").exists():
            return {"files": [], "base": base}
        rev = self._base(repo, base)
        names = git(repo, "ls-files", "-z", "-c", "-o", "--exclude-standard", limit=4_000_000).split("\0")
        files = {n: {"path": n, "status": "", "add": 0, "del": 0} for n in names if n and not n.startswith(".git/")}
        for line in git(repo, "diff", "--numstat", "--no-renames", rev, limit=4_000_000).splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] in files:
                f = files[parts[2]]
                f["add"] = int(parts[0]) if parts[0].isdigit() else 0
                f["del"] = int(parts[1]) if parts[1].isdigit() else 0
                f["status"] = "M"
        tracked = set(git(repo, "ls-files", "-z", limit=4_000_000).split("\0"))
        for line in git(repo, "diff", "--name-status", "--no-renames", rev, limit=4_000_000).splitlines():
            st, _, path = line.partition("\t")
            if path in files and st.startswith("A"):
                files[path]["status"] = "A"
        for path, f in files.items():
            if path not in tracked:
                f["status"] = "A"
                f["add"] = -1  # untracked: the whole file is new
        return {"files": sorted(files.values(), key=lambda f: f["path"])[:5000], "base": base}

    def file(self, name: str, path: str, base: str = "head") -> dict:
        """Contents of one file with the line numbers that are new or changed relative to the base."""
        repo = (self.ws(name) / "repo").resolve()
        target = (repo / path).resolve()
        if not path or target == repo or repo not in target.parents or ".git" in Path(path).parts or not target.is_file():
            raise UiError("File not found")
        size = target.stat().st_size
        data = target.read_bytes()[:MAX_FILE]
        if b"\0" in data[:8000]:
            return {"path": path, "binary": True, "size": size}
        text = data.decode("utf-8", "replace")
        rel = target.relative_to(repo).as_posix()
        tracked = bool(git(repo, "ls-files", "--", rel).strip())
        changed: list[int] = []
        if not tracked:
            changed = list(range(1, text.count("\n") + 2))
        else:
            for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git(repo, "diff", "-U0", "--no-renames", self._base(repo, base), "--", rel), re.M):
                start, cnt = int(m.group(1)), int(m.group(2) or 1)
                changed.extend(range(start, start + cnt))
        return {"path": path, "text": text, "size": size, "truncated": size > MAX_FILE, "changed": changed, "new_file": not tracked}

    def changes(self, name: str) -> dict:
        repo = self.ws(name) / "repo"
        if not (repo / ".git").exists():
            return {"commits": [], "files": [], "uncommitted": []}
        raw = git(repo, "log", "-n", "80", "--numstat", "--format=%x1e%h%x1f%s%x1f%cI")
        commits, totals = [], {}
        for block in raw.split("\x1e")[1:]:
            head, _, stats = block.partition("\n")
            sha, subject, date = (head.split("\x1f") + ["", ""])[:3]
            files = []
            for line in stats.splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    add = int(parts[0]) if parts[0].isdigit() else 0
                    rem = int(parts[1]) if parts[1].isdigit() else 0
                    files.append({"path": parts[2], "add": add, "del": rem})
                    t = totals.setdefault(parts[2], {"path": parts[2], "add": 0, "del": 0, "commits": 0})
                    t["add"] += add
                    t["del"] += rem
                    t["commits"] += 1
            commits.append({"sha": sha, "subject": subject, "date": date, "files": files})
        status = [l for l in git(repo, "status", "--porcelain").splitlines() if l.strip()]
        return {"commits": commits, "files": sorted(totals.values(), key=lambda t: -(t["add"] + t["del"])),
                "uncommitted": status[:100]}

    def diff(self, name: str, sha: str, path: str) -> dict:
        repo = self.ws(name) / "repo"
        if not SHA_RE.match(sha or "") or path.startswith("-") or ".." in Path(path).parts:
            raise UiError("Invalid commit or path")
        args = ["show", "--format=%h %s%n%cI", "--patch", sha]
        return {"diff": git(repo, *args, *(["--", path] if path else []))}

    def tool(self) -> dict:
        """Tasks for the tool itself (loop, UI, Makefile ...), executed on review branches, never merged automatically."""
        branches = []
        for line in git(ROOT, "for-each-ref", "refs/heads/self-improve/", "--sort=-creatordate",
                        "--format=%(refname:short)\x1f%(creatordate:iso8601)\x1f%(subject)").splitlines()[:15]:
            name, date, subject = (line.split("\x1f") + ["", ""])[:3]
            stat = git(ROOT, "diff", "--stat", f"HEAD...{name}").strip().splitlines()
            branches.append({"name": name, "date": date, "subject": subject, "stat": stat[-12:], **self.branch_status(name)})
        head = head_sha()
        perf = throughput.perf(sa.read_json(ROOT / "self_improve" / "metrics.json", {}))
        live = self.live_rate(read_live(ROOT / "self_improve" / "llm_stream.json", alive("tool")))
        return {"perf": perf, "live": live, "gpu": self.gpu_stats(), "tool_durations": diagnostics.tool_durations(ROOT / "self_improve"),
                "running": alive("tool"), "log": tail(ROOT / "self_improve" / "ui.log", 60), "branches": branches, "preview": self.preview_state(),
                "runs": runledger.list_runs(ROOT / "self_improve"), "stats": runledger.stats(ROOT / "self_improve"), "build": {"sha": BUILD["sha"], "head": head, "restart_needed": bool(BUILD["sha"] and head != BUILD["sha"])},
                "last_merge": sa.read_json(ROOT / "self_improve" / "last_merge.json", None)}

    def branch_status(self, name: str) -> dict:
        """How a result branch relates to the current code: commits ahead/behind, merged already, merges cleanly, what was asked."""
        def count(rng: str) -> int:
            out = git(ROOT, "rev-list", "--count", rng).strip()
            return int(out) if out.isdigit() else 0

        ahead, behind = count(f"HEAD..{name}"), count(f"{name}..HEAD")
        mergeable = None
        if ahead:
            rc, _ = git_rc(ROOT, "merge-tree", "--write-tree", "HEAD", name)
            mergeable = True if rc == 0 else False if rc == 1 else None
        run = runledger.run_for_branch(ROOT / "self_improve", name) or {}
        reqs = run.get("requirements", [])
        return {"ahead": ahead, "behind": behind, "merged": ahead == 0, "mergeable": mergeable, "run_id": run.get("id"),
                "req_done": sum(1 for r in reqs if r.get("status") == "done"), "req_total": len(reqs),
                "tests_added": len(run.get("tests_added", [])), "task": (run.get("instruction") or "").strip()[:300]}

    def _check_branch(self, body: dict) -> str:
        branch = str(body.get("branch", ""))
        if not BRANCH_RE.match(branch) or not git(ROOT, "rev-parse", "--verify", "-q", branch).strip():
            raise UiError("Unknown branch")
        return branch

    def tool_cancel(self) -> dict:
        """Stops the running self-change and cleans up: worktree removed, unfinished branch deleted, run marked cancelled."""
        if not alive("tool"):
            raise UiError("No self-change is running", 409)
        sa.force_stop_pid(PROCS["tool"].pid, grace=3.0)
        out = ROOT / "self_improve"
        for f in ("phase.json", "coder_now.json", "cmd_now.json", "llm_now.json", "llm_stream.json"):
            (out / f).unlink(missing_ok=True)
        run = runledger.active_run(out)
        kept = False
        if run:
            wt = out / "work" / run["id"]
            if wt.exists():
                git_rc(ROOT, "worktree", "remove", "--force", str(wt))
                shutil.rmtree(wt, ignore_errors=True)
            git_rc(ROOT, "worktree", "prune")
            branch = f"self-improve/{run['id']}"
            if git(ROOT, "rev-parse", "--verify", "-q", branch).strip():
                kept = git(ROOT, "rev-list", "--count", f"HEAD..{branch}").strip() not in ("", "0")
                if not kept:
                    git_rc(ROOT, "branch", "-D", branch)
            runledger.mark_run(out, run["id"], "cancelled", "Cancelled by you" + ("; commits made so far are kept on the branch" if kept else ""))
        with open(out / "ui.log", "a", encoding="utf-8") as f:
            f.write("Cancelled by user.\n")
        return {"ok": True, "kept_branch": kept}

    def tool_discard(self, body: dict) -> dict:
        branch = self._check_branch(body)
        if alive("tool"):
            raise UiError("Wait for the running self-change or cancel it first", 409)
        if sa.read_json(self.PREVIEW.parent / "preview.json", {}).get("branch") == branch:
            self.preview_stop()
        rc, out = git_rc(ROOT, "branch", "-D", branch)
        if rc != 0:
            raise UiError(out[-300:])
        run = runledger.run_for_branch(ROOT / "self_improve", branch)
        if run:
            runledger.mark_run(ROOT / "self_improve", run["id"], "discarded")
        return {"ok": True, "message": f"Deleted {branch}"}

    def tool_update_branch(self, body: dict) -> dict:
        """Merges the current code into a result branch (in a temporary checkout) so it stays mergeable when main moved on."""
        branch = self._check_branch(body)
        if alive("tool"):
            raise UiError("Wait for the running self-change or cancel it first", 409)
        if sa.read_json(self.PREVIEW.parent / "preview.json", {}).get("branch") == branch:
            self.preview_stop()
        head = head_sha()
        tmp = ROOT / "self_improve" / "update-wt"
        git_rc(ROOT, "worktree", "remove", "--force", str(tmp))
        shutil.rmtree(tmp, ignore_errors=True)
        rc, out = git_rc(ROOT, "worktree", "add", "--detach", str(tmp), branch)
        if rc != 0:
            raise UiError("Could not check the branch out: " + out[-200:])
        try:
            rc, out = git_rc(tmp, "-c", "user.name=codefactory", "-c", "user.email=agent@localhost", "merge", "--no-edit", head)
            if rc != 0:
                files = git_rc(tmp, "diff", "--name-only", "--diff-filter=U")[1].splitlines()
                git_rc(tmp, "merge", "--abort")
                raise UiError("Conflicts with the current code in: " + ", ".join(files[:6]) + ". The branch was not changed. "
                              "Run the task again on the current code or resolve it by hand.", 409)
            new = git_rc(tmp, "rev-parse", "HEAD")[1]
            git_rc(ROOT, "update-ref", f"refs/heads/{branch}", new)
        finally:
            git_rc(ROOT, "worktree", "remove", "--force", str(tmp))
            shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": True, "message": f"{branch} now contains the current code. Try it again before you merge."}

    # ---- try a self-improve branch locally before merging: separate checkout, separate port, empty projects folder
    PREVIEW = ROOT / "self_improve" / "preview"

    def preview_state(self) -> dict:
        info = sa.read_json(self.PREVIEW.parent / "preview.json", {})
        rc = self.PREVIEW.parent / "preview-tests.rc"
        if not alive("ptest") and rc.exists() and "tests_rc" not in info:
            info["tests_rc"] = int((rc.read_text().strip() or "1"))
            rc.unlink(missing_ok=True)
            sa.write_json(self.PREVIEW.parent / "preview.json", info)
        up = alive("preview")
        return {"branch": info.get("branch") if up or alive("ptest") else None, "running": up, "port": info.get("port") if up else None,
                "verify": info.get("verify") if up else None, "tests_running": alive("ptest"), "tests_rc": None if alive("ptest") else info.get("tests_rc"),
                "log": tail(self.PREVIEW.parent / "preview.log", 40), "tests_log": tail(self.PREVIEW.parent / "preview-tests.log", 40)}

    def tool_run(self, run_id: str, branch: str = "") -> dict:
        """The plan/todo ledger of one run (latest if none given; the run that produced `branch` if a branch is given)."""
        out = ROOT / "self_improve"
        d = runledger.run_for_branch(out, branch) if branch else None
        if d is None:
            runs = runledger.list_runs(out, 1)
            d = runledger.load_run(out, run_id or (runs[0]["id"] if runs else ""))
        return {"run": d, "phase": sa.read_json(out / "phase.json", None), "running": alive("tool")}

    def _verify_preview(self, branch: str, port: int, out: Path) -> None:
        info = sa.read_json(out / "preview.json", {})
        info["verify"] = {"state": "running", "checks": []}
        sa.write_json(out / "preview.json", info)
        checks = runledger.verify_checkout(ROOT, self.PREVIEW, branch, port, alive=lambda: alive("preview"))
        info = sa.read_json(out / "preview.json", {})
        if info.get("branch") == branch and info.get("port") == port:  # skip if the preview was replaced meanwhile
            info["verify"] = {"state": "done", "ok": all(c["ok"] for c in checks), "checks": checks, "at": time.time()}
            sa.write_json(out / "preview.json", info)

    def preview_stop(self) -> dict:
        for key in ("preview", "ptest"):
            p = PROCS.get(key)
            if p and p.poll() is None:
                sa.force_stop_pid(p.pid, grace=2.0)
        if self.PREVIEW.exists():
            git(ROOT, "worktree", "remove", "--force", str(self.PREVIEW))
            shutil.rmtree(self.PREVIEW, ignore_errors=True)
        return self.preview_state()

    def preview_start(self, body: dict) -> dict:
        """Checks the branch out next to the running tool and starts its UI on a free port (never touches your projects or main)."""
        branch = str(body.get("branch", ""))
        if not BRANCH_RE.match(branch) or not git(ROOT, "rev-parse", "--verify", "-q", branch).strip():
            raise UiError("Unknown branch")
        self.preview_stop()
        out = self.PREVIEW.parent
        out.mkdir(parents=True, exist_ok=True)
        git(ROOT, "worktree", "prune")
        if "error" in git(ROOT, "worktree", "add", "--detach", str(self.PREVIEW), branch).lower() and not self.PREVIEW.exists():
            raise UiError("Could not check the branch out")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        sandbox = out / "preview-projects"
        sandbox.mkdir(exist_ok=True)
        log = open(out / "preview.log", "w", encoding="utf-8")
        sa.write_json(out / "preview.json", {"branch": branch, "port": port})
        PROCS["preview"] = subprocess.Popen([sys.executable, "webui.py", "--port", str(port), "--projects-dir", str(sandbox), "--no-browser"],
                                            cwd=str(self.PREVIEW), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=True, env={**os.environ, "CODEFACTORY_GPU_DIR": str(out / "preview-gpu")})
        if body.get("tests"):
            self.preview_tests()
        threading.Thread(target=self._verify_preview, args=(branch, port, out), daemon=True).start()
        time.sleep(1.2)
        return self.preview_state()

    @staticmethod
    def _git_rc(*args: str) -> tuple[int, str]:
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()

    def tool_merge(self, body: dict) -> dict:
        """Merges a tested self-improve branch into the checked-out branch of the tool repo (with a backup tag and an undo)."""
        branch = str(body.get("branch", ""))
        if not BRANCH_RE.match(branch) or not git(ROOT, "rev-parse", "--verify", "-q", branch).strip():
            raise UiError("Unknown branch")
        busy = [p["name"] for p in self.list()["projects"] if p["running"]] + (["the tool itself"] if alive("tool") else [])
        if busy:
            raise UiError("Stop the running agent(s) first: " + ", ".join(busy), 409)
        if git(ROOT, "status", "--porcelain").strip():
            raise UiError("The tool folder has uncommitted changes; commit or stash them first", 409)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tag = f"pre-merge-{stamp}"
        self._git_rc("tag", tag)
        rc, out = self._git_rc("merge", "--no-ff", "-m", f"Merge {branch} (tested in preview)", branch)
        if rc != 0:
            self._git_rc("merge", "--abort")
            self._git_rc("tag", "-d", tag)
            raise UiError("Merge not possible (conflict, main moved on). Nothing was changed.\n" + out[-300:], 409)
        sa.write_json(ROOT / "self_improve" / "last_merge.json", {"tag": tag, "sha": git(ROOT, "rev-parse", "HEAD").strip(), "branch": branch})
        return {"ok": True, "tag": tag, "message": f"Merged {branch}. Restart the UI (make ui) to use it. Undo: the Undo button or git reset --hard {tag}"}

    def tool_undo_merge(self) -> dict:
        info = sa.read_json(ROOT / "self_improve" / "last_merge.json", None)
        if not info:
            raise UiError("No merge to undo", 404)
        if git(ROOT, "status", "--porcelain").strip():
            raise UiError("The tool folder has uncommitted changes", 409)
        if git(ROOT, "rev-parse", "HEAD").strip() != info["sha"]:
            raise UiError("The repository has moved on since that merge; undo it with git revert -m 1 " + info["sha"][:8], 409)
        rc, out = self._git_rc("reset", "--hard", info["tag"])
        if rc != 0:
            raise UiError(out[-300:])
        (ROOT / "self_improve" / "last_merge.json").unlink(missing_ok=True)
        return {"ok": True, "message": f"Undone: back at {info['tag']}. Restart the UI (make ui)."}

    def preview_tests(self) -> dict:
        if not self.PREVIEW.exists():
            raise UiError("Start the preview first", 409)
        if alive("ptest"):
            return self.preview_state()
        out = self.PREVIEW.parent
        info = sa.read_json(out / "preview.json", {})
        info.pop("tests_rc", None)
        sa.write_json(out / "preview.json", info)
        f = open(out / "preview-tests.log", "w", encoding="utf-8")
        script = (f"{shlex.quote(sys.executable)} -m unittest discover -s tests && {shlex.quote(sys.executable)} codefactory.py selftest; "
                  f"echo $? > {shlex.quote(str(out / 'preview-tests.rc'))}")
        PROCS["ptest"] = subprocess.Popen(["bash", "-c", script], cwd=str(self.PREVIEW), stdin=subprocess.DEVNULL, stdout=f,
                                          stderr=subprocess.STDOUT, start_new_session=True,
                                          env={**os.environ, "CODEFACTORY_GPU_DIR": str(out / "preview-gpu")})
        return self.preview_state()

    def tool_activity(self) -> dict:
        """Live state of a self-change: stage, streamed model output, Aider/test output and every recorded thought."""
        d = ROOT / "self_improve"
        running = alive("tool")
        rows = []
        for line in tail(d / "thoughts.jsonl", 400, 3_000_000).splitlines()[-60:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return {"running": running, "phase": read_live(d / "phase.json", running), "llm": read_live(d / "llm_now.json", running),
                "coder": read_live(d / "coder_now.json", running), "cmd": read_live(d / "cmd_now.json", running),
                "stream": read_live(d / "llm_stream.json", running), "entries": rows[::-1],
                "coder_live": tail(d / "coder_live.txt", 120, 60_000) if running else "",
                "cmd_live": tail(d / "cmd_live.txt", 120, 60_000) if running else ""}

    def tool_tree(self, branch: str) -> dict:
        """Files a result branch changes compared with the running version."""
        if not BRANCH_RE.match(branch or ""):
            raise UiError("Invalid branch")
        files = {}
        for line in git(ROOT, "diff", "--numstat", "--no-renames", f"HEAD...{branch}", limit=2_000_000).splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                files[parts[2]] = {"path": parts[2], "status": "M", "add": int(parts[0]) if parts[0].isdigit() else 0,
                                   "del": int(parts[1]) if parts[1].isdigit() else 0}
        for line in git(ROOT, "diff", "--name-status", "--no-renames", f"HEAD...{branch}").splitlines():
            st, _, path = line.partition("\t")
            if path in files and st.startswith("A"):
                files[path]["status"] = "A"
        return {"files": sorted(files.values(), key=lambda f: f["path"]), "base": branch}

    def tool_file(self, branch: str, path: str) -> dict:
        if not BRANCH_RE.match(branch or "") or not path or path.startswith("-") or ".." in Path(path).parts:
            raise UiError("File not found")
        ref = f"{branch}:{path}"
        if git(ROOT, "cat-file", "-t", ref).strip() != "blob":
            raise UiError("File not found")
        text = git(ROOT, "show", ref, limit=MAX_FILE)
        changed: list[int] = []
        for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git(ROOT, "diff", "-U0", "--no-renames", f"HEAD...{branch}", "--", path), re.M):
            start, cnt = int(m.group(1)), int(m.group(2) or 1)
            changed.extend(range(start, start + cnt))
        return {"path": path, "text": text, "size": len(text), "truncated": False, "changed": changed,
                "new_file": not git(ROOT, "cat-file", "-t", f"HEAD:{path}").strip()}

    def tool_diff(self, branch: str) -> dict:
        if not BRANCH_RE.match(branch or ""):
            raise UiError("Invalid branch")
        return {"diff": git(ROOT, "diff", f"HEAD...{branch}")}

    def tool_task(self, body: dict) -> dict:
        task = str(body.get("task", "")).strip()
        if len(task) < 10:
            raise UiError("Describe what the tool should do in a sentence or two")
        if alive("tool"):
            raise UiError("A tool task is already running", 409)
        out = ROOT / "self_improve" / "ui.log"
        out.parent.mkdir(exist_ok=True)
        out.write_text("", "utf-8")
        spawn("tool", ["improve", "--task", task], out)
        return {"ok": True}

    def act(self, name: str, action: str, body: dict) -> dict:
        ws = self.ws(name)
        a = ws / "agent"
        if action == "start":
            if self.summary(ws)["running"]:
                raise UiError("Agent is already running", 409)
            spawn(f"run:{name}", ["run", name], a / "run.out")
        elif action == "stop":
            sa.write_text(a / "STOP", "ui\n")
        elif action == "force-stop":
            return {"ok": True, "message": sa.Agent(ws).force_stop()}
        elif action == "try":
            if alive(f"try:{name}"):
                raise UiError("A test version is already being built", 409)
            (a / "try.out").write_text("", "utf-8")
            spawn(f"try:{name}", ["try", name], a / "try.out")
        elif action == "do":
            task = str(body.get("task", "")).strip()
            if len(task) < 5:
                raise UiError("Describe the task")
            if self.summary(ws)["running"]:
                sa.Agent(ws).add_user_task(task)  # the running agent takes it next, before anything else
                return {"ok": True, "queued": True}
            side = bool(body.get("side"))  # one-off side job: only uses the GPU while the main agent is idle
            spawn(f"do:{name}", ["do", name, task], a / "do.out", {"CODEFACTORY_PRIORITY": "side"} if side else None)
            return {"ok": True, "queued": False, "side": side}
        elif action == "say":
            msg = str(body.get("msg", "")).strip()
            if not msg:
                raise UiError("Message is empty")
            sa.Agent(ws).add_feedback(msg)
        elif action in ("task-cancel", "task-reopen", "task-top"):
            self.backlog_edit(ws, action, body)
        elif action == "settings":
            self.save_settings(ws, body)
            return {"ok": True, "applies": "next start" if self.summary(ws)["running"] else "now"}
        elif action in ("rename", "delete"):
            if self.summary(ws)["running"] or any(alive(f"{k}:{name}") for k in ("run", "do", "try")):
                raise UiError("Stop the agent first (Stop or Force stop)", 409)
            return self.rename(ws, body.get("name")) if action == "rename" else self.delete(ws, body)
        elif action == "profile":
            self.guard_profile(lambda: cfprofile.save_profile(a, body))
            self.save_features(ws, body.get("features"))
        elif action == "context-add":
            return {"ok": True, **self.guard_profile(lambda: cfprofile.add_context(a, body.get("name"), str(body.get("text", ""))))}
        elif action == "context-delete":
            self.guard_profile(lambda: cfprofile.delete_context(a, body.get("name")))
        elif action == "goal":
            sa.write_text(a / "GOAL.md", str(body.get("text", "")))
        elif action == "ideas":
            sa.write_text(ws / "IDEAS.md", str(body.get("text", "")))
        elif action == "sources":
            sa.write_text(ws / "SOURCES.md", str(body.get("text", "")))
        else:
            raise UiError("Unknown action", 404)
        return {"ok": True}

    @staticmethod
    def guard_profile(fn):
        try:
            return fn()
        except cfprofile.ProfileError as e:
            raise UiError(str(e)) from None

    def profile(self, name: str) -> dict:
        ws = self.ws(name)
        eff = sa.Agent(ws).cfg
        return {**cfprofile.overview(ws / "agent"), "features": {k: eff["features"].get(k) for k in FEATURE_KEYS}, "web_mode": eff["web"]["mode"],
                "sources": (ws / "SOURCES.md").read_text("utf-8") if (ws / "SOURCES.md").exists() else ""}

    @staticmethod
    def save_features(ws: Path, feats) -> None:
        if not isinstance(feats, dict):
            return
        conf = sa.read_json(ws / "agent" / "config.json", {})
        for k, v in feats.items():
            kind = FEATURE_KEYS.get(k)
            if kind is None:
                raise UiError(f"Unknown capability: {k}")
            if kind == "bool":
                conf.setdefault("features", {})[k] = bool(v)
            elif v not in kind:
                raise UiError(f"{k} must be one of {', '.join(kind)}")
            else:
                conf.setdefault("features", {})[k] = v
        sa.write_json(ws / "agent" / "config.json", conf)

    def create(self, body: dict) -> dict:
        idea = str(body.get("idea", "")).strip()
        if len(idea) < 5:
            raise UiError("Please describe the idea in a sentence")
        preset = str(body.get("preset") or "auto")
        if preset != "auto" and (preset not in sa.PRESETS or preset == "minimal"):
            raise UiError("Unknown language/stack")
        missing = sa.stacks.missing_tools(preset) if preset != "auto" else []
        if missing:
            raise UiError(sa.stacks.install_help(preset))
        name = sa.unique_slug(self.projects, str(body.get("name") or "").strip() or idea)
        if alive(f"init:{name}"):
            raise UiError("Project is already being created", 409)
        spawn(f"init:{name}", ["init", idea, name], self.projects / ".ui" / f"init-{name}.log", {"PRESET": preset})
        return {"ok": True, "name": name}

    def settings(self, name: str) -> dict:
        ws = self.ws(name)
        conf = sa.read_json(ws / "agent" / "config.json", {})
        eff = sa.Agent(ws).cfg
        inst = self.installed_models(eff["llm_api"])
        return {"settings": {"models": eff["models"], "coder_model": eff["coder_model"],
                             "limits": {k: eff["limits"][k] for k in SETTING_LIMITS}, "web_mode": eff["web"]["mode"], "scheduler": eff["scheduler"], "run": eff["run"],
                             "doc_language": eff["doc_language"], "preset": conf.get("preset", eff["preset"]),
                             "routing": {k: eff["routing"].get(k, "") for k in ("mode", "light", "heavy", "final_reviewer")}},
                "limit_bounds": SETTING_LIMITS, "installed_models": inst,
                "resolved": models.resolve(set(inst), eff["routing"], eff["coder_model"]),
                "catalog": models.catalog_view(set(inst))}

    @staticmethod
    def installed_models(api: str) -> list[str]:
        u = urllib.parse.urlparse(api)
        try:
            with urllib.request.urlopen(f"{u.scheme}://{u.netloc}/api/tags", timeout=3) as r:
                return sorted(m["name"] for m in json.loads(r.read()).get("models", []))
        except Exception:  # noqa: BLE001
            return []

    def save_settings(self, ws: Path, body: dict) -> None:
        conf = sa.read_json(ws / "agent" / "config.json", {})
        models = body.get("models", {})
        for role in ("planner", "reviewer", "summarizer"):
            if role in models:
                conf.setdefault("models", {})[role] = self.model_name(models[role])
        if "coder_model" in body:
            conf["coder_model"] = self.model_name(body["coder_model"])
        for k, v in (body.get("limits") or {}).items():
            if k not in SETTING_LIMITS:
                raise UiError(f"Unknown limit: {k}")
            lo, hi = SETTING_LIMITS[k]
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise UiError(f"{k} must be a number")
            if not lo <= v <= hi:
                raise UiError(f"{k} must be between {lo} and {hi}")
            conf.setdefault("limits", {})[k] = v
        rt = body.get("routing")
        if rt:
            if rt.get("mode", "auto") not in ("auto", "off"):
                raise UiError("Routing mode must be auto or off")
            conf["routing"] = {**conf.get("routing", {}), "mode": rt.get("mode", "auto")}
            for k in ("light", "heavy", "final_reviewer"):
                if k in rt:
                    conf["routing"][k] = self.model_name(rt[k]) if str(rt[k]).strip() else ""
        rn = body.get("run")
        if rn:
            if rn.get("mode", "default") not in ("default", "rounds", "endless"):
                raise UiError("Run mode must be default, rounds or endless")
            try:
                rounds = int(rn.get("rounds", 3))
            except (TypeError, ValueError):
                raise UiError("Rounds must be a whole number") from None
            if not 1 <= rounds <= 1000:
                raise UiError("Rounds must be between 1 and 1000")
            conf["run"] = {"mode": rn.get("mode", "default"), "rounds": rounds}
        sc = body.get("scheduler")
        if sc:
            try:
                slots = int(sc.get("slots", 2))
            except (TypeError, ValueError):
                raise UiError("Parallel calls must be a whole number") from None
            if not 1 <= slots <= 4:
                raise UiError("Parallel calls must be between 1 and 4")
            if sc.get("priority", "main") not in ("main", "side"):
                raise UiError("Priority must be main or side")
            try:
                idle = float(sc.get("min_idle", 20))
            except (TypeError, ValueError):
                raise UiError("min_idle must be a number") from None
            if not 0 <= idle <= 3600:
                raise UiError("min_idle must be between 0 and 3600")
            conf["scheduler"] = {"priority": sc.get("priority", "main"), "min_idle": idle, "slots": slots}
        if "web_mode" in body:
            if body["web_mode"] not in ("off", "sources", "open"):
                raise UiError("Web mode must be off, sources or open")
            conf.setdefault("web", {})["mode"] = body["web_mode"]
        if "doc_language" in body:
            lang = str(body["doc_language"]).strip()
            if not re.fullmatch(r"[A-Za-z ]{2,30}", lang):
                raise UiError("Invalid documentation language")
            conf["doc_language"] = lang
        sa.write_json(ws / "agent" / "config.json", conf)

    @staticmethod
    def model_name(v) -> str:
        v = str(v).strip()
        if not MODEL_RE.match(v):
            raise UiError(f"Invalid model name: {v!r}")
        return v

    def rename(self, ws: Path, new: str) -> dict:
        new = str(new or "").strip()
        if not NAME_RE.match(new):
            raise UiError("Name: lowercase letters, digits and dashes (max 61 characters)")
        if (self.projects / new).exists():
            raise UiError("A project with this name already exists", 409)
        ws.rename(self.projects / new)
        return {"ok": True, "name": new}

    def delete(self, ws: Path, body: dict) -> dict:
        if body.get("confirm") != ws.name:
            raise UiError("Type the project name to confirm")
        shutil.rmtree(ws)
        return {"ok": True}

    def backlog_edit(self, ws: Path, action: str, body: dict) -> None:
        bl = sa.Backlog(ws / "agent" / "backlog.json")
        item = next((i for i in bl.items if i["id"] == body.get("id")), None)
        if not item:
            raise UiError("Task not found", 404)
        if action == "task-cancel":
            if item["status"] not in ("pending", "blocked"):
                raise UiError("Only open tasks can be cancelled")
            bl.set(item, status="cancelled", notes=item.get("notes", []) + ["cancelled by you in the UI"])
        elif action == "task-reopen":
            if item["status"] not in ("blocked", "cancelled"):
                raise UiError("Only blocked or cancelled tasks can be reopened")
            bl.set(item, status="pending", attempts=0)
        else:  # task-top
            if item["status"] != "pending":
                raise UiError("Only open tasks can be moved up")
            bl.set(item, priority=max(i["priority"] for i in bl.items) + 1)

    # ---- Model manager (install/remove through the Ollama API)
    @staticmethod
    def ollama_base() -> str:
        u = urllib.parse.urlparse(sa.DEFAULT_CONFIG["llm_api"])
        return f"{u.scheme}://{u.netloc}"

    def models_overview(self) -> dict:
        try:
            with urllib.request.urlopen(self.ollama_base() + "/api/tags", timeout=3) as r:
                tags = json.loads(r.read()).get("models", [])
            ok = True
        except Exception:  # noqa: BLE001
            tags, ok = [], False
        inst = {m["name"] for m in tags}
        return {"ok": ok, "url": self.ollama_base(),
                "installed": sorted(({"name": m["name"], "gb": round(m.get("size", 0) / 1e9, 1), "used_by": self.model_users(m["name"])}
                                     for m in tags), key=lambda m: m["name"]),
                "catalog": models.catalog_view(inst), "pulls": {k: dict(v) for k, v in PULLS.items()}}

    def model_users(self, name: str) -> list[dict]:
        """Projects whose settings use this model (running ones block removal)."""
        out = []
        for d in sorted(self.projects.glob("*/agent/GOAL.md")):
            ws = d.parent.parent
            try:
                eff = sa.Agent(ws).cfg
            except Exception:  # noqa: BLE001
                continue
            used = set(eff["models"].values()) | {str(eff["coder_model"]).split("/", 1)[-1]} \
                | {str(eff["routing"].get(k, "")).split("/", 1)[-1] for k in ("light", "heavy", "final_reviewer")}
            if name in used:
                out.append({"project": ws.name, "running": self.summary(ws)["running"]})
        return out

    def model_pull(self, body: dict) -> dict:
        name = self.model_name(body.get("name"))
        if PULLS.get(name, {}).get("active"):
            raise UiError("Already downloading", 409)
        PULLS[name] = {"status": "starting", "completed": 0, "total": 0, "active": True, "error": ""}
        PULL_CANCEL.discard(name)
        threading.Thread(target=self.pull_worker, args=(name,), daemon=True).start()
        return {"ok": True}

    def pull_worker(self, name: str) -> None:
        st = PULLS[name]
        req = urllib.request.Request(self.ollama_base() + "/api/pull", data=json.dumps({"model": name, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                for raw in r:
                    if name in PULL_CANCEL:
                        st["error"] = "cancelled"
                        break
                    j = json.loads(raw)
                    if j.get("error"):
                        st["error"] = str(j["error"])[:300]
                        break
                    st["status"] = j.get("status", st["status"])
                    if j.get("total"):
                        st["total"], st["completed"] = j["total"], j.get("completed", 0)
                    if j.get("status") == "success":
                        st["status"] = "done"
        except Exception as e:  # noqa: BLE001
            st["error"] = f"Model server not reachable or download interrupted: {e}"[:300]
        st["active"] = False
        self._ms = None

    def model_cancel(self, body: dict) -> dict:
        PULL_CANCEL.add(self.model_name(body.get("name")))
        return {"ok": True}

    def model_delete(self, body: dict) -> dict:
        name = self.model_name(body.get("name"))
        busy = [u["project"] for u in self.model_users(name) if u["running"]]
        if busy:
            raise UiError(f"In use by running project(s): {', '.join(busy)}. Stop them first.", 409)
        req = urllib.request.Request(self.ollama_base() + "/api/delete", data=json.dumps({"model": name}).encode(), method="DELETE",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30).close()
        except urllib.error.HTTPError as e:
            raise UiError("Model not found" if e.code == 404 else f"Ollama refused: HTTP {e.code}", 404 if e.code == 404 else 502)
        except OSError as e:
            raise UiError(f"Model server not reachable: {e}", 502)
        PULLS.pop(name, None)
        self._ms = None
        return {"ok": True}

    def chat_ws(self, name: str) -> Path | None:
        try:
            return self.ws(name)
        except UiError:
            return None

    def chat_default_model(self, projects: list[str]) -> str:
        for n in projects:
            ws = self.chat_ws(n)
            if ws:
                return sa.Agent(ws).cfg["models"]["planner"]
        return sa.DEFAULT_CONFIG["models"]["planner"]

    def chat_list(self) -> dict:
        out = self.chats.list()
        out["installed_models"] = self.installed_models(sa.DEFAULT_CONFIG["llm_api"])
        out["default_model"] = self.chat_default_model([])
        return out

    def chat_create(self, body: dict) -> dict:
        names = self.chats.clean_links(body.get("projects"))
        return self.chats.create(body, self.chat_default_model(names))

    def chat_stream(self, cid: str, text: str, write) -> None:
        """Appends the user message, streams the answer through write(dict) and stores it (also when cut off)."""
        text = text.strip()
        if not text:
            raise UiError("Message is empty")
        if len(text) > chatlib.MAX_USER_CHARS:
            raise UiError("Message too long")
        chat = self.chats.load(cid)
        chat["messages"].append({"role": "user", "content": text, "t": time.time()})
        if chat["title"] == "New chat":
            chat["title"] = text.replace("\n", " ")[:50]
        chat["messages"] = chat["messages"][-chatlib.MAX_MESSAGES:]
        chat["updated"] = time.time()
        self.chats.save(chat)
        cfg = sa.DEFAULT_CONFIG
        msgs = chatlib.build_messages(chat, self.chat_ws, cfg["llm_api"], cfg["api_key"])
        answer, err = "", ""
        try:
            for d in chatlib.stream_completion(cfg["llm_api"], cfg["api_key"], chat["model"], msgs):
                answer += d
                write({"delta": d})
        except OSError as e:  # includes a closed browser connection: keep the partial answer
            err = f"Model server not reachable or connection closed: {e}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        finally:
            if answer:
                chat["messages"].append({"role": "assistant", "content": answer, "t": time.time(), "model": chat["model"]})
                chat["updated"] = time.time()
                self.chats.save(chat)
        if err:
            try:
                write({"error": err})
            except OSError:
                pass

    def languages(self) -> dict:
        return {"stacks": sa.stacks.availability()}

    def get_master(self) -> dict:
        return {"text": self.master.read_text("utf-8") if self.master.exists() else sa.MASTER_TEMPLATE}

    def set_master(self, body: dict) -> dict:
        sa.write_text(self.master, str(body.get("text", "")))
        return {"ok": True}


def restart_self() -> None:
    """Re-executes this server with the same arguments so a pulled update takes effect (the listening socket is not inherited)."""
    os.execv(sys.executable, [sys.executable, *sys.argv])


def make_handler(svc: Service, port: int, token: str | None = None):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    class H(BaseHTTPRequestHandler):
        server_version = "CodeFactoryUI"

        def log_message(self, *a):
            pass

        def send(self, code: int, payload, ctype: str = "application/json") -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authed(self) -> bool:
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            got = cookie["cf_token"].value if "cf_token" in cookie else ""
            return hmac.compare_digest(got.encode(), token.encode())

        def guard(self, post: bool) -> bool:
            host = self.headers.get("Host", "")
            if token:  # LAN mode: every request needs the access token (cookie), any Host name is fine
                if not self.authed():
                    self.send(401, {"error": "Not authorised: open the link with ?token=... printed by `make ui-lan`"})
                    return False
                allowed = {host}
            else:  # Protects against DNS rebinding and foreign websites posting to localhost
                if host not in hosts:
                    self.send(403, {"error": "Host not allowed"})
                    return False
                allowed = hosts
            if post:
                origin = self.headers.get("Origin")
                if (origin and origin.split("://", 1)[-1] not in allowed) or self.headers.get("X-UI") != "1":
                    self.send(403, {"error": "Request rejected"})
                    return False
            return True

        def handle_any(self, method: str) -> None:
            if not self.guard(method == "POST"):
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            try:
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        raise UiError("Request too large", 413)
                    body = json.loads(self.rfile.read(n) or b"{}")
                ms = re.fullmatch(r"/api/chat/([a-f0-9]{12})/send", path)
                if method == "POST" and ms:
                    return self.chat_send(ms.group(1), body)  # streams for minutes, so it must not hold LOCK
                if path.startswith("/api/update"):  # network and pip work: must not hold LOCK
                    return self.send(200, self.update_route(method, path))
                if path.startswith(("/api/chats", "/api/chat/")):
                    svc.require_chat()
                with LOCK:
                    self.send(200, self.route(method, path, body, q))
            except chatlib.ChatError as e:
                self.send(e.code, {"error": str(e)})
            except UiError as e:
                self.send(e.code, {"error": str(e)})
            except (ValueError, KeyError) as e:
                self.send(400, {"error": f"Invalid request: {e}"})
            except Exception as e:  # noqa: BLE001
                self.send(500, {"error": f"{type(e).__name__}: {e}"})

        def update_route(self, method: str, path: str):
            with UPDATE_LOCK:
                if method == "GET":
                    return svc.update_status(False)
                if path == "/api/update/check":
                    return svc.update_status(True)
                if path == "/api/update/apply":
                    return svc.update_apply()
            raise UiError("Not found", 404)

        def chat_send(self, cid: str, body: dict) -> None:
            svc.chats.load(cid)  # 404 before the stream starts
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def write(obj) -> None:
                self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
            try:
                svc.chat_stream(cid, str(body.get("content", "")), write)
                write("[DONE]")
            except (UiError, chatlib.ChatError) as e:
                write({"error": str(e)})
            except OSError:
                pass

        def route(self, method: str, path: str, body: dict, q: dict):
            if method == "GET":
                if path == "/":
                    return INDEX.read_bytes()
                if path == "/api/projects":
                    return svc.list()
                if path == "/api/master":
                    return svc.get_master()
                if path == "/api/dashboard":
                    return svc.dashboard()
                if path == "/api/diagnostics":
                    return svc.diagnostics()
                if path == "/api/languages":
                    return svc.languages()
                if path == "/api/ui-settings":
                    return {"chat_enabled": svc.chat_enabled()}
                if path == "/api/lan":  # only in LAN mode: the phone link as a QR code (the request itself already needed the token)
                    if not token:
                        return {"lan": False}
                    host = self.headers.get("Host", "")  # the address you reached the UI by, if it is not a loopback one
                    host = host if host and not re.match(r"^(127\.|localhost|\[::1\])", host) else f"{lan_ip()}:{port}"
                    url = f"http://{host}/?token={token}"
                    return {"lan": True, "url": url, "svg": qr.svg(url)}
                if path == "/api/tool":
                    return svc.tool()
                if path == "/api/build":
                    return {"sha": BUILD["sha"], "head": head_sha(), "started": BUILD["started"]}
                if path == "/api/tool/run":
                    return svc.tool_run(q.get("id", ""), q.get("branch", ""))
                if path == "/api/chats":
                    return svc.chat_list()
                if path == "/api/models":
                    return svc.models_overview()
                m = re.fullmatch(r"/api/chat/([a-f0-9]{12})", path)
                if m:
                    return svc.chats.load(m.group(1))
                if path == "/api/tool/stream":
                    return svc.tool_stream()
                if path == "/api/tool/activity":
                    return svc.tool_activity()
                if path == "/api/tool/tree":
                    return svc.tool_tree(q.get("branch", ""))
                if path == "/api/tool/file":
                    return svc.tool_file(q.get("branch", ""), q.get("path", ""))
                if path == "/api/tool/diff":
                    return svc.tool_diff(q.get("branch", ""))
                m = re.fullmatch(r"/api/project/([^/]+)", path)
                if m:
                    return svc.detail(m.group(1))
                m = re.fullmatch(r"/api/project/([^/]+)/profile", path)
                if m:
                    return svc.profile(m.group(1))
                m = re.fullmatch(r"/api/project/([^/]+)/context", path)
                if m:
                    return {"name": q.get("name", ""), "text": svc.guard_profile(lambda: cfprofile.read_context(svc.ws(m.group(1)) / "agent", q.get("name", "")))}
                m = re.fullmatch(r"/api/project/([^/]+)/settings", path)
                if m:
                    return svc.settings(m.group(1))
                m = re.fullmatch(r"/api/project/([^/]+)/(llm|changes|diff|thoughts|tree|file|stream)", path)
                if m:
                    if m.group(2) == "stream":
                        return svc.stream(m.group(1))
                    if m.group(2) == "thoughts":
                        return svc.thoughts(m.group(1))
                    if m.group(2) == "tree":
                        return svc.tree(m.group(1), q.get("base", "head"))
                    if m.group(2) == "file":
                        return svc.file(m.group(1), q.get("path", ""), q.get("base", "head"))
                    if m.group(2) == "llm":
                        return svc.llm_log(m.group(1))
                    if m.group(2) == "changes":
                        return svc.changes(m.group(1))
                    return svc.diff(m.group(1), q.get("sha", ""), q.get("path", ""))
            else:
                if path == "/api/projects":
                    return svc.create(body)
                if path == "/api/master":
                    return svc.set_master(body)
                if path == "/api/tool/task":
                    return svc.tool_task(body)
                if path == "/api/tool/preview":
                    return svc.preview_start(body)
                if path == "/api/tool/preview/tests":
                    return svc.preview_tests()
                if path == "/api/tool/merge":
                    return svc.tool_merge(body)
                if path == "/api/tool/undo-merge":
                    return svc.tool_undo_merge()
                if path == "/api/tool/cancel":
                    return svc.tool_cancel()
                if path == "/api/tool/discard":
                    return svc.tool_discard(body)
                if path == "/api/tool/update-branch":
                    return svc.tool_update_branch(body)
                if path == "/api/tool/preview/stop":
                    return svc.preview_stop()
                if path == "/api/ui-settings":
                    return svc.set_ui_settings(body)
                if path == "/api/chats":
                    return svc.chat_create(body)
                if path in ("/api/models/pull", "/api/models/delete", "/api/models/cancel"):
                    return getattr(svc, "model_" + path.rsplit("/", 1)[1])(body)
                m = re.fullmatch(r"/api/chat/([a-f0-9]{12})/(update|delete)", path)
                if m:
                    if m.group(2) == "delete":
                        svc.chats.delete(m.group(1))
                        return {"ok": True}
                    return svc.chats.update(m.group(1), body)
                m = re.fullmatch(r"/api/project/([^/]+)/([a-z-]+)", path)
                if m:
                    return svc.act(m.group(1), m.group(2), body)
            raise UiError("Not found", 404)

        def login(self) -> bool:
            """LAN mode: /?token=... sets the cookie and redirects to the clean URL."""
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            given = (q.get("token") or [""])[0]
            if not token or not given or not hmac.compare_digest(given.encode(), token.encode()):
                return False
            self.send_response(302)
            self.send_header("Set-Cookie", f"cf_token={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
            self.send_header("Location", "/")
            self.end_headers()
            return True

        def do_GET(self):
            if token and self.path.split("?", 1)[0] == "/" and self.login():
                return
            if self.path.split("?", 1)[0] == "/":
                if not self.guard(False):
                    return
                self.send(200, INDEX.read_bytes(), "text/html")
                return
            self.handle_any("GET")

        def do_POST(self):
            self.handle_any("POST")

    return H


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as c:
            c.connect(("10.255.255.255", 1))  # no packet is sent, it only selects the outgoing interface
            return c.getsockname()[0]
    except OSError:
        return "<this-machine-ip>"


def load_token(projects: Path) -> str:
    f = projects / ".ui" / "token"
    if f.exists() and f.read_text("utf-8").strip():
        return f.read_text("utf-8").strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    return tok


def serve(projects: Path, port: int, open_browser: bool = True, lan: bool = False) -> None:
    BUILD["sha"], BUILD["started"] = head_sha(), time.time()
    svc = Service(projects)
    token = load_token(projects.resolve()) if lan else None
    srv = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), make_handler(svc, port, token))
    url = f"http://127.0.0.1:{port}"
    if lan:
        print(f"Web UI reachable in your network (plain HTTP, trusted networks only).\n"
              f"  This machine: {url}/?token={token}\n  Other devices: http://{lan_ip()}:{port}/?token={token}\n"
              f"  (the token is stored in {projects}/.ui/token; stop with Ctrl+C; running agents keep going)")
        open_browser = False
    else:
        print(f"Web UI running at {url}  (stop with Ctrl+C; running agents keep going)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nUI stopped.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--projects-dir", default="projects")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--lan", action="store_true", help="listen on all interfaces (other devices in your network), protected by an access token")
    a = ap.parse_args()
    serve(Path(a.projects_dir), a.port, not a.no_browser, a.lan)


if __name__ == "__main__":
    sys.exit(main())
