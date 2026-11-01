"""Strategic planning: turns the product brief into an architecture blueprint (layers, core first) and seeds the backlog from it."""
from __future__ import annotations

import json

from core import blueprint, uidetect
from core.cfutil import clip, write_text

SYSTEM = ("You are a software architect planning a build strategy, not a feature list. Find the smallest kernel that makes every later "
          "feature cheap and safe (domain model, storage, core logic, public interfaces, error handling, configuration, test harness) "
          "and put it in layer 0. Every next layer builds only on the public interfaces of the layers below and adds user-visible "
          "capability. Keep interfaces stable so lower layers are never reworked. The last layer is polish and hardening. Tasks are "
          "small enough for one iteration. Realistic and focused, no monetization.")

SCHEMA = ('{"architecture": {"principles": ["..."], "modules": [{"name": "", "responsibility": ""}], '
          '"contracts": ["stable interface, e.g. function or endpoint signature and its meaning"], "decisions": ["decision and why"]}, '
          '"layers": [{"name": "", "goal": "what exists and works after this layer", "tasks": [{"key": "short-unique-id", "title": "", '
          '"description": "", "acceptance": ["testable"], "depends_on": ["keys of earlier tasks"], "type": "feature|refactor|test", '
          '"value": 1-10, "effort": 1-10}]}]}')


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
        return blueprint.context_for(self.blueprint(), item)

    def make_blueprint(self, idea: str, brief: dict) -> bool:
        """Plans the layers and seeds the backlog from them. False (nothing changed) if the model gave no usable plan."""
        self.phase("Designing the architecture blueprint (core first)", "plan")
        try:
            data = self.llm.json(
                "blueprint", "planner", SYSTEM,
                f"Idea: {idea}\nStack: {self.cfg['stack']}\nVision: {brief.get('vision', '')}\nProblems: {brief.get('problems', [])}\n"
                f"Features wanted (each must end up in some layer): {json.dumps(brief.get('mvp_features', []))}\n"
                f"Performance goals: {brief.get('performance_goals', [])}\nNon-goals: {brief.get('non_goals', [])}\n"
                f"3 to 5 layers, at most {blueprint.MAX_TASKS} tasks each. Schema: {SCHEMA}")
            bp = blueprint.normalize(data, brief.get("mvp_features", []))
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
                                        depends_on=[ids[d] for d in t["depends_on"] if d in ids])
                ids[t["key"]] = item["id"]
                t["id"] = item["id"]
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
        self.log(f"Layer {layer} '{name}' complete: everything above builds on it from here.")
        self.metric(kind="layer_done", layer=layer, name=name)
        try:
            self.git.run("tag", "-f", f"layer-{layer}", "main")
            self.mem.refresh_architecture()
        except Exception as e:  # noqa: BLE001  (bookkeeping only)
            self.log(f"Layer bookkeeping skipped: {clip(str(e), 200)}")
