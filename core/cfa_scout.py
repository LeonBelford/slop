"""Scouts: read-only look-ahead for the next tasks in parallel (no model call, no GPU): likely files and their symbols per task."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from core.cfutil import words

SCOUT_AHEAD = 3  # tasks looked at per call
SYMBOL_RE = re.compile(r"^\s*(?:async\s+def|def|class|function|export\s+(?:default\s+)?(?:async\s+)?function)\s+(\w+)", re.M)


def scout_task(repo, codemap: dict, item: dict) -> str:
    """Files whose path or purpose matches the task words, with their top-level symbols."""
    kw = words(item["title"] + " " + item.get("description", ""))
    scored = sorted(((len(kw & words(r + " " + e.get("purpose", ""))), r) for r, e in codemap.items()), reverse=True)
    parts = []
    for score, rel in scored[:3]:
        if score <= 0:
            break
        try:
            text = (repo / rel).read_text("utf-8", "replace")[:20000]
        except OSError:
            continue
        names = list(dict.fromkeys(SYMBOL_RE.findall(text)))[:8]
        parts.append(f"{rel}" + (f" ({', '.join(names)})" if names else ""))
    return "; ".join(parts)


class ScoutMixin:
    def scout_ahead(self, current: dict | None = None) -> int:
        """Scouts the next ready tasks (not the current one) in parallel threads and stores the hint on the task. Never raises."""
        if not self.feat("scout") or not getattr(self.mem, "codemap", None):
            return 0
        try:
            ready = [i for i in self.backlog.ready(self.backlog.pending()) if not current or i["id"] != current["id"]]
            todo = [i for i in ready if "scout" not in i][:SCOUT_AHEAD]
            if not todo:
                return 0
            with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                hints = list(pool.map(lambda i: scout_task(self.repo, self.mem.codemap, i), todo))
            for i, h in zip(todo, hints, strict=True):
                i["scout"] = h
                if not i.get("files"):
                    i["files"] = [p.split(" (")[0] for p in h.split("; ") if p][:4]
            self.backlog.save()
            return len(todo)
        except Exception as e:  # noqa: BLE001  (look-ahead only)
            self.log(f"Scouting skipped: {e}")
            return 0
