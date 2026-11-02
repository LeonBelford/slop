"""Graph planning for the agent: merging and splitting tasks inside the dependency graph, plus the re-plan at the start of a run."""
from __future__ import annotations

import hashlib

from core import taskgraph
from core.cfa_conductor import REPLAN_EVERY_MERGES
from core.cfa_triage import MIN_OPEN, RELAYOUT_EVERY_MERGES
from core.cfutil import clip, now, read_json, write_json

HISTORY_MAX = 12  # snapshots kept for the replay timeline
BLOCK_IMPACT = 2  # a blocked task that holds back this many tasks gets its own mini re-plan


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

    # ---- the one graph review: every trigger is collected, the model is asked once
    def directives_hash(self) -> str:
        """Fingerprint of what you wrote (goal and ideas), so a change always triggers a review."""
        ideas = self.ideas_path.read_text("utf-8") if self.ideas_path.exists() else ""
        return hashlib.sha1((self.goal + "\0" + ideas).encode()).hexdigest()[:12]

    def review_reasons(self) -> tuple[list[str], list[str]]:
        """Why the graphs should be reviewed now (human readable) and which tasks the review should focus on (blocked chains)."""
        items = self.backlog.items
        open_ = [i for i in items if i["status"] in ("pending", "blocked")]
        if not open_:
            return [], []
        reasons, focus = [], []
        merged = self.state.get("merged", 0)
        flaws = taskgraph.problems(items)
        if self.feat("graph_replan") and not getattr(self, "_graph_started", False):
            changed = taskgraph.signature(items) != self.state.get("graph_sig")
            if changed or flaws or self.feat("graph_review_always"):
                reasons.append("start of the run" + ("" if changed else " (nothing changed since the last review)"))
        if "directives_hash" not in self.state:
            self.state["directives_hash"] = self.directives_hash()  # first look: the start review covers it
        elif self.directives_hash() != self.state["directives_hash"]:
            reasons.append("GOAL.md or IDEAS.md changed: turn the new wishes into tasks")
        if self.custom_pending():
            reasons.append("custom graph(s) to build out: " + ", ".join(g["title"] for g in self.custom_pending())[:100])
        if self.state.get("milestone_replan"):
            reasons.append("the milestone review found the product drifting from the vision")
        blocked = sum(1 for i in items if i["status"] == "blocked")
        spaced = merged - self.state.get("auto_replan_at", -REPLAN_EVERY_MERGES) >= REPLAN_EVERY_MERGES
        if (blocked >= 3 or self.state.get("consecutive_failures", 0) >= 3) and spaced:
            reasons.append("the project is stuck (blocked or failing tasks)")
        since = merged - self.state.get("triage_at", 0)
        unlayered = [i for i in self.backlog.pending() if i.get("layer") is None and i.get("source") != "user"]
        if (since >= RELAYOUT_EVERY_MERGES and len(open_) >= MIN_OPEN) or (len(unlayered) >= 2 and since >= 1):
            reasons.append(f"{since} merge(s) since the last layout")
        by = {i["id"]: i for i in items}
        for b, who in taskgraph.blocked_impact(items).items():
            if len(who) >= BLOCK_IMPACT and not by[b].get("block_reviewed"):
                reasons.append(f"blocked task {b} holds back {len(who)} task(s)")
                focus += [b, *who]
        if reasons and flaws:
            reasons.append("structure: " + "; ".join(flaws[:4]))
        return reasons, list(dict.fromkeys(focus))

    def graph_review(self) -> bool:
        """Runs ONE re-evaluation for all current reasons (instead of one call per trigger). True when the plan was looked at."""
        reasons, focus = self.review_reasons()
        if not reasons:
            self._graph_started = True
            return False
        merged = self.state.get("merged", 0)
        self._graph_started = True
        self.state.pop("milestone_replan", None)
        self.state["triage_at"] = merged  # also after a failure: never retry on every iteration
        if any(r.startswith(("the project is stuck", "the milestone")) for r in reasons):
            self.state["auto_replan_at"] = merged
        for i in self.backlog.items:
            if i["id"] in focus and i["status"] == "blocked":
                i["block_reviewed"] = True  # one mini re-plan per blocked task
        self.backlog.save()
        why = "; ".join(reasons)
        self.log(f"Graph review: {why}")
        note = self._state_note() + (f" Review reasons: {why}." if why else "")
        only = focus if focus and all(r.startswith(("blocked task", "structure:")) for r in reasons) else None
        if only:
            note += " Focus on the graph(s) around the blocked task: unblock the waiting tasks (split it, change the approach, re-link the followers, or cancel it with a reason)."
        try:
            self.reevaluate(requeue=False, state_note=note, focus=only, reason=why[:160])
        except Exception as e:  # noqa: BLE001
            self.log(f"Graph review failed ({clip(str(e), 200)}), keeping the current graph")
            return False
        return True

    # ---- history (replay) and the confirm-before-apply proposal
    @property
    def history_path(self):
        return self.dir / "graph_history.json"

    def record_graph(self, reason: str, stats: dict) -> None:
        """Snapshot of the graphs for the replay timeline; skipped when the structure is the same as in the last one."""
        hist = read_json(self.history_path, [])
        snap = taskgraph.snapshot(self.backlog.items, reason, stats, now())
        if hist and hist[-1].get("sig") == snap["sig"]:
            return
        write_json(self.history_path, (hist + [snap])[-HISTORY_MAX:])

    @property
    def proposal_path(self):
        return self.dir / "plan_proposal.json"

    def save_proposal(self, data: dict, res: dict, items: list[dict], requeue: bool, focus: list[str] | None) -> dict:
        """Writes what the re-evaluation wants to change as a readable diff; nothing is applied until you confirm in the UI."""
        if res["keep"] and not (res["new"] or res["cancel"] or res["merge"] or res["split"]):
            return self.apply_reevaluation(data, items, requeue, focus, "reviewed")
        title = {i["id"]: i["title"] for i in items}
        why = {str(c.get("id")): c.get("why", "") for c in data.get("cancel", []) if isinstance(c, dict)}
        lines = [f"NEW {t['title']}: {t.get('why') or '-'}" for t in res["new"]]
        lines += [f"CANCEL {i} {title.get(i, '')[:60]}: {why.get(i) or '-'}" for i in res["cancel"]]
        lines += [f"MERGE {', '.join(m['ids'])} -> {m['title']}: {m['why'] or '-'}" for m in res["merge"]]
        lines += [f"SPLIT {sp['id']} {title.get(sp['id'], '')[:50]} -> " + " | ".join(st["title"] for st in sp["steps"]) for sp in res["split"]]
        moved = [i for i, a in res["assign"].items() if next((x.get("layer") for x in items if x["id"] == i), None) != a["layer"]]
        if moved:
            lines.append(f"RE-LAYER {len(moved)} task(s)")
        cur = {i["id"]: i for i in items}
        for i, a in res["assign"].items():
            if a["depends_on"] and i in cur and cur[i]["status"] in ("pending", "blocked") and set(a["depends_on"]) != set(cur[i].get("depends_on") or []):
                lines.append(f"RE-LINK {i} {title.get(i, '')[:50]}: now needs {', '.join(a['depends_on'])}" + (f" ({a['why']})" if a.get("why") else ""))
        if not lines:
            self.proposal_path.unlink(missing_ok=True)
            self.log("The model found nothing concrete to change (no usable new, cancelled, merged, split or re-linked tasks): no proposal made.")
            self.phase("Idle (re-plan preview: nothing to change)", None)
            return {"proposal": 0}
        write_json(self.proposal_path, {"ts": now(), "assessment": res.get("assessment", ""), "lines": lines, "data": data,
                                        "requeue": requeue, "focus": focus or [], "reason": getattr(self, "_review_reason", "manual")})
        self.log(f"Plan proposal saved ({len(lines)} change(s)); waiting for your confirmation in the UI.")
        self.phase("Idle (plan proposal waiting for you)", None)
        return {"proposal": len(lines)}

    def apply_proposal(self) -> dict:
        prop = read_json(self.proposal_path, None)
        if not prop:
            raise RuntimeError("There is no plan proposal")
        self.backlog.reload()
        items = [i for i in self.backlog.items if i["status"] != "cancelled"]
        if prop.get("focus"):
            scope = {k for c in taskgraph.components(items) if set(c) & set(prop["focus"]) for k in c}
            items = [i for i in items if i["id"] in scope]
        res = self.apply_reevaluation(prop["data"], items, bool(prop.get("requeue")), prop.get("focus") or None, "confirmed")
        self.proposal_path.unlink(missing_ok=True)
        return res

    def downstream_note(self, item: dict) -> str:
        """For the planning prompt of a task: which open tasks build on it, so it keeps its interfaces usable for them."""
        deps = taskgraph.deps_of(self.backlog.items)
        by = {i["id"]: i for i in self.backlog.items}
        later = [k for k, ds in deps.items() if item["id"] in ds and by[k]["status"] in ("pending", "blocked")]
        if not later:
            return ""
        return "Tasks that build directly on this one (keep what they need stable): " + "; ".join(f"{k} {clip(str(by[k]['title']), 60)}" for k in later[:6])
