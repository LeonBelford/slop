"""Per-iteration triage: before every pick the open tasks are re-scored for the current state of the project, and the plan is
laid out again (strategist) when the state has changed enough to make the old layout stale."""
from core.cfa_priority import PriorityMixin
from core.cfutil import clip

RELAYOUT_EVERY_MERGES = 3  # re-layout only after this many merges since the last one
MIN_OPEN = 3  # ... and only with at least this many open tasks
MAX_FANOUT_BONUS = 3.0
ATTEMPT_PENALTY = 0.75
MAX_PENALTY = 3.0


def fanout(items: list[dict]) -> dict[str, int]:
    """How many open tasks (transitively) wait for each task."""
    open_ids = {i["id"] for i in items if i["status"] in ("pending", "blocked")}
    waiting: dict[str, list[str]] = {}
    for i in items:
        if i["id"] in open_ids:
            for d in i.get("depends_on", []):
                waiting.setdefault(d, []).append(i["id"])
    out = {}
    for i in items:
        seen, stack = set(), list(waiting.get(i["id"], []))
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack.extend(waiting.get(n, []))
        out[i["id"]] = len(seen)
    return out


def score(items: list[dict]) -> dict[str, float]:
    """Dynamic priority correction per pending task: tasks that unblock others rise, tasks that keep failing sink."""
    fan = fanout(items)
    out = {}
    for i in items:
        if i["status"] != "pending" or i.get("source") == "user":
            continue
        bonus = min(MAX_FANOUT_BONUS, 0.75 * fan.get(i["id"], 0))
        penalty = min(MAX_PENALTY, ATTEMPT_PENALTY * int(i.get("attempts", 0)) + 0.5 * int(i.get("revives", 0)))
        extra = (1.0 if i.get("essential_note") else 0) + (1.5 if i.get("risk") == "high" else 0)  # essential and risky work first
        extra += 0.5 if 0 < float(i.get("estimate_s") or 0) < 900 else -0.5 if float(i.get("estimate_s") or 0) > 3600 else 0  # small before big
        out[i["id"]] = round(bonus + extra - penalty, 2)
    return out


class TriageMixin(PriorityMixin):
    def triage(self) -> None:
        """Called every iteration before the next task is picked. Never raises: a failed triage keeps the current order."""
        if not self.cfg["features"].get("triage", True):
            self.replan_if_stuck()
            return
        try:
            self.backlog.reload()
            changed = self.light_check()
            changed = self._rescore() or changed
            relaid = self.replan_if_stuck() or self._relayout_if_stale()
            if relaid:
                self.backlog.reload()
                changed = self._rescore() or changed
            if changed:
                self.backlog.save()
        except Exception as e:  # noqa: BLE001
            self.log(f"Triage skipped: {clip(str(e), 200)}")

    def _rescore(self) -> bool:
        scores = score(self.backlog.items)
        changed = False
        for i in self.backlog.items:
            new = scores.get(i["id"], 0)
            if (i.get("dyn", 0) or 0) != new:
                changed = True
                if new:
                    i["dyn"] = new
                else:
                    i.pop("dyn", None)
        top = sorted((i for i in self.backlog.pending() if i.get("dyn")), key=lambda i: -abs(i["dyn"]))[:3]
        if changed and top:
            self.log("Triage: priority adjusted for the current state: " + ", ".join(f"{i['id']} {i['dyn']:+g}" for i in top))
        return changed

    def _relayout_if_stale(self) -> bool:
        """Strategist re-layout (layers, order, dependencies, missing/redundant tasks) when enough has been merged since the last one,
        or when open tasks have no layer. Blocked tasks stay with `revive_blocked`."""
        merged = self.state.get("merged", 0)
        since = merged - self.state.get("triage_at", 0)
        open_items = [i for i in self.backlog.items if i["status"] in ("pending", "blocked")]
        unlayered = [i for i in self.backlog.pending() if i.get("layer") is None and i.get("source") != "user"]
        if not ((since >= RELAYOUT_EVERY_MERGES and len(open_items) >= MIN_OPEN) or (len(unlayered) >= 2 and since >= 1)):
            return False
        self.state["triage_at"] = merged  # also after a failure: never retry on every iteration
        self.log(f"Triage: {since} merge(s) since the last layout, laying out the {len(open_items)} open task(s) again")
        try:
            self.reevaluate(requeue=False, state_note=self._state_note())
        except Exception as e:  # noqa: BLE001
            self.log(f"Triage: re-layout failed ({clip(str(e), 200)}), keeping the current order")
            return False
        return True

    def _state_note(self) -> str:
        items = self.backlog.items
        failing = [i["title"] for i in items if i["status"] == "pending" and int(i.get("attempts", 0)) > 0][:5]
        blocked = [i["title"] for i in items if i["status"] == "blocked"][:5]
        done = [i["title"] for i in items if i["status"] == "done"][-5:]
        return (f"{self.state.get('merged', 0)} task(s) merged so far; recently finished: {done or '-'}; "
                f"failing: {failing or '-'}; blocked: {blocked or '-'}. Re-prioritise for what the project needs NOW.")
