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
