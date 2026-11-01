"""Keeps essential tasks from dying: a task that keeps failing is split into smaller slices, and a blocked task gets new chances later."""
from __future__ import annotations

from core.cfutil import clip

MAX_SPLIT_DEPTH = 2
MAX_REVIVES = 2
REVIVE_AFTER_MERGES = 2
SLICE_SCHEMA = ('{"cause": "why the task keeps failing, one sentence", "subtasks": [{"title": "", "description": "exact files and behaviour", '
                '"acceptance": ["testable"], "type": "feature|refactor|test"}]}')


class SplitMixin:
    def split_task(self, item: dict, previous: list[str]) -> bool:
        """Replaces a failed task by 2-4 smaller, ordered slices (the first one is the smallest working core). True if it was split."""
        depth = int(item.get("split_depth", 0))
        if depth >= MAX_SPLIT_DEPTH:
            return False
        self.phase("The task keeps failing: splitting it into smaller slices", "plan", item["title"])
        try:
            d = self.llm.json(
                "split_task", "strategist",
                "You rescue a task a small local coding model could not finish. Cut it into 2 to 4 slices; each touches at most 3 files, "
                "is testable on its own and keeps the build green. Slice 1 is the smallest working core (pure logic with tests, no UI, "
                "no extras); later slices build on it. Together they must fulfil the original acceptance criteria. Do not repeat the "
                "approach that failed.",
                f"Task {item['id']} ({item['type']}): {item['title']}\n{item['description']}\nAcceptance: {item.get('acceptance')}\n"
                f"Goal:\n{clip(self.goal, 1500)}\nCode map:\n{clip(self.mem.render_codemap(), 3500)}\n"
                f"Failed attempts:\n{chr(10).join(previous[-6:])}\nSchema: {SLICE_SCHEMA}")
            subs = [s for s in d.get("subtasks", []) if isinstance(s, dict) and str(s.get("title", "")).strip()][:4]
        except Exception as e:  # noqa: BLE001
            self.log(f"Splitting failed: {e}")
            return False
        if len(subs) < 2:
            return False
        ids, prev_ids = [], list(item.get("depends_on", []))
        for n, s in enumerate(subs):
            kind = s.get("type") if s.get("type") in ("feature", "refactor", "test") else item["type"]
            new = self.backlog.add(str(s["title"])[:120], str(s.get("description", ""))[:800], kind, item["priority"] + 1 - 0.1 * n,
                                   [str(a)[:200] for a in (s.get("acceptance") or [])][:6] or item.get("acceptance", []),
                                   f"Slice {n + 1}/{len(subs)} of {item['id']}: {d.get('cause', '')}", item.get("source", "ideation"),
                                   force=True, layer=item.get("layer"), depends_on=prev_ids)
            new["split_depth"] = depth + 1
            ids.append(new["id"])
            prev_ids = [new["id"]]
        for other in self.backlog.items:  # whoever waited for the task now waits for its last slice
            if item["id"] in other.get("depends_on", []) and other["id"] not in ids:
                other["depends_on"] = [ids[-1] if x == item["id"] else x for x in other["depends_on"]]
        self.backlog.set(item, status="cancelled", split_into=ids,
                         notes=item.get("notes", []) + [f"split into {', '.join(ids)} after repeated failures"])
        self.metric(kind="split", item=item["id"], into=ids)
        self.log(f"Split {item['id']} into {len(ids)} slices: {', '.join(ids)}")
        return True

    def revive_blocked(self) -> int:
        """Blocked tasks get a fresh start once other work has been merged meanwhile (the code base changed); at most twice each."""
        n = 0
        for it in self.backlog.items:
            if it["status"] == "blocked" and int(it.get("revives", 0)) < MAX_REVIVES \
                    and self.state["merged"] - int(it.get("blocked_at_merge", 0)) >= REVIVE_AFTER_MERGES:
                self.backlog.set(it, status="pending", attempts=0, revives=int(it.get("revives", 0)) + 1,
                                 notes=it.get("notes", []) + ["revived: the code base changed since it was blocked"])
                self.log(f"{it['id']} {it['title']} gets another chance (revive {it['revives']}/{MAX_REVIVES})")
                n += 1
        return n

    def block_task(self, item: dict) -> None:
        self.backlog.set(item, status="blocked", blocked_at_merge=self.state["merged"])
