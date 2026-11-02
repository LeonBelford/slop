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


def context_pack(items: list[dict], item: dict, budget: int = PACK_CHARS, codemap: dict | None = None) -> dict:
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
    past = [i for i in live if i["id"] != item["id"] and i["status"] == "done" and mine & set(files_of(i)) and i["id"] not in {u["id"] for u, _ in upstream(items, item["id"])}]
    if past:
        text += "\nEarlier tasks that touched the same files (keep their behaviour): " + "; ".join(f"{i['id']} {str(i['title'])[:50]}" for i in sorted(past, key=lambda x: x["id"])[-3:])
    if codemap and mine:
        out = sorted({d for f in mine for d in codemap.get(f, {}).get("imports", [])} - mine)[:4]
        inn = sorted({d for f in mine for d in codemap.get(f, {}).get("imported_by", [])} - mine)[:4]
        if out or inn:
            text += "\nFile neighbourhood (static imports): " + (f"your files use {', '.join(out)}" if out else "") + ("; " if out and inn else "") + (f"used by {', '.join(inn)}" if inn else "")
    return {"text": text[:budget], "files": files}


def hotspot_score(path: str, loc: int, churn: int, items: list[dict], codemap: dict) -> float:
    """How urgently a file needs a refactor: big, often changed, touched by many tasks and imported by many files."""
    touched = sum(1 for i in items if path in files_of(i))
    return loc * max(1, churn) * (1 + touched) * (1 + len(codemap.get(path, {}).get("imported_by", [])))


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


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or "_test." in name or ".test." in name or ".spec." in name


def _stem(path: str) -> str:
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.removeprefix("test_").removesuffix("_test").removesuffix(".test").removesuffix(".spec")


def _group_key(path: str) -> str:
    """Files that belong together share a key: the package directory (tests folded into their module's directory) or the module stem."""
    parts = [p for p in path.split("/")[:-1] if p not in ("tests", "test", "__tests__")]
    if parts:
        return "/".join(parts)
    stem = _stem(path)
    return stem.split("_")[0] or stem


def group_files(files: list[str], max_groups: int = 3, min_files: int = 4) -> list[list[str]]:
    """Splits the files of a big task into 2-3 balanced groups along the module structure. [] = not worth splitting (few files or one block)."""
    files = list(dict.fromkeys(f for f in files if isinstance(f, str)))
    if len(files) < min_files:
        return []
    buckets: dict[str, list[str]] = {}
    for f in sorted(files):
        buckets.setdefault(_group_key(f), []).append(f)
    stems = {}
    for k, fs in buckets.items():
        for f in fs:
            stems.setdefault(_stem(f), k)
    for k in list(buckets):  # a test follows the module it tests
        fs = buckets[k]
        if len(fs) == 1 and _is_test(fs[0]) and stems.get(_stem(fs[0])) not in (None, k):
            buckets[stems[_stem(fs[0])]].append(buckets.pop(k)[0])
    groups = sorted(buckets.values(), key=len, reverse=True)
    if len(groups) < 2:
        return []
    while len(groups) > max_groups:  # merge the two smallest
        groups.sort(key=len)
        a, b = groups.pop(0), groups.pop(0)
        groups.append(a + b)
    return sorted(groups, key=lambda g: g[0])
