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
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dash import chat as chatlib
import codefactory as sa
from core import cfprofile
from infra import qr
from infra import update

from webapi.chat import ChatMixin
from webapi.common import BUILD, INDEX, LOCK, MAX_BODY, NAME_RE, PROCS, UPDATE_LOCK, UiError, alive, head_sha, pid_alive, spawn, tail
from webapi.dashboard import DashboardMixin
from webapi.models import ModelsMixin
from webapi.project import ProjectMixin
from webapi.tool import ToolMixin

ROOT = Path(__file__).resolve().parent


class Service(ProjectMixin, DashboardMixin, ToolMixin, ModelsMixin, ChatMixin):
    """The web UI backend. Each concern lives in its own mixin under webapi/; see webapi/common.py for shared helpers."""

    def __init__(self, projects: Path):
        self._dash_lock = threading.Lock()
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
        elif action == "replan":
            if self.summary(ws)["running"]:
                raise UiError("Stop the agent first: the re-evaluation rewrites the task list.", 409)
            if alive(f"replan:{name}"):
                raise UiError("A re-evaluation is already running", 409)
            spawn(f"replan:{name}", ["replan", name], a / "replan.out")
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
                if path == "/api/history":
                    return svc.history()
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
                if path == "/api/tool/models":
                    return svc.tool_models()
                mc = re.fullmatch(r"/api/project/([^/]+)/chat", path)
                if mc:
                    svc.require_chat()
                    return svc.chat_project(mc.group(1))
                if path == "/api/tool/chat":
                    svc.require_chat()
                    return svc.chat_tool()
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
                if path == "/api/tool/models":
                    return svc.tool_models_save(body)
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
            path = self.path.split("?", 1)[0]
            if path == "/api/dashboard/stream":
                return self.dashboard_stream()
            if path.startswith("/static/"):
                return self.static(path[8:])
            self.handle_any("GET")

        def static(self, name: str) -> None:
            """The UI's own css/js files (whitelisted names inside web/, nothing else)."""
            kind = {".css": "text/css", ".js": "text/javascript"}.get(Path(name).suffix)
            f = INDEX.parent / name
            if not self.guard(False):
                return
            if not kind or not re.fullmatch(r"[a-z0-9_-]+\.(?:css|js)", name) or not f.is_file():
                return self.send(404, {"error": "Not found"})
            self.send(200, f.read_bytes(), kind)

        def dashboard_stream(self) -> None:
            """Server-sent events: the dashboard snapshot, sent only when something changed (a ping keeps idle connections open)."""
            if not self.guard(False):
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            last, quiet = "", 0
            try:
                while True:
                    snap = svc.dashboard()
                    key = json.dumps({**snap, "time": 0}, sort_keys=True)
                    if key != last or quiet >= 15:
                        self.wfile.write(b"data: " + json.dumps(snap, ensure_ascii=False).encode() + b"\n\n" if key != last else b": ping\n\n")
                        self.wfile.flush()
                        last, quiet = key, 0
                    time.sleep(1.0)
                    quiet += 1
            except OSError:
                return

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
