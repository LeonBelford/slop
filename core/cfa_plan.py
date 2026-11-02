"""Strategic planning: turns the product brief into an architecture blueprint (layers, core first) and seeds the backlog from it."""
from __future__ import annotations

import copy
import json

from core import blueprint, taskgraph, uidetect
from core.cfllm import scaled
from core.cfutil import clip, write_text

SYSTEM = ("You are a software architect planning a build strategy, not a feature list. Find the smallest kernel that makes every later "
          "feature cheap and safe (domain model, storage, core logic, public interfaces, error handling, configuration, test harness) "
          "and put it in layer 0. Every next layer builds only on the public interfaces of the layers below and adds user-visible "
          "capability. Keep interfaces stable so lower layers are never reworked. The last layer is polish and hardening. Tasks are "
          "small enough for one iteration. Choose the libraries up front in `dependencies` (name, exact version, reason); never leave library choice to later tasks. Layer 0 starts with a walking skeleton: the thinnest end-to-end path (input, logic, storage, output) with one test, marked skeleton=true. Mark uncertain tasks (unknown library, parser, performance) risk=high and place them early. The tasks form a dependency GRAPH, not a flat list: a task lists in depends_on every task whose result it needs, and only those. Work that does not need each other stays in separate branches (no artificial chaining); a task that needs several branches joins them. A big step becomes a short chain of small steps. Realistic and focused, no monetization.")

SCHEMA = ('{"architecture": {"principles": ["..."], "modules": [{"name": "", "responsibility": "", "uses": ["names of modules it may import"]}], '
          '"contracts": ["stable interface, e.g. function or endpoint signature and its meaning"], "decisions": ["decision and why"]}, "dependencies": [{"name": "", "version": "", "reason": ""}], '
          '"layers": [{"name": "", "goal": "what exists and works after this layer", "tasks": [{"key": "short-unique-id", "title": "", '
          '"description": "", "acceptance": ["testable"], "depends_on": ["keys of earlier tasks"], "type": "feature|refactor|test", '
          '"risk": "low|high", "skeleton": false, "why": "one short sentence: why this task is needed", "value": 1-10, "effort": 1-10}]}]}')


REEVAL_SCHEMA = ('{"keep_plan": true if the graphs are sound and nothing needs to change (then leave all other lists empty), "assessment": "what is wrong with the current order, 2-4 sentences", "architecture": {"principles": ["..."], '
                 '"modules": [{"name": "", "responsibility": ""}], "contracts": ["..."], "decisions": ["..."]}, '
                 '"layers": [{"name": "", "goal": ""}], "assign": [{"id": "T0001", "layer": 0, "depends_on": ["T0000"], "priority": 1-10, "why": "only if the reason changed"}], '
                 '"new_tasks": [{"key": "short-id", "title": "", "description": "", "acceptance": ["testable"], "type": "feature|refactor|test", '
                 '"layer": 0, "depends_on": ["task ids or keys of earlier tasks"], "why": "short reason this task is needed", "value": 1-10, "effort": 1-10}], '
                 '"cancel": [{"id": "T0002", "why": ""}], "merge": [{"ids": ["T0004", "T0005"], "title": "", "description": "", "acceptance": ["testable"], "why": ""}], '
                 '"split": [{"id": "T0009", "steps": [{"title": "", "description": "", "acceptance": ["testable"], "why": ""}]}], '
                 '"graph_ideas": [{"title": "new feature or improvement", "why": "why it fits this project", "steps": [{"title": "", "description": "", "type": "feature|refactor|test", "acceptance": ["testable"], "needs": [0], "why": ""}]}]}. Every open and finished task should appear in assign.')
GRAPH_RULES = (" Think in a graph: every task lists in depends_on exactly the tasks whose result it needs (never a cycle); independent work stays in separate branches, "
               "a task needing several branches joins them. Merge pending tasks that touch the same code or would be one change anyway (merge, at most 5 ids each, never in_progress "
               "or user tasks). Split a pending task that is too big for one iteration into 2 to 4 small steps (split). Use new_tasks for missing steps. "
               "Review every graph: do the steps make sense, are they in the right order and size, is a step missing for the vision, is a step still needed? "
               "Every task carries a `why`; keep a task while its why holds, cancel it only with a concrete reason (cancel needs `why`), and give new tasks a why. "
               "Besides, suggest up to 3 graph_ideas: new features or improvements (2 to 5 steps each, needs = indexes of earlier steps) that fit the goal and build on the features already built; "
               "never repeat an existing task or graph, and leave graph_ideas empty if nothing useful comes to mind. "
               "Re-read the Goal and the user directives (IDEAS.md) every time: every wish in them that no task covers yet becomes a new task (or graph) with a why; open tasks that contradict them are cancelled with a reason. "
               "Do NOT rebuild what is sound: without a valid reason change nothing, set keep_plan true and leave the lists empty. Moving or re-linking a task needs a reason too.")


class PlanMixin:
    """Agent mixin: blueprint creation at init, blueprint context for tasks and layer-completion bookkeeping."""

    @property
    def blueprint_path(self):
        return self.dir / "blueprint.json"

    def blueprint(self) -> dict | None:
        """The saved blueprint, or None for projects planned before layers existed."""
        return blueprint.load(self.blueprint_path)

    def has_ui(self) -> bool:
        """Whether the product has a UI that UI tests can drive (see core/uidetect.py)."""
        return uidetect.has_ui(self.repo, self.dir / "interface.json")

    def ui_note(self) -> str:
        """Tells the planner whether UI tests make sense for this product."""
        if self.has_ui():
            return "\nUI tests: the product has a UI; add a UI test only for changes that users see or click.\n"
        return "\nThis product has no UI (yet): plan no UI, browser or screenshot tests; test behaviour through the CLI, API or functions.\n"

    def top_layer(self) -> int | None:
        """Layer index for extension work added later (ideas, reviews): above all planned layers."""
        bp = self.blueprint()
        return len(bp["layers"]) if bp else None

    def blueprint_ctx(self, item: dict) -> str:
        """Where a task sits in the architecture, for the planning prompt."""
        base = [self.backlog.by_id(d) for d in item.get("depends_on", [])]
        built = [f"{b['id']} {b['title']}" for b in base if b and b["status"] == "done"]
        text = blueprint.context_for(self.blueprint(), item)
        if built:
            text += "\nBuilds on (already merged, extend it, do not redo it): " + "; ".join(built)
        down = self.downstream_note(item)
        if item.get("scout"):
            text += f"\nLikely relevant files (scouted, check before relying on it): {item['scout']}"
        if item.get("why"):
            text += f"\nWhy this task exists (the purpose to serve): {item['why']}"
        return text + ("\n" + down if down else "")

    def make_blueprint(self, idea: str, brief: dict) -> bool:
        """Plans the layers and seeds the backlog from them. False (nothing changed) if the model gave no usable plan."""
        self.phase("Designing the architecture blueprint (core first)", "plan")
        try:
            data = self.llm.json(
                "blueprint", "strategist", SYSTEM,
                f"Idea: {idea}\nStack: {self.cfg['stack']}\nVision: {brief.get('vision', '')}\nProblems: {brief.get('problems', [])}\n"
                f"Features wanted (each must end up in some layer): {json.dumps(brief.get('mvp_features', []))}\n"
                f"Performance goals: {brief.get('performance_goals', [])}\nNon-goals: {brief.get('non_goals', [])}\n"
                f"3 to 5 layers, at most {blueprint.MAX_TASKS} tasks each. Schema: {SCHEMA}")
            bp = blueprint.normalize(data, brief.get("mvp_features", []), skeleton=bool(self.feat("skeleton")),
                                     deps_task=bool(self.feat("dependency_plan")), contracts_task=bool(self.feat("contracts_code")), chain=not self.feat("graph_plan"))
        except Exception as e:  # noqa: BLE001  (a failed plan must not stop the project: the flat MVP list is the fallback)
            self.log(f"Blueprint failed, using the flat MVP list: {e}")
            return False
        if not bp["layers"]:
            self.log("Blueprint was empty, using the flat MVP list.")
            return False
        ids: dict[str, str] = {}
        for li, layer in enumerate(bp["layers"]):
            for t in layer["tasks"]:
                item = self.backlog.add(t["title"], t["description"], t["type"], t["priority"], t["acceptance"],
                                        f"Layer {li}: {layer['name']}", "blueprint", force=True, layer=li,
                                        depends_on=[ids[d] for d in t["depends_on"] if d in ids], why=t.get("why"))
                ids[t["key"]] = item["id"]
                t["id"] = item["id"]
                item["key"] = t["key"]
                if t.get("risk") == "high":
                    item["risk"] = "high"
        blueprint.save(self.blueprint_path, bp)
        write_text(self.repo / "docs" / "BLUEPRINT.md", blueprint.render_md(bp))
        sizes = ", ".join(f"{ly['name']} ({len(ly['tasks'])})" for ly in bp["layers"])
        self.log(f"Blueprint: {len(bp['layers'])} layers, built from the core upwards: {sizes}")
        return True

    def layer_done(self, item: dict) -> None:
        """When the last open task of a layer is finished: log it, refresh the architecture doc and tag the commit as a rollback point."""
        layer, bp = item.get("layer"), self.blueprint()
        if layer is None or not bp or layer >= len(bp["layers"]):
            return
        if any(i.get("layer") == layer and i["status"] in ("pending", "in_progress") for i in self.backlog.items):
            return
        name = bp["layers"][layer]["name"]
        if not self.layer_gate(layer, name):
            return
        self.log(f"Layer {layer} '{name}' complete: everything above builds on it from here.")
        self.metric(kind="layer_done", layer=layer, name=name)
        try:
            self.git.run("tag", "-f", f"layer-{layer}", "main")
            self.mem.refresh_architecture()
        except Exception as e:  # noqa: BLE001  (bookkeeping only)
            self.log(f"Layer bookkeeping skipped: {clip(str(e), 200)}")
        self.milestone_review(layer, name)
        self.doc_sync(layer, name)

    def _reeval_note(self, data: dict, res: dict, by_title: dict) -> None:
        """Puts the verdict per task (layer, priority, cancel reason, new tasks) into the Reasoning tab as its own card."""
        why = {str(c.get("id")): c.get("why", "") for c in data.get("cancel", []) if isinstance(c, dict)}
        lines = [f"Assessment: {res.get('assessment') or data.get('assessment', '-')}", "", "Layers: " + ", ".join(
            f"{n}. {ly['name']}" for n, ly in enumerate(res["layers"]))]
        lines += [f"  {i} {by_title.get(i, '')[:60]} -> layer {a['layer']}, priority {a['priority']}" for i, a in res["assign"].items()]
        lines += [f"  CANCEL {i} {by_title.get(i, '')[:60]}: {why.get(i) or 'no reason given'}" for i in res["cancel"]]
        lines += [f"  NEW {t['title']} (layer {t['layer']})" for t in res["new"]]
        self.llm.record("reevaluate_result", "strategist", "-", "Decisions applied to the backlog by the re-evaluation", "\n".join(lines))

    def reevaluate(self, requeue: bool = True, state_note: str = "", focus: list[str] | None = None, preview: bool = False, reason: str = "manual") -> dict:
        """Thinks the plan through again for an existing project: new layers, every open task re-ordered into them, gaps in the
        foundation added as tasks, redundant tasks cancelled. `focus` (task ids) limits the review to the graphs those tasks belong to
        (a mini re-plan, e.g. around a blocked task). `preview` only saves the proposal for you to confirm."""
        self.phase("Re-evaluating the plan: what does the core need?", "plan")
        self.backlog.reload()
        items = [i for i in self.backlog.items if i["status"] != "cancelled"]
        if focus:
            scope = {k for c in taskgraph.components(items) if set(c) & set(focus) for k in c}
            items = [i for i in items if i["id"] in scope]
        old = self.blueprint()
        arch = self.mem.dir / "ARCHITECTURE.md"
        sctx = getattr(self.llm, "server_ctx", 0)
        # a small server window: the task list and schema matter most, so notes and the code map shrink first
        share = scaled(self.llm, 1000, 0.0) / 1000
        visible = taskgraph.visible_ids(items)  # finished graphs are only history: they stay out of the prompt
        rows = [{k: v for k, v in {"id": i["id"], "title": clip(i["title"], 80 if sctx else 200), "type": i["type"], "status": i["status"],
                                   "layer": i.get("layer"), "source": i.get("source"), "why": clip(i.get("why", ""), 100)}.items() if v not in (None, [], "")}
                for i in items if i["id"] in visible]
        ideas = self.ideas_path.read_text("utf-8") if self.ideas_path.exists() else ""
        self.state["directives_hash"] = self.directives_hash()
        graph = taskgraph.render(items, 70 if sctx else 100, int(6000 * max(share, 0.4)))
        data = self.llm.json(
            "reevaluate", "strategist", SYSTEM + GRAPH_RULES + " You are re-planning a project that already has code and tasks: keep what works, think briefly (a few sentences), then answer the JSON only, "
            "fix the order. Finished tasks are foundation: put them in the layer they belong to. Add tasks for missing foundation, "
            "cancel tasks that are redundant or contradict the architecture. Never cancel tasks the user wrote.",
            f"Goal:\n{clip(self.goal, int(2500 * max(share, 0.4)))}{self.directive}\nUser directives (IDEAS.md):\n{clip(ideas, int(3000 * share)) or '-'}\n"
            f"Previous layers: {[ly['name'] for ly in old['layers']] if old else 'none (the project was planned feature by feature)'}\n"
            f"Features built: {[f['title'] for f in self.mem.features.values()]}\nArchitecture notes:\n"
            f"{clip(arch.read_text('utf-8') if arch.exists() else '', int(2500 * share))}\nCode map:\n{clip(self.mem.render_codemap(), int(5000 * share))}\n"
            f"Current state of the project:\n{state_note or '-'}\nDependency graphs now:\n{graph}\nTasks: {json.dumps(rows)}\n3 to 5 layers. Schema: " + REEVAL_SCHEMA)
        self._review_reason = reason
        self.store_ideas(data.get("graph_ideas"), self.backlog.items)
        res = blueprint.normalize_reevaluation(data, items)
        self._reeval_note(data, res, by_title={i["id"]: i["title"] for i in items})
        if preview or (self.feat("plan_confirm") and reason != "confirmed"):
            return self.save_proposal(data, res, items, requeue, focus)
        return self.apply_reevaluation(data, items, requeue, focus, reason)

    def apply_reevaluation(self, data: dict, items: list[dict], requeue: bool, focus: list[str] | None, reason: str) -> dict:
        """Applies a (normalized) re-evaluation to the backlog. A result that looks harmful (mass cancellation, graphs torn apart) is rolled back."""
        res = blueprint.normalize_reevaluation(data, items)
        zero = {"assigned": 0, "new": 0, "cancelled": 0, "requeued": 0, "merged": 0, "split": 0}
        if res["keep"] and not (res["new"] or res["cancel"] or res["merge"] or res["split"]):
            self.state["graph_sig"] = taskgraph.signature(self.backlog.items)
            self.metric(kind="reevaluate", kept=True)
            self.log(f"Plan reviewed, the graphs are sound: nothing changed. {res.get('assessment', '')}")
            self.phase("Idle (plan reviewed, no change needed)", None)
            return {**zero, "kept": True}
        if not res["layers"]:
            raise RuntimeError("The model returned no usable layers; nothing was changed.")
        before = copy.deepcopy(self.backlog.items)
        by_id = {i["id"]: i for i in self.backlog.items if i["status"] != "cancelled"}
        scope = {i["id"] for i in items}
        top, stats = len(res["layers"]) - 1, dict(zero)
        for cid in res["cancel"]:
            if by_id[cid]["status"] in ("pending", "blocked"):
                why = next((str(c.get("why", ""))[:200] for c in data.get("cancel", []) if isinstance(c, dict) and c.get("id") == cid), "")
                self.backlog.set(by_id[cid], status="cancelled", reason=why or "cancelled by the re-evaluation",
                                 notes=by_id[cid].get("notes", []) + [f"cancelled by the re-evaluation: {why}"])
                stats["cancelled"] += 1
        key_to_id, redirect = {}, {}
        for t in res["new"]:
            item = self.backlog.add(t["title"], t["description"], t["type"], t["priority"], t["acceptance"], "Re-evaluation", "blueprint",
                                    layer=t["layer"], why=t.get("why"))
            if item:
                key_to_id[t["key"]] = item["id"]
                by_id[item["id"]] = item
                stats["new"] += 1
        for m in res["merge"]:
            new = self.merge_tasks(m, by_id)
            if new:
                redirect.update({i: new["id"] for i in m["ids"]})
                stats["merged"] += len(m["ids"])
        for sp in res["split"]:
            item = by_id.get(sp["id"])
            if item and item["status"] == "pending":
                redirect[item["id"]] = self.split_into_steps(item, sp["steps"], by_id)[-1]
                stats["split"] += 1
        for i in list(by_id.values()):
            if i["status"] == "cancelled" or i.get("source") == "user" or i["id"] in key_to_id.values() or (focus and i["id"] not in scope):
                continue
            a = res["assign"].get(i["id"])
            if a:
                if a["why"]:
                    i["why"] = a["why"]
                if i["status"] != "done" and abs(a["priority"] * 2 - i["priority"]) >= 3:
                    i["priority"] = a["priority"] * 2  # small priority differences are noise, not a reason to reorder
                if i.get("layer") != a["layer"]:
                    stats["assigned"] += 1
                i["layer"] = a["layer"]
            elif i.get("layer") is None and i["status"] != "done":
                i["layer"] = top
            elif i.get("layer") is not None:
                i["layer"] = min(i["layer"], top)  # not mentioned: stays where it is
            if requeue and i["status"] == "blocked":
                i.update(status="pending", attempts=0)
                stats["requeued"] += 1
        wanted = {**{k: v["depends_on"] for k, v in res["assign"].items()}, **{key_to_id[t["key"]]: t["depends_on"] for t in res["new"] if t["key"] in key_to_id}}
        for i in by_id.values():
            if i["status"] == "cancelled":
                continue
            if i["id"] in wanted:
                deps = [redirect.get(key_to_id.get(d, d), key_to_id.get(d, d)) for d in wanted[i["id"]]]
            else:
                deps = [redirect.get(d, d) for d in i.get("depends_on", [])]  # not mentioned: its links stay
            deps = [d for d in dict.fromkeys(deps) if d in by_id and d != i["id"] and by_id[d]["status"] != "cancelled"]
            i.pop("depends_on", None)
            if deps:
                i["depends_on"] = deps
        for k, ds in taskgraph.break_cycles(taskgraph.deps_of(list(by_id.values()))).items():
            by_id[k].pop("depends_on", None)
            if ds:
                by_id[k]["depends_on"] = ds
        taskgraph.raise_layers(list(by_id.values()))
        if bad := taskgraph.damage(before, self.backlog.items):
            self.backlog.items[:] = before
            self.backlog.save()
            self.record_graph(f"rolled back: {bad}", {})
            self.log(f"Re-evaluation rolled back, the result looked harmful ({bad}). The plan stays as it was.")
            self.metric(kind="reevaluate", rolled_back=True)
            self.phase("Idle (re-evaluation rolled back)", None)
            return {**zero, "rolled_back": bad}
        self.state["graph_sig"] = taskgraph.signature(self.backlog.items)
        self.backlog.save()
        if not focus:
            bp = {"architecture": res["architecture"], "assessment": res.get("assessment", ""), "layers": [
                {**ly, "tasks": [{"title": i["title"], "id": i["id"]} for i in by_id.values() if i.get("layer") == n and i["status"] != "cancelled"]}
                for n, ly in enumerate(res["layers"])]}
            blueprint.save(self.blueprint_path, bp)
            write_text(self.repo / "docs" / "BLUEPRINT.md", blueprint.render_md(bp))
            self.git.commit_all("docs: re-evaluated plan and architecture blueprint")
        self.record_graph(reason, stats)
        self.metric(kind="reevaluate", **stats)
        self.log(f"Re-evaluation ({reason}): {len(res['layers'])} layers, {stats['assigned']} tasks re-ordered, {stats['new']} new, "
                 f"{stats['merged']} merged, {stats['split']} split, {stats['cancelled']} cancelled, {stats['requeued']} blocked tasks given a new chance. {res.get('assessment', '')}")
        self.phase("Idle (re-evaluation finished)", None)
        return stats
