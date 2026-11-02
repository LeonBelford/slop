"""Task-graph API: summaries for the overview, manual graph edits (link, unlink, merge, split, custom graphs), the plan proposal and the replay timeline."""
from __future__ import annotations

from pathlib import Path

import codefactory as sa
from core import graph_ideas, taskgraph

from webapi.common import UiError, alive, spawn


class GraphEditMixin:
    def graph_extras(self, a: Path, backlog: list[dict]) -> dict:
        """What the overview needs besides the tasks: per-graph summaries, what blocked tasks hold back, the open proposal."""
        impact = taskgraph.blocked_impact(backlog)
        prop = sa.read_json(a / "plan_proposal.json", None)
        return {"graphs": taskgraph.summaries(backlog), "blocks": {k: len(v) for k, v in impact.items() if v},
                "proposal": {k: prop.get(k) for k in ("ts", "assessment", "lines", "reason")} if prop else None,
                "graph_ideas": graph_ideas.load_ideas(a / "graph_ideas.json", sa.read_json)["ideas"],
                "processes": sa.read_json(a / "processes.json", [])}

    def graph_history(self, name: str) -> dict:
        return {"history": sa.read_json(self.ws(name) / "agent" / "graph_history.json", [])}

    def graph_edit(self, ws: Path, name: str, action: str, body: dict) -> dict:
        a = ws / "agent"
        if action == "replan-preview":
            return self.start_replan(ws, name, ["--preview"])
        if action == "plan-apply":
            if not (a / "plan_proposal.json").exists():
                raise UiError("There is no plan proposal", 404)
            return self.start_replan(ws, name, ["--apply"])
        if action == "plan-reject":
            (a / "plan_proposal.json").unlink(missing_ok=True)
            return {"ok": True}
        ag = sa.Agent(ws)
        bl = ag.backlog
        bl.reload()
        by = {i["id"]: i for i in bl.items}

        def need(key: str) -> dict:
            item = by.get(str(body.get(key, "")))
            if not item or item["status"] == "cancelled":
                raise UiError("Task not found", 404)
            return item

        if action == "graph-custom":
            try:
                made = sa.Agent(ws).add_custom_graph(str(body.get("name", "")), str(body.get("goal", "")), str(body.get("steps", "")))
            except ValueError as e:
                raise UiError(str(e)) from None
            return {"ok": True, "ids": [i["id"] for i in made]}
        if action in ("graph-link", "graph-unlink"):
            item, other = need("id"), need("needs")
            if item is other:
                raise UiError("A task cannot wait for itself")
            deps = list(item.get("depends_on") or [])
            if action == "graph-unlink":
                item["depends_on"] = [d for d in deps if d != other["id"]]
                if not item["depends_on"]:
                    item.pop("depends_on")
            else:
                if item["status"] not in ("pending", "blocked"):
                    raise UiError("Only open tasks can get a new dependency")
                if other["id"] not in deps:
                    item["depends_on"] = deps + [other["id"]]
                if taskgraph.break_cycles(taskgraph.deps_of(bl.items)) != taskgraph.deps_of(bl.items):
                    raise UiError("That link would create a cycle")
            bl.save()
            return {"ok": True}
        if action == "graph-merge":
            ids = [str(i) for i in body.get("ids", [])][:6]
            members = [by[i] for i in ids if i in by and by[i]["status"] == "pending"]
            if len(members) < 2:
                raise UiError("Select at least two open (pending) tasks to merge")
            title = str(body.get("title") or " + ".join(m["title"] for m in members))[:120]
            new = ag.merge_tasks({"ids": [m["id"] for m in members], "title": title, "description": "\n".join(m.get("description", "") for m in members)[:1500],
                                  "acceptance": [], "why": str(body.get("why") or "merged by you in the UI")}, by)
            return {"ok": True, "id": new["id"] if new else None}
        if action == "graph-split":
            item = need("id")
            steps = [str(s).strip() for s in body.get("steps", []) if str(s).strip()][:4]
            if item["status"] != "pending" or len(steps) < 2:
                raise UiError("Split needs an open task and at least two step titles")
            ids = ag.split_into_steps(item, [{"title": t[:120], "description": f"{t}. Part of: {item['title']}. {item.get('description', '')}"[:1500],
                                              "acceptance": []} for t in steps], by)
            return {"ok": True, "ids": ids}
        if action.startswith(("idea-", "process-")):
            return self.graph_routine(ws, action, body)
        raise UiError("Unknown graph action")

    def graph_routine(self, ws: Path, action: str, body: dict) -> dict:
        """AI graph ideas (add / dismiss) and your own standard processes (save / delete / run)."""
        a = ws / "agent"
        ag = sa.Agent(ws)
        if action.startswith("idea-"):
            cur = graph_ideas.load_ideas(a / "graph_ideas.json", sa.read_json)
            idea = next((i for i in cur["ideas"] if i["id"] == body.get("id")), None)
            if not idea:
                raise UiError("Idea not found", 404)
            made = []
            if action == "idea-add":
                made = graph_ideas.add_graph(ag.backlog, idea["steps"], f"Idea: {idea['title']}", "idea", idea.get("why", ""))
            sa.write_json(a / "graph_ideas.json", {"ideas": [i for i in cur["ideas"] if i is not idea], "dismissed": (cur["dismissed"] + [idea["title"]])[-40:]})
            return {"ok": True, "ids": [m["id"] for m in made]}
        procs = sa.read_json(a / "processes.json", [])
        if action == "process-save":
            pid = str(body.get("id") or "") or "P" + str(max([int(p["id"][1:]) for p in procs if p["id"][1:].isdigit()] + [0]) + 1)
            try:
                proc = graph_ideas.norm_process(body, pid)
            except ValueError as e:
                raise UiError(str(e)) from e
            sa.write_json(a / "processes.json", [p for p in procs if p["id"] != pid] + [proc])
            return {"ok": True, "id": pid}
        proc = next((p for p in procs if p["id"] == body.get("id")), None)
        if not proc:
            raise UiError("Process not found", 404)
        if action == "process-delete":
            sa.write_json(a / "processes.json", [p for p in procs if p is not proc])
            return {"ok": True}
        if action == "process-run":
            return {"ok": True, "ids": [m["id"] for m in ag.run_process(proc)]}
        raise UiError("Unknown graph action")

    def start_replan(self, ws: Path, name: str, flags: list[str]) -> dict:
        if self.summary(ws)["running"]:
            raise UiError("Stop the agent first: this rewrites the task list.", 409)
        if alive(f"replan:{name}"):
            raise UiError("A re-evaluation is already running", 409)
        spawn(f"replan:{name}", ["replan", name, *flags], ws / "agent" / "replan.out")
        return {"ok": True}
