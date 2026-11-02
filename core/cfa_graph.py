"""Graph planning for the agent: merging and splitting tasks inside the dependency graph, plus the re-plan at the start of a run."""
from __future__ import annotations

from core import taskgraph
from core.cfutil import clip

MIN_OPEN = 3  # a start-of-run re-plan needs at least this many open tasks


class GraphMixin:
    def merge_tasks(self, spec: dict, by_id: dict[str, dict]) -> dict | None:
        """Replaces several pending tasks by one: it inherits their outside dependencies, whoever waited for a member now waits for it."""
        members = [by_id[i] for i in spec["ids"] if i in by_id and by_id[i]["status"] == "pending"]
        if len(members) < 2:
            return None
        ids = {m["id"] for m in members}
        deps = list(dict.fromkeys(d for m in members for d in m.get("depends_on", []) if d not in ids))
        layers = [m["layer"] for m in members if m.get("layer") is not None]
        accept = spec["acceptance"] or [a for m in members for a in m.get("acceptance", [])][:6]
        new = self.backlog.add(spec["title"], spec["description"] or members[0].get("description", ""), members[0]["type"],
                               max(m["priority"] for m in members), accept, f"Merged {', '.join(sorted(ids))}: {spec['why']}", "blueprint",
                               force=True, layer=max(layers) if layers else None, depends_on=deps)
        for other in self.backlog.items:
            if other["id"] not in ids and ids & set(other.get("depends_on", [])):
                other["depends_on"] = list(dict.fromkeys(new["id"] if d in ids else d for d in other["depends_on"]))
        for m in members:
            self.backlog.set(m, status="cancelled", merged_into=new["id"], reason=f"merged into {new['id']}: {clip(spec['why'], 150) or 'one change instead of several'}",
                             notes=m.get("notes", []) + [f"merged into {new['id']} by the re-evaluation"])
        by_id[new["id"]] = new
        return new

    def split_into_steps(self, item: dict, steps: list[dict], by_id: dict[str, dict]) -> list[str]:
        """Replaces a big pending task by a chain of small steps; whoever waited for it now waits for the last step."""
        ids, prev = [], list(item.get("depends_on", []))
        for n, s in enumerate(steps):
            new = self.backlog.add(s["title"], s["description"], item["type"], item["priority"] + 1 - 0.1 * n, s["acceptance"] or item.get("acceptance", []),
                                   f"Step {n + 1}/{len(steps)} of {item['id']}", "blueprint", force=True, layer=item.get("layer"), depends_on=prev)
            ids.append(new["id"])
            by_id[new["id"]] = new
            prev = [new["id"]]
        for other in self.backlog.items:
            if item["id"] in other.get("depends_on", []) and other["id"] not in ids:
                other["depends_on"] = [ids[-1] if d == item["id"] else d for d in other["depends_on"]]
        self.backlog.set(item, status="cancelled", split_into=ids, reason=f"planned as {len(ids)} steps ({', '.join(ids)})",
                         notes=item.get("notes", []) + [f"split into {', '.join(ids)} by the re-evaluation"])
        return ids

    def graph_replan_start(self) -> bool:
        """Once at the start of a run: lays the open tasks out as a graph again, but only if the backlog changed since the last plan
        (new, finished or cancelled tasks), so an unchanged project does not spend a model call."""
        if getattr(self, "_graph_started", False) or not self.feat("graph_replan"):
            return False
        self._graph_started = True
        self.backlog.reload()
        open_ = [i for i in self.backlog.items if i["status"] in ("pending", "blocked")]
        if len(open_) < MIN_OPEN or taskgraph.signature(self.backlog.items) == self.state.get("graph_sig"):
            return False
        self.log(f"Graph plan: the backlog changed since the last plan, laying out the {len(open_)} open task(s) as a graph")
        try:
            self.reevaluate(requeue=False, state_note=self._state_note())
        except Exception as e:  # noqa: BLE001
            self.log(f"Graph plan failed ({clip(str(e), 200)}), keeping the current graph")
            return False
        return True

    def downstream_note(self, item: dict) -> str:
        """For the planning prompt of a task: which open tasks build on it, so it keeps its interfaces usable for them."""
        deps = taskgraph.deps_of(self.backlog.items)
        by = {i["id"]: i for i in self.backlog.items}
        later = [k for k, ds in deps.items() if item["id"] in ds and by[k]["status"] in ("pending", "blocked")]
        if not later:
            return ""
        return "Tasks that build directly on this one (keep what they need stable): " + "; ".join(f"{k} {clip(str(by[k]['title']), 60)}" for k in later[:6])
