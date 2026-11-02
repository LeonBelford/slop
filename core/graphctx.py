"""Files on the graph: every task node carries the files it touched (done) or will touch (open). From that follows a small, targeted context for
a model (only what the node builds on), links between tasks that worked on the same files, and a layer for new nodes. Pure functions."""
from __future__ import annotations

from core import taskgraph

PACK_CHARS = 1600  # budget of the context text for one task
MAX_FILE_LINKS = 2  # file-based links added per task


def files_of(item: dict) -> list[str]:
    return [f for f in (item.get("files") or []) if isinstance(f, str)]


def upstream(items: list[dict], task_id: str, depth: int = 2) -> list[tuple[dict, int]]:
    """Tasks this one builds on, nearest first: [(item, distance)], at most `depth` steps away."""
    by = {i["id"]: i for i in taskgraph.live(items)}
    deps = taskgraph.deps_of(items)
    out, seen, frontier = [], {task_id}, [task_id]
    for dist in range(1, depth + 1):
        nxt = []
        for n in frontier:
            for d in deps.get(n, []):
                if d not in seen:
                    seen.add(d)
                    nxt.append(d)
                    out.append((by[d], dist))
        frontier = nxt
    return out


def context_pack(items: list[dict], item: dict, budget: int = PACK_CHARS) -> dict:
    """Compact context for one task: what it builds on (with the files and the result of those tasks), who builds on it, and which other
    open tasks touch the same files. Returns {"text", "files"}; `files` are the paths worth opening first, nearest task first."""
    live = taskgraph.live(items)
    deps = taskgraph.deps_of(items)
    mine = set(files_of(item))
    files: list[str] = []
    lines = []
    for up, _ in upstream(items, item["id"]):
        fs = files_of(up)
        files += [f for f in fs if f not in files]
        state = "done" if up["status"] == "done" else up["status"]
        res = f" - {str(up['result'])[:110]}" if up.get("result") else ""
        lines.append(f"- {up['id']} {str(up['title'])[:70]} [{state}]" + (f" files: {', '.join(fs[:4])}" if fs else "") + res)
    text = ("Builds on (extend it, do not redo it):\n" + "\n".join(lines)) if lines else ""
    later = [i for i in live if item["id"] in deps.get(i["id"], []) and i["status"] in ("pending", "blocked")]
    if later:
        text += "\nTasks that build on this one (keep what they need stable): " + "; ".join(f"{i['id']} {str(i['title'])[:50]}" for i in later[:5])
    shared = [i for i in live if i["id"] != item["id"] and i["status"] in ("pending", "in_progress") and mine & set(files_of(i))]
    if shared:
        text += "\nOther open tasks touching the same files: " + "; ".join(f"{i['id']} ({', '.join(sorted(mine & set(files_of(i)))[:2])})" for i in shared[:4])
    return {"text": text[:budget], "files": files}


def prefer_files(upstream_files: list[str], candidates: list[str], exists, limit: int = 4) -> list[str]:
    """The files of the tasks a node builds on come first (up to `limit`), then the other candidates."""
    first = [f for f in upstream_files if exists(f)][:limit]
    return first + [c for c in candidates if c not in first]


def file_links(items: list[dict]) -> dict[str, list[str]]:
    """Links for tasks that worked on (or will work on) the same files as a finished task, so the graph shows where code came from.
    {task id: [finished task ids to add]}; never creates a cycle."""
    live = taskgraph.live(items)
    done = [i for i in live if i["status"] == "done" and files_of(i)]
    deps = {k: set(v) for k, v in taskgraph.deps_of(items).items()}

    def reach(a: str, b: str, seen: set[str] | None = None) -> bool:
        seen = seen if seen is not None else set()
        if a == b:
            return True
        seen.add(a)
        return any(reach(d, b, seen) for d in deps.get(a, ()) if d not in seen)

    out: dict[str, list[str]] = {}
    for i in live:
        fs = set(files_of(i))
        if not fs or i["status"] == "cancelled":
            continue
        scored = sorted(((len(fs & set(files_of(d))), d["id"]) for d in done if d["id"] < i["id"] or i["status"] != "done"), reverse=True)
        picks = [d for n, d in scored if n and d != i["id"] and not reach(i["id"], d) and not reach(d, i["id"])][:MAX_FILE_LINKS]
        for d in picks:
            deps[i["id"]].add(d)
        if picks:
            out[i["id"]] = picks
    return out


def place(items: list[dict]) -> dict[str, int]:
    """A layer for new open tasks that have none: the highest layer among their dependencies, else among the finished tasks sharing their files."""
    by = {i["id"]: i for i in taskgraph.live(items)}
    deps = taskgraph.deps_of(items)
    owners: dict[str, list[int]] = {}
    for i in by.values():
        if i.get("layer") is not None:
            for f in files_of(i):
                owners.setdefault(f, []).append(i["layer"])
    out = {}
    for i in by.values():
        if i.get("layer") is not None or i["status"] not in ("pending", "blocked") or i.get("source") == "user":
            continue
        cand = [by[d]["layer"] for d in deps[i["id"]] if by[d].get("layer") is not None] or [x for f in files_of(i) for x in owners.get(f, [])]
        if cand:
            out[i["id"]] = max(cand)
    return out
