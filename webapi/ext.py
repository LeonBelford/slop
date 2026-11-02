"""Service mixin for the control, code, test, sandbox, refactor and insight features (project tabs "Control" and "Code").

Reads come in as GET /api/project/<name>/x/<what>, actions as POST /api/project/<name>/<action> (see `ext_act`), global ones
(context tuner) as /api/ext/<what>. Long runs (mutation check, sandbox command) are jobs polled via x/job.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import codefactory as sa
from core import control
from dash import adrs, chatagent, codesearch, events, refactor, review, roles, sandbox, testgen
from infra import ctxtune
from webapi import common
from webapi.common import UiError

JOBS: dict[tuple[str, str], dict] = {}
JOB_LOCK = threading.Lock()
ACTIONS = ("control", "spec-decide", "role-save", "role-reset", "task-prio", "review-comment", "testgen", "mutation", "sandbox-run",
           "play-start", "play-stop", "refactor-rename", "refactor-dead", "refactor-split")


def _job(name: str, kind: str, fn) -> None:
    """Runs fn() in a thread; only one job of a kind per project at a time. The result is polled through x/job."""
    with JOB_LOCK:
        if JOBS.get((name, kind), {}).get("state") == "running":
            raise UiError("This is already running", 409)
        JOBS[(name, kind)] = {"state": "running", "since": time.time()}

    def go():
        try:
            res = {"state": "done", "result": fn()}
        except (ValueError, OSError) as e:
            res = {"state": "error", "error": str(e)}
        except Exception as e:  # noqa: BLE001  (a job must never kill the server)
            res = {"state": "error", "error": f"{type(e).__name__}: {e}"}
        with JOB_LOCK:
            JOBS[(name, kind)] = {**res, "finished": time.time()}
    threading.Thread(target=go, daemon=True).start()


class ExtMixin:
    def _running(self, ws: Path, name: str) -> bool:
        return bool(self.summary(ws)["running"]) or any(common.alive(f"{k}:{name}") for k in ("run", "do", "try"))

    def _enqueue(self, ws: Path, task: dict, source: str) -> dict:
        bl = sa.Backlog(ws / "agent" / "backlog.json")
        return bl.add(task["title"], task["description"], task["type"], task["priority"], task["acceptance"], f"Requested in the UI ({source})", source, force=True)

    def ext_get(self, name: str, what: str, q: dict):
        ws = self.ws(name)
        a, repo = ws / "agent", ws / "repo"
        try:
            if what == "control":
                p, d = control.spec_paths(a)
                return {**control.load(a), "running": self._running(ws, name), "spec": sa.read_json(p, None)}
            if what == "roles":
                eff = sa.Agent(ws).cfg["models"]
                return {"roles": sorted(eff), "defaults": eff, "custom": roles.load(a / "roles.json")}
            if what == "search":
                return {"hits": codesearch.search(repo, q.get("q", ""))}
            if what == "uses":
                return {"uses": codesearch.uses(repo, q.get("symbol", "")), "graph": codesearch.call_graph(repo, q.get("symbol", ""))}
            if what == "untested":
                return {"files": testgen.untested(repo)}
            if what == "dead":
                return {"dead": refactor.dead_code(repo), "big": refactor.big_modules(repo)}
            if what == "health":
                return adrs.health(a, repo)
            if what == "adrs":
                return {"adrs": adrs.decisions(a, q.get("q", ""))}
            if what == "timeline":
                return {"events": events.timeline(a)}
            if what == "play":
                return sandbox.play_status(ws)
            if what == "job":
                return JOBS.get((name, q.get("kind", "")), {"state": "none"})
        except ValueError as e:
            raise UiError(str(e)) from None
        raise UiError("Not found", 404)

    def ext_act(self, ws: Path, name: str, action: str, body: dict) -> dict:
        a, repo = ws / "agent", ws / "repo"
        def test_cmd() -> str:
            return sa.Agent(ws).cfg.get("commands", {}).get("test") or ""
        try:
            if action == "control":
                keys = {k: body[k] for k in ("paused", "step_mode", "spec_review", "next", "hint", "clear_hints") if k in body}
                if keys.get("paused") and not self._running(ws, name):
                    raise UiError("The agent is not running: nothing to pause", 409)
                c = control.update(a, **keys)
                events.add(a, "control", ", ".join(f"{k}={str(v)[:60]}" for k, v in keys.items()))
                return {"ok": True, "control": c}
            if action == "spec-decide":
                control.decide_spec(a, bool(body.get("approve")), body.get("spec"))
                events.add(a, "control", "design approved" if body.get("approve") else "design rejected")
                return {"ok": True}
            if action == "role-save":
                return {"ok": True, "custom": roles.save(a / "roles.json", sorted(sa.Agent(ws).cfg["models"]), str(body.get("role", "")), body)}
            if action == "role-reset":
                return {"ok": True, "custom": roles.reset(a / "roles.json", body.get("role") or None)}
            if action == "task-prio":
                bl = sa.Backlog(a / "backlog.json")
                item = next((i for i in bl.items if i["id"] == body.get("id")), None)
                if not item or item["status"] != "pending":
                    raise UiError("Only open tasks can change priority", 404 if not item else 400)
                bl.set(item, priority=float(body.get("priority")))
                events.add(a, "control", f"{item['id']} priority set to {body.get('priority')}")
                return {"ok": True}
            if action == "review-comment":
                bl = sa.Backlog(a / "backlog.json")
                item = next((i for i in bl.items if i["id"] == body.get("task")), {"id": str(body.get("task", "")), "title": ""})
                t = review.comment_task(item["id"], item["title"], str(body.get("path", ""))[:200], int(body.get("line") or 0), str(body.get("code", "")), str(body.get("comment", "")))
                new = self._enqueue(ws, t, "review")
                events.add(a, "review", f"comment on {item['id']} became {new['id']}")
                return {"ok": True, "task": new["id"]}
            if action == "testgen":
                f = str(body.get("file", ""))
                if f not in {x["file"] for x in testgen.untested(repo, 10_000)}:
                    raise UiError("That file already has tests or does not exist")
                return {"ok": True, "task": self._enqueue(ws, testgen.task_for(f), "testgen")["id"]}
            if action == "mutation":
                f, tc = str(body.get("file", "")), test_cmd()
                _job(name, "mutation", lambda: testgen.mutation_check(repo, f, tc))
                return {"ok": True, "started": True}
            if action == "sandbox-run":
                cmd = str(body.get("cmd", ""))
                _job(name, "terminal", lambda: sandbox.run(ws, cmd, int(body.get("timeout") or 60)))
                return {"ok": True, "started": True}
            if action == "play-start":
                cmd = str(body.get("cmd") or "").strip() or sa.Agent(ws).detect_run_plan().get("run", "")
                return sandbox.play_start(ws, cmd)
            if action == "play-stop":
                sandbox.play_stop(ws)
                return {"ok": True}
            if action.startswith("refactor-"):
                return self._refactor(ws, name, action, body, test_cmd)
        except (ValueError, TypeError) as e:
            raise UiError(str(e)) from None
        raise UiError("Unknown action", 404)

    def _refactor(self, ws: Path, name: str, action: str, body: dict, test_cmd) -> dict:
        repo = ws / "repo"
        if action == "refactor-dead":
            dead = refactor.dead_code(repo)
            if not dead:
                raise UiError("No dead-code candidates found")
            return {"ok": True, "task": self._enqueue(ws, refactor.dead_code_task(dead), "refactor")["id"]}
        if action == "refactor-split":
            big = next((b for b in refactor.big_modules(repo) if b["file"] == body.get("file")), None)
            if not big:
                raise UiError("That module is not over the size limit")
            return {"ok": True, "task": self._enqueue(ws, refactor.split_task(big["file"], big["lines"]), "refactor")["id"]}
        old, new = str(body.get("old", "")), str(body.get("new", ""))
        if not body.get("apply"):
            return {"ok": True, "plan": refactor.rename_plan(repo, old, new)}
        if self._running(ws, name):
            raise UiError("Stop the agent first: the rename changes many files", 409)
        res = refactor.rename(repo, old, new, test_cmd())
        events.add(ws / "agent", "control", f"renamed {old} to {new} in {res['files']} file(s)")
        return res

    # ---- global
    def ext_global(self, what: str, body: dict | None = None) -> dict:
        if what == "ctx":
            return ctxtune.detect(sa.DEFAULT_CONFIG["llm_api"].rsplit("/v1", 1)[0] or "http://localhost:11434")
        if what == "ctx-write":
            body = body or {}
            try:
                return ctxtune.write_modelfile(common.ROOT / "scripts", str(body.get("model", "")), int(body.get("num_ctx", 0)))
            except (ValueError, TypeError) as e:
                raise UiError(str(e)) from None
        raise UiError("Not found", 404)

    # ---- chat agent
    def chat_action(self, cid: str, call: dict) -> dict:
        chat = self.chats.load(cid)
        try:
            tool, args = chatagent.validate(call, chat["projects"])
        except ValueError as e:
            raise UiError(str(e)) from None
        text = self._run_tool(tool, args)
        chat["messages"].append({"role": "user", "content": f"[action done] {chatagent.describe(tool, args)}\n{text}", "t": time.time(), "action": True})
        self.chats.save(chat)
        return {"ok": True, "result": text, "chat": chat}

    def _run_tool(self, tool: str, args: dict) -> str:
        name = str(args.get("project", ""))
        if tool == "create_project":
            r = self.create({"idea": args["idea"], "name": args.get("name", ""), "template": args.get("template", "")})
            return f"Project '{r['name']}' is being created."
        ws = self.ws(name)
        a = ws / "agent"
        if tool == "status":
            s = self.summary(ws)
            bl = sa.read_json(a / "backlog.json", [])
            counts: dict[str, int] = {}
            for i in bl:
                counts[i["status"]] = counts.get(i["status"], 0) + 1
            return f"running={s['running']}, tasks={counts}, phase={s.get('phase', '')}"
        if tool == "search_code":
            return "\n".join(f"{h['kind']} {h['name']} {h['file']}:{h['line']}" for h in codesearch.search(ws / "repo", args["query"], 8)) or "no hits"
        if tool == "add_task":
            r = self.act(name, "do", {"task": args["text"]})
            return "queued for the running agent" if r.get("queued") else "task started"
        if tool in ("start", "stop"):
            self.act(name, tool, {})
            return f"{tool} requested"
        if tool in ("pause", "resume", "step_mode", "run_next", "hint"):
            kw = {"pause": {"paused": True}, "resume": {"paused": False}, "step_mode": {"step_mode": bool(args.get("on", True))},
                  "run_next": {"next": str(args.get("task", ""))}, "hint": {"hint": str(args.get("text", ""))}}[tool]
            self.ext_act(ws, name, "control", kw)
            return f"{tool} set"
        if tool == "set_priority":
            pr = args["priority"]
            if pr == "top":
                self.act(name, "task-top", {"id": args["task"]})
            else:
                self.ext_act(ws, name, "task-prio", {"id": args["task"], "priority": pr})
            return "priority changed"
        if tool == "generate_tests":
            return f"task {self.ext_act(ws, name, 'testgen', {'file': args['file']})['task']} queued"
        raise UiError("Unknown tool", 404)
