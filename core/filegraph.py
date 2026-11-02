"""File graph of a project: files as nodes, imports as links, and the tasks that touched each file (from the task graph). No model involved."""
from __future__ import annotations

import posixpath
import re

SRC = (".py", ".js", ".mjs", ".ts", ".tsx", ".jsx")
WEB = (".html", ".htm", ".css", ".vue", ".svelte")
REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+)|@import\s+(?:url\()?["']([^"']+)|url\(\s*["']?([^"')#?]+)""")
PY = re.compile(r"^\s*(?:from\s+(\.*)([\w.]*)\s+import\s+([^#\n]+)|import\s+([\w., ]+))", re.M)
JS = re.compile(r"""(?:from\s+|import\s*\(?\s*|require\(\s*)['"]([^'"]+)['"]""")
RECENT = 4


def _py_targets(path: str, text: str) -> list[list[str]]:
    """Per import statement the module paths it can mean, most specific first (`from a import b` is a/b.py or a.py)."""
    out = []
    for dots, mod, names, plain in PY.findall(text):
        if plain:
            out += [[x.strip().split(" as ")[0].replace(".", "/")] for x in plain.split(",") if x.strip()]
            continue
        base = posixpath.dirname(path) if dots else ""
        for _ in range(max(0, len(dots) - 1)):
            base = posixpath.dirname(base)
        mod = posixpath.join(base, mod.replace(".", "/")) if mod else base
        subs = [x.strip().split(" as ")[0].strip("()") for x in names.split(",")]
        out.append([posixpath.join(mod, n) for n in subs if n.isidentifier()] + [mod])
    return out


def imports_of(path: str, text: str, files: set[str]) -> set[str]:
    """Files of the project that `text` (the content of `path`) imports."""
    found: set[str] = set()
    if path.endswith(".py"):
        for cands in _py_targets(path, text):
            here = posixpath.dirname(path)
            hits = {h for t in cands + ([posixpath.join(here, c) for c in cands] if here else []) for h in [c for c in (t + ".py", t + "/__init__.py") if c in files][:1]}
            if len(hits) > 1:  # `from pkg import mod`: the module is the link, the package file is just its container
                hits = {h for h in hits if not h.endswith("/__init__.py")} or hits
            if not hits and "/" in cands[-1]:
                loose = [f for f in files if f.endswith("/" + cands[-1] + ".py")]  # source roots like src/pkg/x.py imported as pkg.x
                hits = set(loose) if len(loose) == 1 else set()
            found |= hits
    elif path.endswith(SRC + WEB):
        base = posixpath.dirname(path)
        refs = JS.findall(text) if path.endswith(SRC) else [x for m in REF.findall(text) for x in m if x]
        refs += [x for x in JS.findall(text) if x not in refs] if path.endswith(WEB) else []
        for t in refs:
            if t.startswith(("http:", "https:", "data:", "//")):
                continue
            if t.startswith("."):
                p = posixpath.normpath(posixpath.join(base, t))
            elif t.startswith(("/", "@/", "~/")):
                p = t.lstrip("/@~")
            elif path.endswith(WEB):
                p = posixpath.normpath(posixpath.join(base, t))
            else:
                continue
            cands = [p] + [p + e for e in SRC] + [p + "/index" + e for e in SRC]
            hit = next((c for c in cands if c in files), None)
            if not hit:  # served from a folder the path does not show (/static/app.js is web/app.js)
                loose = [f for f in files if f.endswith("/" + p.split("/", 1)[-1])] if "/" in p else []
                hit = loose[0] if len(loose) == 1 else None
            if hit:
                found.add(hit)
    found.discard(path)
    return found


def edges_of(files: list[str], read) -> list[list[str]]:
    fset = set(files)
    return [[f, t] for f in files if f.endswith(SRC + WEB) for t in sorted(imports_of(f, read(f), fset))]


def build(files: list[str], read, items: list[dict], edges: list[list[str]] | None = None) -> dict:
    """`read(path)` returns the text of a file (or ''). Nodes carry size, number of importers and the tasks that touched the file:
    the newest RECENT in full, the rest only as a count. Pass `edges` (from `edges_of`) to reuse an earlier scan of unchanged files."""
    touched: dict[str, list[dict]] = {}
    for it in sorted(items, key=lambda i: i["id"]):
        for f in it.get("files") or []:
            touched.setdefault(f, []).append({"id": it["id"], "title": it["title"], "status": it["status"]})
    edges = edges_of(files, read) if edges is None else edges
    used: dict[str, int] = {}
    for _, t in edges:
        used[t] = used.get(t, 0) + 1
    nodes = []
    for f in files:
        ts = touched.get(f, [])
        nodes.append({"path": f, "used_by": used.get(f, 0), "tasks": ts[-RECENT:][::-1], "more": max(0, len(ts) - RECENT), "total": len(ts)})
    return {"nodes": nodes, "edges": edges}
