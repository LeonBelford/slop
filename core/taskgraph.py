"""The backlog as a dependency graph: connected graphs, cycle repair, trees for display and a compact text form for planning prompts.

Pure functions over backlog items (dicts with id, status, title, depends_on); nothing here touches disk or the model.
"""
from __future__ import annotations

OPEN = ("pending", "in_progress", "blocked")


def live(items: list[dict]) -> list[dict]:
    """Items that still belong to the plan (cancelled ones are history)."""
    return [i for i in items if i.get("status") != "cancelled"]


def deps_of(items: list[dict]) -> dict[str, list[str]]:
    """id -> dependencies that exist among the live items."""
    ids = {i["id"] for i in live(items)}
    return {i["id"]: [d for d in dict.fromkeys(i.get("depends_on") or []) if d in ids and d != i["id"]] for i in live(items)}


def break_cycles(deps: dict[str, list[str]]) -> dict[str, list[str]]:
    """Drops the dependency edges that close a cycle (depth-first), so the graph can always be ordered."""
    out = {k: list(v) for k, v in deps.items()}
    state: dict[str, int] = {}

    def visit(n: str) -> None:
        state[n] = 1
        for d in list(out[n]):
            if state.get(d) == 1:
                out[n].remove(d)
            elif d not in state:
                visit(d)
        state[n] = 2

    for n in list(out):
        if n not in state:
            visit(n)
    return out


def components(items: list[dict]) -> list[list[str]]:
    """Weakly connected graphs, ordered by their first task id; a task without links is a graph of its own."""
    deps = deps_of(items)
    adj: dict[str, set[str]] = {k: set() for k in deps}
    for k, ds in deps.items():
        for d in ds:
            adj[k].add(d)
            adj[d].add(k)
    seen, out = set(), []
    for k in sorted(deps):
        if k in seen:
            continue
        comp, stack = [], [k]
        seen.add(k)
        while stack:
            n = stack.pop()
            comp.append(n)
            for m in adj[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        out.append(sorted(comp))
    return out


def active_graphs(items: list[dict]) -> list[list[str]]:
    """Graphs with at least one task that is not finished yet; finished graphs are only history."""
    by = {i["id"]: i for i in live(items)}
    return [c for c in components(items) if any(by[n]["status"] != "done" for n in c)]


def visible_ids(items: list[dict], show_done: bool = False) -> set[str]:
    """What the UI shows: every live task of an active graph (finished tasks of it included, they are context), and finished
    graphs only on request."""
    by = {i["id"]: i for i in live(items)}
    out: set[str] = set()
    for c in components(items):
        if show_done or any(by[n]["status"] != "done" for n in c):
            out.update(c)
    return out


def trees(items: list[dict], ids: set[str] | None = None) -> list[dict]:
    """The graphs as trees: a task hangs under its first dependency; further dependencies are listed as `also` on the node.
    Returns [{"id", "also": [ids], "children": [...]}] roots first, in id order."""
    deps = break_cycles(deps_of(items))
    keep = set(deps) if ids is None else ids & set(deps)
    parent = {k: next((d for d in ds if d in keep), None) for k, ds in deps.items() if k in keep}
    kids: dict[str, list[str]] = {}
    for k, p in parent.items():
        if p:
            kids.setdefault(p, []).append(k)

    def node(n: str) -> dict:
        return {"id": n, "also": [d for d in deps[n] if d != parent[n] and d in keep], "children": [node(c) for c in sorted(kids.get(n, []))]}

    return [node(k) for k in sorted(parent) if parent[k] is None]


def unlocks(items: list[dict]) -> dict[str, int]:
    """How many open tasks (transitively) wait for each task."""
    deps = deps_of(items)
    open_ids = {i["id"] for i in live(items) if i["status"] in OPEN}
    waiting: dict[str, list[str]] = {}
    for k, ds in deps.items():
        if k in open_ids:
            for d in ds:
                waiting.setdefault(d, []).append(k)
    out = {}
    for k in deps:
        seen, stack = set(), list(waiting.get(k, []))
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack.extend(waiting.get(n, []))
        out[k] = len(seen)
    return out


def levels(items: list[dict]) -> dict[str, int]:
    """Column of each task: one right of its deepest dependency."""
    deps = break_cycles(deps_of(items))
    depth: dict[str, int] = {}

    def d(n: str) -> int:
        if n not in depth:
            depth[n] = 1 + max((d(x) for x in deps[n]), default=-1)
        return depth[n]

    for n in deps:
        d(n)
    return depth


def render(items: list[dict], title_len: int = 70, max_chars: int = 6000) -> str:
    """Compact text for planning prompts: one block per active graph, `ID [status] title <- needs`; finished graphs are only counted."""
    by = {i["id"]: i for i in live(items)}
    deps = deps_of(items)
    lvl = levels(items)
    blocks, done_graphs = [], 0
    for n, c in enumerate(components(items), 1):
        if all(by[x]["status"] == "done" for x in c):
            done_graphs += 1
            continue
        lines = [f"Graph {n} ({len(c)} tasks):"]
        for x in sorted(c, key=lambda k: (lvl[k], k)):
            need = f" <- {', '.join(deps[x])}" if deps[x] else ""
            lines.append(f"  {x} [{by[x]['status']}] {str(by[x]['title'])[:title_len]}{need}")
        blocks.append("\n".join(lines))
    text = "\n".join(blocks) or "(no open graphs)"
    if done_graphs:
        text += f"\n({done_graphs} finished graph(s) are left out)"
    crit = [f"{' -> '.join(g['critical'])}" for g in summaries(items) if len(g["critical"]) > 1 and g["open"]]
    if crit:
        text += "\nCritical paths (longest open chains): " + "; ".join(crit[:4])
    return text if len(text) <= max_chars else text[:max_chars] + "\n[...]"


def raise_layers(items: list[dict]) -> None:
    """A task never sits in a lower layer than what it depends on (layers follow the graph, not the other way round)."""
    deps = break_cycles(deps_of(items))
    by = {i["id"]: i for i in live(items)}
    done: set[str] = set()

    def fix(n: str) -> None:
        if n in done:
            return
        done.add(n)
        for d in deps[n]:
            fix(d)
            if by[d].get("layer") is not None and by[n].get("layer") is not None and by[d]["layer"] > by[n]["layer"]:
                by[n]["layer"] = by[d]["layer"]

    for n in deps:
        fix(n)


def signature(items: list[dict]) -> str:
    """Changes when tasks are added, finished or cancelled: tells whether the plan was made for the current backlog."""
    return ",".join(f"{i['id']}:{i['status'][:2]}" for i in sorted(items, key=lambda i: i["id"]))


DEFAULT_TASK_S = 900  # estimate for a task without one


def duration_ratio(items: list[dict]) -> dict[str, float]:
    """Calibration per task type: real runtime / estimate over finished tasks that have both (1.0 = the estimates are right)."""
    sums: dict[str, list[float]] = {}
    for i in items:
        if i.get("status") == "done" and float(i.get("runtime_s") or 0) > 0 and float(i.get("estimate_s") or 0) > 0:
            sums.setdefault(i.get("type", ""), []).append(float(i["runtime_s"]) / float(i["estimate_s"]))
    return {t: round(min(4.0, max(0.25, sorted(v)[len(v) // 2])), 2) for t, v in sums.items() if len(v) >= 2}


def _cost(i: dict, ratio: dict[str, float]) -> float:
    return float(i.get("estimate_s") or DEFAULT_TASK_S) * ratio.get(i.get("type", ""), 1.0)


def critical_path(items: list[dict], comp: list[str], ratio: dict[str, float] | None = None) -> tuple[list[str], float]:
    """Longest chain of still open tasks inside one graph, weighted by calibrated estimates: what decides when the graph is finished."""
    ratio = ratio or {}
    by = {i["id"]: i for i in live(items)}
    deps = break_cycles(deps_of(items))
    best: dict[str, tuple[float, list[str]]] = {}

    def walk(n: str) -> tuple[float, list[str]]:
        if n not in best:
            sub = max((walk(d) for d in deps[n]), key=lambda t: t[0], default=(0.0, []))
            own = _cost(by[n], ratio) if by[n]["status"] in OPEN else 0.0
            best[n] = (sub[0] + own, sub[1] + ([n] if by[n]["status"] in OPEN else []))
        return best[n]

    top = max((walk(n) for n in comp), key=lambda t: t[0], default=(0.0, []))
    return top[1], top[0]


def summaries(items: list[dict]) -> list[dict]:
    """One entry per connected graph for the UI and the prompts: goal (the task everything leads to), progress, remaining time and critical path."""
    by = {i["id"]: i for i in live(items)}
    ratio = duration_ratio(items)
    deps = deps_of(items)
    lvl = levels(items)
    out = []
    for n, c in enumerate(components(items), 1):
        waiting = {d for k in c for d in deps[k]}
        sinks = [k for k in c if k not in waiting] or c
        goal = by[max(sinks, key=lambda k: (lvl[k], k))]
        crit, secs = critical_path(items, c, ratio)
        opened = [k for k in c if by[k]["status"] in OPEN]
        out.append({"n": n, "ids": c, "goal": str(goal["title"])[:80], "done": sum(by[k]["status"] == "done" for k in c), "total": len(c),
                    "open": len(opened), "remaining_s": round(sum(_cost(by[k], ratio) for k in opened)), "critical": crit, "critical_s": round(secs),
                    "blocked": sum(by[k]["status"] == "blocked" for k in c)})
    return out


def blocked_impact(items: list[dict]) -> dict[str, list[str]]:
    """For every blocked task: the open tasks that (transitively) cannot start before it is resolved."""
    deps = deps_of(items)
    by = {i["id"]: i for i in live(items)}
    waiting: dict[str, list[str]] = {}
    for k, ds in deps.items():
        for d in ds:
            waiting.setdefault(d, []).append(k)
    out = {}
    for b in (k for k in by if by[k]["status"] == "blocked"):
        seen, stack = [], list(waiting.get(b, []))
        while stack:
            n = stack.pop()
            if n not in seen and by[n]["status"] in ("pending", "in_progress"):
                seen.append(n)
                stack.extend(waiting.get(n, []))
        out[b] = sorted(seen)
    return out


def problems(items: list[dict]) -> list[str]:
    """Cheap structural findings (no model call): a review is only needed when something is off."""
    found = []
    by = {i["id"]: i for i in live(items)}
    raw = {i["id"]: [d for d in (i.get("depends_on") or []) if d in by] for i in live(items)}
    if any(raw[k] != break_cycles(deps_of(items))[k] for k in raw):
        found.append("dependency cycle")
    for i in live(items):
        if i["status"] in ("pending", "blocked") and i.get("layer") is None and i.get("source") != "user":
            found.append(f"{i['id']} has no layer")
        if any(d in by and by[d]["status"] == "blocked" for d in raw.get(i["id"], [])) and i["status"] == "pending":
            found.append(f"{i['id']} waits for a blocked task")
    for b, who in blocked_impact(items).items():
        if len(who) >= 2:
            found.append(f"blocked {b} holds back {len(who)} tasks")
    return list(dict.fromkeys(found))[:8]


def damage(before: list[dict], after: list[dict]) -> str:
    """Why a re-evaluation result looks harmful (empty = fine): mass cancellation or a graph torn into pieces."""
    was = {i["id"]: i for i in live(before) if i["status"] in ("pending", "blocked")}
    now = {i["id"]: i for i in after}
    gone = [k for k in was if now.get(k, {}).get("status") == "cancelled" and not now[k].get("merged_into") and not now[k].get("split_into")]
    if len(was) >= 4 and len(gone) > max(2, len(was) * 0.4):
        return f"{len(gone)} of {len(was)} open tasks were cancelled"
    open_before = [c for c in active_graphs(before) if any(k in was for k in c)]
    open_after = active_graphs(after)
    if len(open_after) > max(len(open_before) + 2, len(open_before) * 1.5) and len(open_after) >= 4:
        return f"the graphs fell apart ({len(open_before)} -> {len(open_after)} separate graphs)"
    return ""


def snapshot(items: list[dict], reason: str = "", stats: dict | None = None, ts: str = "") -> dict:
    """Compact picture of the graphs at one moment for the replay timeline."""
    return {"ts": ts, "reason": reason[:160], "stats": stats or {}, "sig": signature(items),
            "nodes": [{"id": i["id"], "t": str(i["title"])[:40], "s": i["status"], "l": i.get("layer"), "d": list(i.get("depends_on") or [])}
                      for i in live(items)]}
