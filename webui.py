#!/usr/bin/env python3
"""Local web UI for CodeFactory (standard library only, 127.0.0.1 only).

Start: `make ui` or `python3 webui.py [--port 8765] [--projects-dir projects]`.
The CLI stays fully usable; the UI calls the same commands (scripts/run.sh) and reads/writes the same files.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import codefactory as sa

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "web" / "index.html"
MAX_BODY = 1_000_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")
BRANCH_RE = re.compile(r"^self-improve/[0-9]{8}-[0-9]{6}$")
MAX_DIFF = 200_000

PROCS: dict[str, subprocess.Popen] = {}  # started agents, init and try jobs: "<kind>:<project>"
LOCK = threading.Lock()


class UiError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


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
        self.master = ROOT / "MASTER.md"

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
                "failures": state.get("consecutive_failures", 0), "last_log": log[-220:]}

    def list(self) -> dict:
        projects = [self.summary(p) for p in sa.list_projects(self.projects)]
        jobs = []
        for key, p in list(PROCS.items()):
            if key.startswith("init:") and not (self.projects / key[5:] / "agent" / "GOAL.md").exists():
                jobs.append({"name": key[5:], "running": p.poll() is None, "code": p.poll(),
                             "log": tail(self.projects / ".ui" / f"init-{key[5:]}.log", 8)})
        return {"projects": projects, "init_jobs": jobs, "master": bool(sa.read_master(self.master))}

    def detail(self, name: str) -> dict:
        ws = self.ws(name)
        a = ws / "agent"
        feats = sa.read_json(a / "memory" / "features.json", {})
        backlog = sorted(sa.read_json(a / "backlog.json", []), key=lambda i: -i["priority"])
        read = lambda p: p.read_text("utf-8") if p.exists() else ""
        return {**self.summary(ws), "ideas": read(ws / "IDEAS.md"), "goal": read(a / "GOAL.md"), "testing": read(ws / "TESTING.md"), "sources": read(ws / "SOURCES.md"), "web_mode": sa.read_json(a / "config.json", {}).get("web", {}).get("mode", "sources"),
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
                "llm": read_live(a / "llm_now.json", running), "coder": read_live(a / "coder_now.json", running),
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
            branches.append({"name": name, "date": date, "subject": subject, "stat": stat[-12:]})
        return {"running": alive("tool"), "log": tail(ROOT / "self_improve" / "ui.log", 60), "branches": branches}

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
            spawn(f"do:{name}", ["do", name, task], a / "do.out")
            return {"ok": True, "queued": False}
        elif action == "say":
            msg = str(body.get("msg", "")).strip()
            if not msg:
                raise UiError("Message is empty")
            sa.Agent(ws).add_feedback(msg)
        elif action == "ideas":
            sa.write_text(ws / "IDEAS.md", str(body.get("text", "")))
        elif action == "sources":
            sa.write_text(ws / "SOURCES.md", str(body.get("text", "")))
        else:
            raise UiError("Unknown action", 404)
        return {"ok": True}

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

    def languages(self) -> dict:
        return {"stacks": sa.stacks.availability()}

    def get_master(self) -> dict:
        return {"text": self.master.read_text("utf-8") if self.master.exists() else sa.MASTER_TEMPLATE}

    def set_master(self, body: dict) -> dict:
        sa.write_text(self.master, str(body.get("text", "")))
        return {"ok": True}


def make_handler(svc: Service, port: int):
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

        def guard(self, post: bool) -> bool:
            # Protects against DNS rebinding and foreign websites posting to localhost
            if self.headers.get("Host", "") not in hosts:
                self.send(403, {"error": "Host not allowed"})
                return False
            if post:
                origin = self.headers.get("Origin")
                if (origin and origin.split("://", 1)[-1] not in hosts) or self.headers.get("X-UI") != "1":
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
                with LOCK:
                    self.send(200, self.route(method, path, body, q))
            except UiError as e:
                self.send(e.code, {"error": str(e)})
            except (ValueError, KeyError) as e:
                self.send(400, {"error": f"Invalid request: {e}"})
            except Exception as e:  # noqa: BLE001
                self.send(500, {"error": f"{type(e).__name__}: {e}"})

        def route(self, method: str, path: str, body: dict, q: dict):
            if method == "GET":
                if path == "/":
                    return INDEX.read_bytes()
                if path == "/api/projects":
                    return svc.list()
                if path == "/api/master":
                    return svc.get_master()
                if path == "/api/languages":
                    return svc.languages()
                if path == "/api/tool":
                    return svc.tool()
                if path == "/api/tool/diff":
                    return svc.tool_diff(q.get("branch", ""))
                m = re.fullmatch(r"/api/project/([^/]+)", path)
                if m:
                    return svc.detail(m.group(1))
                m = re.fullmatch(r"/api/project/([^/]+)/(llm|changes|diff)", path)
                if m:
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
                m = re.fullmatch(r"/api/project/([^/]+)/([a-z-]+)", path)
                if m:
                    return svc.act(m.group(1), m.group(2), body)
            raise UiError("Not found", 404)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/":
                if not self.guard(False):
                    return
                self.send(200, INDEX.read_bytes(), "text/html")
                return
            self.handle_any("GET")

        def do_POST(self):
            self.handle_any("POST")

    return H


def serve(projects: Path, port: int, open_browser: bool = True) -> None:
    svc = Service(projects)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(svc, port))
    url = f"http://127.0.0.1:{port}"
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
    a = ap.parse_args()
    serve(Path(a.projects_dir), a.port, not a.no_browser)


if __name__ == "__main__":
    sys.exit(main())
