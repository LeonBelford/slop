#!/usr/bin/env python3
"""Local web UI for the SaaS agent (standard library only, 127.0.0.1 only).

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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import saas_agent as sa

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "web" / "index.html"
MAX_BODY = 1_000_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")

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
                "log": tail(a / "agent.log"), "run_out": tail(a / "run.out", 15), "try_out": tail(a / "try.out", 15),
                "features": [{"id": k, "title": v.get("title", k)} if isinstance(v, dict) else {"id": k, "title": str(v)}
                             for k, v in feats.items()],
                "backlog_items": [{k: i.get(k) for k in ("id", "title", "type", "priority", "status", "source", "attempts")}
                                  for i in backlog]}

    def act(self, name: str, action: str, body: dict) -> dict:
        ws = self.ws(name)
        a = ws / "agent"
        if action == "start":
            if self.summary(ws)["running"]:
                raise UiError("Agent is already running", 409)
            spawn(f"run:{name}", ["run", name], a / "run.out")
        elif action == "stop":
            sa.write_text(a / "STOP", "ui\n")
        elif action == "try":
            if alive(f"try:{name}"):
                raise UiError("A test version is already being built", 409)
            (a / "try.out").write_text("", "utf-8")
            spawn(f"try:{name}", ["try", name], a / "try.out")
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
        server_version = "SaasAgentUI"

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
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            try:
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        raise UiError("Request too large", 413)
                    body = json.loads(self.rfile.read(n) or b"{}")
                with LOCK:
                    self.send(200, self.route(method, path, body))
            except UiError as e:
                self.send(e.code, {"error": str(e)})
            except (ValueError, KeyError) as e:
                self.send(400, {"error": f"Invalid request: {e}"})
            except Exception as e:  # noqa: BLE001
                self.send(500, {"error": f"{type(e).__name__}: {e}"})

        def route(self, method: str, path: str, body: dict):
            if method == "GET":
                if path == "/":
                    return INDEX.read_bytes()
                if path == "/api/projects":
                    return svc.list()
                if path == "/api/master":
                    return svc.get_master()
                if path == "/api/languages":
                    return svc.languages()
                m = re.fullmatch(r"/api/project/([^/]+)", path)
                if m:
                    return svc.detail(m.group(1))
            else:
                if path == "/api/projects":
                    return svc.create(body)
                if path == "/api/master":
                    return svc.set_master(body)
                m = re.fullmatch(r"/api/project/([^/]+)/([a-z]+)", path)
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
