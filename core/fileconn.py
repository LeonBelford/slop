"""Smart connections between the files of a project, beyond plain imports. Every link has a kind, so the graph can show WHY two files belong together:

  import   a uses b (static import / include / mod / use)
  test     a test file and the file it tests (by name: foo_test.rs, test_foo.py, foo.spec.ts, tests/foo.rs)
  mention  a names b by path or unique file name (docs, configs, templates, include_str!, script tags ...)
  module   a folder's hub (mod.rs, __init__.py, index.js, lib.rs, main.rs) and what lives next to it
  task     two files that the same tasks touched (built from the task history in `together`)

No model involved: only names, paths and text."""
from __future__ import annotations

import posixpath
import re

HUBS = {"mod", "__init__", "index", "lib", "main"}
TOKEN = re.compile(r"[\w@./-]{0,120}\w\.[A-Za-z]\w{0,5}")  # bounded: a long base64 or minified line must not make the scan quadratic
PRIORITY = {"import": 0, "test": 1, "mention": 2, "module": 3, "task": 4}
MAX_HUB = 15
MAX_TASK_FILES = 8
MAX_PER_FILE = 6
DOCS = (".md", ".txt", ".rst", ".json", ".toml", ".yml", ".yaml", ".lock", ".cfg", ".ini")
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
AFFIX = re.compile(r"^(?:test_|tests_)|(?:_tests?|_spec|\.tests?|\.spec|Tests?|Spec)$")


def _stem(path: str) -> str:
    return posixpath.splitext(posixpath.basename(path))[0]


def is_test(path: str) -> bool:
    parts = path.split("/")
    return any(p in TEST_DIRS for p in parts[:-1]) or AFFIX.search(_stem(path)) is not None


def _keys(path: str) -> list[str]:
    """Names a test may use for this file: `observer.rs` in file_system/ is `observer` or `file_system_observer`; a hub is its folder."""
    parts = path.split("/")
    stem = _stem(path)
    folder = parts[-2] if len(parts) > 1 else ""
    if stem in HUBS:
        return [folder] if folder else []
    return ([f"{folder}_{stem}"] if folder else []) + [stem]


def tests_of(files: list[str]) -> list[list]:
    subjects: dict[str, set[str]] = {}
    for f in files:
        if not is_test(f) and not f.endswith(DOCS):
            for k in _keys(f):
                subjects.setdefault(k.lower(), set()).add(f)
    out = []
    for f in files:
        if not is_test(f) or _stem(f) in HUBS:
            continue
        key = AFFIX.sub("", _stem(f)).lower()
        for cand in dict.fromkeys([key, key.rsplit("_", 1)[-1]] if "_" in key else [key]):
            hit = subjects.get(cand, set()) - {f}
            if len(hit) == 1:
                out.append([f, next(iter(hit)), "test"])
                break
    return out


def modules_of(files: list[str]) -> list[list]:
    """Hub file -> its siblings and the hubs of its sub-folders."""
    by_dir: dict[str, list[str]] = {}
    for f in files:
        by_dir.setdefault(posixpath.dirname(f), []).append(f)
    out = []
    for d, members in by_dir.items():
        hubs = [f for f in members if _stem(f) in HUBS and not is_test(f)]
        if not hubs:
            continue
        hub = sorted(hubs, key=lambda f: (_stem(f) not in ("mod", "__init__", "index"), f))[0]
        rest = sorted(f for f in members if f != hub and not is_test(f))[:MAX_HUB]
        out += [[hub, f, "module"] for f in rest]
        for sub, ms in by_dir.items():
            if posixpath.dirname(sub) == d and sub != d:
                child = [f for f in ms if _stem(f) in ("mod", "__init__", "index")]
                if child:
                    out.append([hub, child[0], "module"])
    return out


def mentions_of(files: list[str], read) -> list[list]:
    """Files whose text names another file of the project by path or by a unique file name."""
    tails: dict[str, set[str]] = {}
    for f in files:
        parts = f.split("/")
        for i in range(len(parts)):
            tails.setdefault("/".join(parts[i:]), set()).add(f)
    out = []
    for f in files:
        seen: set[str] = set()
        for tok in TOKEN.findall(read(f)):
            t = re.sub(r"^(?:\.{1,2}/|/)+", "", tok)
            if "/" not in t and len(t) < 5:
                continue
            hit = tails.get(t)
            if hit and len(hit) == 1:
                target = next(iter(hit))
                if target != f and target not in seen:
                    seen.add(target)
                    out.append([f, target, "mention"])
    return out


def together(items: list[dict], files: set[str]) -> list[list]:
    """Pairs of files that tasks touched together; the weight is the number of shared tasks. The strongest few per file are kept."""
    weight: dict[tuple[str, str], int] = {}
    for it in items:
        fs = sorted({f for f in (it.get("files") or []) if f in files})
        if 2 <= len(fs) <= MAX_TASK_FILES:
            for n, a in enumerate(fs):
                for b in fs[n + 1:]:
                    weight[(a, b)] = weight.get((a, b), 0) + 1
    used: dict[str, int] = {}
    out = []
    for (a, b), w in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0])):
        if used.get(a, 0) < MAX_PER_FILE and used.get(b, 0) < MAX_PER_FILE:
            used[a] = used.get(a, 0) + 1
            used[b] = used.get(b, 0) + 1
            out.append([a, b, "task", w])
    return out


def merge(*groups: list[list]) -> list[list]:
    """One link per pair of files: the strongest kind wins (an import beats a mention); only imports keep their direction."""
    imports = {(e[0], e[1]) for g in groups for e in g if e[2] == "import"}
    best: dict[tuple[str, str], list] = {}
    for g in groups:
        for e in g:
            if e[2] == "import":
                best[(e[0], e[1])] = e
            elif (e[0], e[1]) not in imports and (e[1], e[0]) not in imports:
                key = tuple(sorted(e[:2]))
                if key not in best or PRIORITY[e[2]] < PRIORITY[best[key][2]]:
                    best[key] = e
    return sorted(best.values(), key=lambda e: (e[0], e[1]))
