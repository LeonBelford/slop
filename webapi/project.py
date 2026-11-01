"""Per-project API: detail/activity/logs views, settings and project CRUD."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import codefactory as sa
from core import cfprofile
from dash import throughput
from infra import models, notify

from webapi.common import FEATURE_KEYS, NAME_RE, SETTING_LIMITS, UiError, alive, pid_alive, read_live, spawn, tail


class ProjectMixin:
    """Service mixin: per-project detail/activity/log views, settings and project CRUD."""

    def detail(self, name: str) -> dict:
        ws = self.ws(name)
        a = ws / "agent"
        feats = sa.read_json(a / "memory" / "features.json", {})
        backlog = sorted(sa.read_json(a / "backlog.json", []), key=lambda i: -i["priority"])
        def read(p):
            return p.read_text("utf-8") if p.exists() else ""
        sm = self.summary(ws)
        live = self.live_rate(read_live(a / "llm_stream.json", sm["running"]))
        if not live["active"] and sm["running"]:
            live = self.coder_rate(name, a) or live
        conf = sa.read_json(a / "config.json", {})
        return {**sm, "perf": throughput.perf(sa.read_json(a / "metrics.json", {})), "live": live, "gpu": self.gpu_stats(), "ideas": read(ws / "IDEAS.md"), "goal": read(a / "GOAL.md"), "testing": read(ws / "TESTING.md"), "sources": read(ws / "SOURCES.md"), "web_mode": conf.get("web", {}).get("mode", "sources"),
                "notify": conf.get("notify", {"webhook": "", "events": list(notify.EVENTS)}),
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
                raise UiError(f"{k} must be a number") from None
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
        if "notify" in body:
            nb = body["notify"] if isinstance(body["notify"], dict) else {}
            url = str(nb.get("webhook", "")).strip()
            if url and not notify.valid_url(url):
                raise UiError("Webhook must be a plain http(s) URL")
            events = [e for e in (nb.get("events") or []) if e in notify.EVENTS]
            conf["notify"] = {"webhook": url, "events": events or list(notify.EVENTS)}
        sa.write_json(ws / "agent" / "config.json", conf)

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
                "sources": (ws / "SOURCES.md").read_text("utf-8") if (ws / "SOURCES.md").exists() else "",
                "notify": eff.get("notify", {"webhook": "", "events": list(notify.EVENTS)})}

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

