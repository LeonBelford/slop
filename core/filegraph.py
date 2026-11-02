"""File graph of a project: files as nodes, imports as links, and the tasks that touched each file (from the task graph). No model involved."""
from __future__ import annotations

import posixpath
import re

SRC = (".py", ".js", ".mjs", ".ts", ".tsx", ".jsx")
PY = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+))", re.M)
JS = re.compile(r"""(?:from\s+|import\s*\(?\s*|require\(\s*)['"]([^'"]+)['"]""")
RECENT = 4


def _py_targets(text: str) -> list[str]:
    out = []
    for a, b in PY.findall(text):
        out += [a] if a else [x.strip().split(" as ")[0] for x in b.split(",")]
    return [m.replace(".", "/") for m in out if m]


def imports_of(path: str, text: str, files: set[str]) -> set[str]:
    """Files of the project that `text` (the content of `path`) imports."""
    found: set[str] = set()
    if path.endswith(".py"):
        for t in _py_targets(text):
            for cand in (t + ".py", t + "/__init__.py"):
                if cand in files:
                    found.add(cand)
                    break
            else:
                found |= {f for f in files if f.endswith("/" + t + ".py") or f.endswith("/" + t + "/__init__.py")}
    elif path.endswith(SRC):
        base = posixpath.dirname(path)
        for t in JS.findall(text):
            if not t.startswith("."):
                continue
            p = posixpath.normpath(posixpath.join(base, t))
            for cand in [p] + [p + e for e in SRC] + [p + "/index" + e for e in SRC]:
                if cand in files:
                    found.add(cand)
                    break
    found.discard(path)
    return found


def build(files: list[str], read, items: list[dict]) -> dict:
    """`read(path)` returns the text of a file (or ''). Nodes carry size, number of importers and the tasks that touched the file:
    the newest RECENT in full, the rest only as a count."""
    fset = set(files)
    touched: dict[str, list[dict]] = {}
    for it in sorted(items, key=lambda i: i["id"]):
        for f in it.get("files") or []:
            touched.setdefault(f, []).append({"id": it["id"], "title": it["title"], "status": it["status"]})
    edges: list[list[str]] = []
    for f in files:
        if f.endswith(SRC):
            edges += [[f, t] for t in sorted(imports_of(f, read(f), fset))]
    used = {}
    for _, t in edges:
        used[t] = used.get(t, 0) + 1
    nodes = []
    for f in files:
        ts = touched.get(f, [])
        nodes.append({"path": f, "used_by": used.get(f, 0), "tasks": ts[-RECENT:][::-1], "more": max(0, len(ts) - RECENT), "total": len(ts)})
    return {"nodes": nodes, "edges": edges}
