"""Light priority check after every merge: the strategist sees only titles, status and results and corrects priorities."""
from __future__ import annotations

from core.cfutil import clip, now

LOG_LIMIT = 40
SCHEMA = '{"changes": [{"id": "T0007", "priority": 1-10, "reason": "one sentence"}]}'


class PriorityMixin:
    def light_check(self) -> bool:
        """Once per merge: are order and priorities still right for the current state? Changes are logged with reasons. True if any."""
        merged = self.state.get("merged", 0)
        if not self.feat("triage_light") or merged <= self.state.get("light_at", 0):
            return False
        pending = [i for i in self.backlog.pending() if i.get("source") != "user"]
        if len(pending) < 2:
            return False
        self.state["light_at"] = merged
        done = [i["title"] for i in self.backlog.items if i["status"] == "done"][-6:]
        rows = "\n".join(f"{i['id']} L{i.get('layer', '-')} p{i['priority']} tries={i['attempts']} risk={i.get('risk', '-')} "
                         f"deps={i.get('depends_on', [])}: {i['title']}" for i in pending[:25])
        try:
            d = self.llm.json("triage_light", "strategist",
                              "You re-check the priorities of open tasks after a merge. Raise tasks that unblock others, carry risk "
                              "(unsure libraries, parsers, performance) or are essential; lower nice-to-haves. Change only what is clearly "
                              "wrong; an empty list is a fine answer.",
                              f"Vision:\n{clip(self.goal, 1200)}\nRecently finished: {done or '-'}\nOpen tasks:\n{rows}\nSchema: {SCHEMA}")
        except Exception as e:  # noqa: BLE001
            self.log(f"Light priority check skipped: {clip(str(e), 150)}")
            return False
        by_id = {i["id"]: i for i in pending}
        log = self.state.setdefault("triage_log", [])
        changed = False
        for c in (d.get("changes") or [])[:10]:
            item = by_id.get(c.get("id")) if isinstance(c, dict) else None
            try:
                new = max(1.0, min(10.0, float(c.get("priority"))))
            except (TypeError, ValueError, AttributeError):
                continue
            if item is None or abs(new - item["priority"]) < 0.5:
                continue
            log.append({"time": now(), "id": item["id"], "title": item["title"][:80], "from": item["priority"], "to": new,
                        "reason": clip(str(c.get("reason", "")), 200), "merge": merged})
            self.backlog.set(item, priority=new)
            changed = True
        del log[:-LOG_LIMIT]
        if changed:
            self.log(f"Light priority check changed {sum(1 for x in log if x['merge'] == merged)} priorities")
        return changed
