"""Dashboard API: the live snapshot shared by every open tab, history and slow-moving diagnostics."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
import urllib.request

import codefactory as sa
from dash import diagnostics, history, runledger, throughput
from infra import perf

from webapi import common
from webapi.common import alive, read_live


class DashboardMixin:
    """Service mixin: the Dashboard snapshot (running agents, rates, GPU, models), history and diagnostics."""

    @staticmethod
    def gpu_stats() -> dict | None:
        c = getattr(DashboardMixin, "_gpu", None)
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
        DashboardMixin._gpu = (time.time(), res)
        return res

    @staticmethod
    def loaded_models() -> list[dict]:
        u = urllib.parse.urlparse(sa.DEFAULT_CONFIG["llm_api"])
        try:
            with urllib.request.urlopen(f"{u.scheme}://{u.netloc}/api/ps", timeout=1.5) as r:
                return [{"name": m.get("name", ""), "vram_gb": round(m.get("size_vram", 0) / 1e9, 1)} for m in json.loads(r.read()).get("models", [])]
        except Exception:  # noqa: BLE001
            return []

    def dashboard(self, max_age: float = 1.0) -> dict:
        """The dashboard snapshot, shared by every open tab and stream: rebuilt at most once per `max_age` seconds."""
        with self._dash_lock:
            c = getattr(self, "_dash", None)
            if c and time.time() - c[0] < max_age:
                return c[1]
            res = self._dashboard_build()
            self._dash = (time.time(), res)
            return res

    def history(self) -> dict:
        """Per-day series per project, for the whole tool and for self-improve (cached for 15 s)."""
        c = getattr(self, "_hist", None)
        if c and time.time() - c[0] < 15:
            return c[1]
        per = {p.name: history.daily(p / "agent") for p in sa.list_projects(self.projects)}
        res = {"projects": per, "total": history.merge(list(per.values())), "tool": history.daily(common.ROOT / "self_improve")}
        self._hist = (time.time(), res)
        return res

    def _dashboard_build(self) -> dict:
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
            rows.append({"name": "Self-improve", "tool": True, "running": True, "priority": "main", "iteration": 0, "state": "", "perf": throughput.perf(sa.read_json(common.ROOT / "self_improve" / "metrics.json", {})),
                         "live": self.live_rate(read_live(common.ROOT / "self_improve" / "llm_stream.json", True)), **{k: sa.read_json(common.ROOT / "self_improve" / "metrics.json", {}).get(k, 0) for k in ("calls", "tokens", "chars")}, "coder_chars": 0})
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
            projects.append({**diagnostics.project(p, summ["running"], summ["hours"]), "running": summ["running"], "merged": summ["merged"],
                             "quality": history.quality(p / "agent")})
        oc = getattr(DashboardMixin, "_oll", None)
        if not oc or time.time() - oc[0] > 30:
            DashboardMixin._oll = oc = (time.time(), perf.diagnose())
        sysinfo = diagnostics.system(oc[1])
        res = {"time": time.time(), "projects": projects, "system": sysinfo, "findings": diagnostics.findings(projects, sysinfo),
               "tool": {**runledger.stats(common.ROOT / "self_improve"), "durations": diagnostics.tool_durations(common.ROOT / "self_improve"),
                         "quality": history.quality(common.ROOT / "self_improve")}}
        self._diag = (time.time(), res)
        return res

