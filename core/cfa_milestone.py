"""Milestone review: when a layer is finished, check that the product still matches the vision and steer the next layers."""
from __future__ import annotations

import json

from core.cfutil import clip, now

REVIEW_SCHEMA = ('{"alignment": 1-10, "drift": "how the product differs from the vision, or empty", "missing": [{"title": "", '
                 '"description": "", "acceptance": ["testable"]}], "obsolete": ["ids of open tasks that no longer serve the vision"], '
                 '"replan": false, "summary": "2 sentences"}')


class MilestoneMixin:
    def milestone_review(self, layer: int, name: str) -> dict | None:
        """Strategist compares vision, built features and open tasks after a layer. Adds up to 3 missing tasks to the next layer,
        cancels up to 3 obsolete ones (never the user's), and asks for a full re-plan if the product drifted far."""
        bp = self.blueprint()
        if not bp:
            return None
        open_ = [i for i in self.backlog.items if i["status"] == "pending"]
        try:
            d = self.llm.json(
                "milestone", "strategist",
                "You review a product at a milestone: does what exists still serve the vision, and what is missing to make the next "
                "layer valuable? Be concrete and brief. Only propose tasks that are really missing.",
                f"Vision:\n{clip(self.goal, 2500)}\nLayer finished: {layer} '{name}' (goal: {bp['layers'][layer].get('goal', '')})\n"
                f"Next layers: {[ly['name'] for ly in bp['layers'][layer + 1:]]}\n"
                f"Built features: {[f['title'] for f in self.mem.features.values()][-40:]}\n"
                f"Open tasks: {json.dumps([{'id': i['id'], 'title': i['title'], 'layer': i.get('layer'), 'source': i.get('source')} for i in open_][:40])}\n"
                f"Schema: {REVIEW_SCHEMA}")
        except Exception as e:  # noqa: BLE001
            self.log(f"Milestone review skipped: {clip(str(e), 160)}")
            return None
        top = len(bp["layers"]) - 1
        added, cancelled = [], []
        for t in [x for x in d.get("missing", []) if isinstance(x, dict) and x.get("title")][:3]:
            it = self.backlog.add(str(t["title"])[:120], str(t.get("description", ""))[:800], "feature", 6, [str(a)[:200] for a in (t.get("acceptance") or [])][:6],
                                  f"Milestone review after layer {layer}", "milestone", layer=min(layer + 1, top))
            if it:
                added.append(it["id"])
        protected = {"user", "ideas"}
        for cid in [c for c in d.get("obsolete", []) if isinstance(c, str)][:3]:
            it = next((i for i in open_ if i["id"] == cid and i.get("source") not in protected), None)
            if it:
                self.backlog.set(it, status="cancelled", notes=it.get("notes", []) + [f"cancelled by the milestone review after layer {layer}"])
                cancelled.append(cid)
        score = d.get("alignment") if isinstance(d.get("alignment"), (int, float)) else 7
        if score <= 4 or d.get("replan"):
            self.state["milestone_replan"] = True
        rec = {"time": now(), "layer": layer, "name": name, "alignment": score, "drift": clip(str(d.get("drift", "")), 400),
               "added": added, "cancelled": cancelled, "summary": clip(str(d.get("summary", "")), 400)}
        with open(self.dir / "milestones.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.metric(kind="milestone", layer=layer, alignment=score, added=len(added), cancelled=len(cancelled))
        self.log(f"Milestone review after layer {layer} '{name}': alignment {score}/10, {len(added)} task(s) added, {len(cancelled)} cancelled. {rec['summary']}"
                 + (" Re-planning is scheduled." if self.state.get("milestone_replan") else ""))
        return rec
