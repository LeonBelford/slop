"""Import links for languages beyond Python/JS: C-family, Go, Rust, JVM, C#, PHP, Ruby, Dart, Lua. Regex only, no model."""
from __future__ import annotations

import posixpath
import re

EXTS = {
    "c": (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm"),
    "go": (".go",), "rs": (".rs",), "jvm": (".java", ".kt", ".kts", ".scala", ".groovy"),
    "cs": (".cs",), "php": (".php",), "rb": (".rb",), "dart": (".dart",), "lua": (".lua",),
}
ALL = tuple(e for v in EXTS.values() for e in v)
INC = re.compile(r'^\s*#\s*(?:include|import)\s+"([^"]+)"', re.M)
GO_IMP = re.compile(r'^\s*import\s*(?:\(([^)]*)\)|(?:[\w.]+\s+)?("[^"]+"))', re.M)
GO_STD = set("archive bufio bytes compress container context crypto database debug embed encoding errors expvar flag fmt go hash html image index io log math mime net os path plugin reflect regexp runtime sort strconv strings sync syscall testing text time unicode unsafe".split())
GO_STR = re.compile(r'"([^"]+)"')
RS_MOD = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.M)
RS_USE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([\w:]+?)(?:::)?\s*(?:\{([^}]*)\})?\s*(?:;|as\b|$)", re.M)
RS_SKIP = {"std", "alloc", "crate", "self", "super"}
JVM = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;?\s*$", re.M)
CS = re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?([\w.]+)\s*;", re.M)
PHP_USE = re.compile(r"^\s*use\s+(?:function\s+|const\s+)?([\w\\]+)", re.M)
PHP_REQ = re.compile(r"""(?:require|include)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]([^'"]+)""")
RB_REL = re.compile(r"""require_relative\s*\(?\s*['"]([^'"]+)""")
RB_REQ = re.compile(r"""^\s*require\s*\(?\s*['"]([^'"]+)""", re.M)
DART = re.compile(r"""^\s*(?:import|export|part)\s+['"]([^'"]+)['"]""", re.M)
LUA = re.compile(r"""require\s*\(?\s*['"]([^'"]+)['"]""")


def _unique(rel: str, files: set[str], here: str = "") -> str | None:
    """Project file for `rel`: exact path, next to the importer, or the only file ending with it."""
    rel = posixpath.normpath(rel)
    for c in (posixpath.normpath(posixpath.join(here, rel)), rel):
        if c in files:
            return c
    tail = [f for f in files if f.endswith("/" + rel)]
    return tail[0] if len(tail) == 1 else None


def _first(stem: str, exts: tuple[str, ...], files: set[str], here: str = "", extra: tuple[str, ...] = ()) -> str | None:
    for c in [stem + e for e in exts] + [stem + s for s in extra]:
        hit = _unique(c, files, here)
        if hit:
            return hit
    return None


def _dir_files(rel: str, exts: tuple[str, ...], files: set[str], cap: int = 6, floor: int = 1) -> set[str]:
    """Files of the directory an import path names; the longest suffix of the path that is a project directory wins."""
    parts = [p for p in re.split(r"[/.\\]", rel) if p]
    for i in range(max(1, len(parts) - floor + 1)):
        d = "/".join(parts[i:])
        hit = sorted(f for f in files if f.endswith(exts) and not f.endswith("_test.go") and (posixpath.dirname(f) == d or posixpath.dirname(f).endswith("/" + d)))
        if hit:
            return set(hit[:cap])
    return set()


def _walk(segs: list[str], exts: tuple[str, ...], files: set[str], extra: tuple[str, ...] = ()) -> str | None:
    """a.b.C.member or crate::a::b::Item: drop trailing parts until a project file matches."""
    for n in range(len(segs), 0, -1):
        h = _first("/".join(segs[:n]), exts, files, extra=extra)
        if h:
            return h
    return None


def imports_of(path: str, text: str, files: set[str]) -> set[str]:
    here = posixpath.dirname(path)
    ext = posixpath.splitext(path)[1]
    hits: list[str | None] = []
    found: set[str] = set()
    if ext in EXTS["c"]:
        hits = [_unique(t, files, here) for t in INC.findall(text)]
    elif ext == ".go":
        for block, one in GO_IMP.findall(text):
            for imp in GO_STR.findall(block or one):
                if "/" in imp and imp.split("/")[0] not in GO_STD:
                    found |= _dir_files(imp, EXTS["go"], files)
    elif ext == ".rs":
        own = posixpath.basename(path)[:-3]
        sub = here if own in ("mod", "lib", "main") else posixpath.join(here, own)
        hits = [_first(posixpath.join(sub, m), EXTS["rs"], files, extra=("/mod.rs",)) for m in RS_MOD.findall(text)]
        for u, group in RS_USE.findall(text):
            segs = u.split("::")
            if segs[0] in ("std", "alloc"):
                continue
            segs = [x for x in segs if x not in ("crate", "self", "super")]
            for tail in [""] + [n.strip().split("::")[0] for n in group.split(",") if n.strip()]:
                path_ = segs + ([tail] if tail and tail.isidentifier() else [])
                # `use mycrate::a::b` from tests or main: the leading crate name is not a folder, so retry without it
                hits.append(_walk(path_, EXTS["rs"], files, ("/mod.rs",)) or (_walk(path_[1:], EXTS["rs"], files, ("/mod.rs",)) if len(path_) > 1 else None))
    elif ext in EXTS["jvm"]:
        hits = [_walk(m.split("."), EXTS["jvm"], files) for m in JVM.findall(text)]
    elif ext == ".cs":
        for ns in CS.findall(text):
            if not ns.startswith(("System", "Microsoft")):
                found |= _dir_files(ns, EXTS["cs"], files, 4)
    elif ext == ".php":
        hits = [_first(u.replace("\\", "/"), EXTS["php"], files) for u in PHP_USE.findall(text)]
        hits += [_unique(t.lstrip("/"), files, here) for t in PHP_REQ.findall(text)]
    elif ext == ".rb":
        hits = [_first(t, (".rb", ""), files, here) for t in RB_REL.findall(text)]
        hits += [_first(t, (".rb",), files) for t in RB_REQ.findall(text)]
    elif ext == ".dart":
        hits = [_unique(t.split("/", 1)[1] if t.startswith("package:") and "/" in t else t, files, here)
                for t in DART.findall(text) if not t.startswith("dart:")]
    elif ext == ".lua":
        hits = [_first(t.replace(".", "/"), EXTS["lua"], files, extra=("/init.lua",)) for t in LUA.findall(text)]
    found |= {h for h in hits if h}
    found.discard(path)
    return found
