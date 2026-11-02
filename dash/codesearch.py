"""Code search for the UI: symbol search, "who uses this" and a call graph. Python is parsed with ast, other languages with regexes."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".cs", ".rb", ".php", ".swift"}
DEF_RE = re.compile(r"^\s*(?:export\s+|public\s+|private\s+|static\s+|async\s+|pub\s+)*(function|class|def|fn|func|struct|interface|enum|type|const|let)\s+([A-Za-z_]\w*)")
MAX_FILE = 400_000
MAX_FILES = 3000
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def files(repo: Path) -> list[str]:
    r = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, timeout=30)
    return [f for f in r.stdout.splitlines() if Path(f).suffix in EXTS][:MAX_FILES]


def _read(repo: Path, rel: str) -> str:
    try:
        p = repo / rel
        return p.read_text("utf-8", "replace") if p.stat().st_size <= MAX_FILE else ""
    except OSError:
        return ""


def definitions(repo: Path, rel: str, text: str | None = None) -> list[dict]:
    text = _read(repo, rel) if text is None else text
    out: list[dict] = []
    if rel.endswith(".py"):
        try:
            for n in ast.walk(ast.parse(text)):
                if isinstance(n, (*FUNCS, ast.ClassDef)):
                    out.append({"name": n.name, "kind": "class" if isinstance(n, ast.ClassDef) else "function", "file": rel, "line": n.lineno})
            return out
        except SyntaxError:
            pass
    for i, line in enumerate(text.splitlines(), 1):
        m = DEF_RE.match(line)
        if m:
            out.append({"name": m.group(2), "kind": m.group(1), "file": rel, "line": i})
    return out


def symbols(repo: Path) -> list[dict]:
    return [d for f in files(repo) for d in definitions(repo, f)]


def repo_map(repo: Path, budget: int = 3500, focus: str = "") -> str:
    """Compact map (file: symbols) that costs a fraction of the file contents. Files whose path or symbols match words of
    `focus` (the user's question) come first; the rest is cut at the character budget."""
    words = {w for w in re.findall(r"[a-z_]{3,}", focus.lower())}
    rows = []
    for f in files(repo):
        defs = definitions(repo, f)
        if not defs:
            continue
        top = [d for d in defs if d["kind"] in ("class", "function", "def", "fn", "func", "struct", "interface", "enum", "type")]
        hit = sum(1 for w in words if w in f.lower() or any(w in d["name"].lower() for d in top))
        rows.append((-hit, f, f"{f}: " + ", ".join(d["name"] for d in top[:12]) + (" …" if len(top) > 12 else "")))
    out, used = [], 0
    for _, _, line in sorted(rows):
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def search(repo: Path, query: str, limit: int = 40) -> list[dict]:
    """Symbols whose name matches the query: exact first, then prefix, then contains."""
    q = query.strip().lower()
    if len(q) < 2:
        return []

    def rank(s: dict) -> int:
        n = s["name"].lower()
        return 0 if n == q else 1 if n.startswith(q) else 2
    return sorted((s for s in symbols(repo) if q in s["name"].lower()), key=lambda s: (rank(s), s["name"]))[:limit]


def _owners(text: str) -> list[tuple[int, int, str]]:
    try:
        return sorted((n.lineno, getattr(n, "end_lineno", n.lineno), n.name) for n in ast.walk(ast.parse(text)) if isinstance(n, FUNCS))
    except SyntaxError:
        return []


def _owner(owners: list[tuple[int, int, str]], line: int) -> str:
    best = ""
    for a, b, name in owners:  # sorted by start: the last enclosing one is the innermost
        if a <= line <= b:
            best = name
    return best


def uses(repo: Path, symbol: str, limit: int = 60) -> list[dict]:
    """Every line that mentions the symbol outside its definition line, with the enclosing function for Python."""
    if not re.fullmatch(r"[A-Za-z_]\w*", symbol):
        return []
    word = re.compile(r"\b" + re.escape(symbol) + r"\b")
    out: list[dict] = []
    for rel in files(repo):
        text = _read(repo, rel)
        if symbol not in text:
            continue
        defs = {d["line"] for d in definitions(repo, rel, text) if d["name"] == symbol}
        owners = _owners(text) if rel.endswith(".py") else []
        for i, line in enumerate(text.splitlines(), 1):
            if i not in defs and word.search(line):
                out.append({"file": rel, "line": i, "text": line.strip()[:160], "in": _owner(owners, i)})
                if len(out) >= limit:
                    return out
    return out


def call_edges(repo: Path) -> dict[str, set[str]]:
    """Python call graph by name: function -> the project functions it calls."""
    trees: list[ast.AST] = []
    for rel in files(repo):
        if rel.endswith(".py"):
            try:
                trees.append(ast.parse(_read(repo, rel)))
            except SyntaxError:
                continue
    defined = {n.name for t in trees for n in ast.walk(t) if isinstance(n, FUNCS)}
    edges: dict[str, set[str]] = {}
    for t in trees:
        for fn in (n for n in ast.walk(t) if isinstance(n, FUNCS)):
            for c in ast.walk(fn):
                if isinstance(c, ast.Call):
                    f = c.func
                    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                    if name in defined and name != fn.name:
                        edges.setdefault(fn.name, set()).add(name)
    return edges


def call_graph(repo: Path, symbol: str, depth: int = 2) -> dict:
    """Callers and callees of a function up to `depth` levels (each node capped so the answer stays readable)."""
    edges = call_edges(repo)
    rev: dict[str, set[str]] = {}
    for a, bs in edges.items():
        for b in bs:
            rev.setdefault(b, set()).add(a)

    def walk(graph: dict[str, set[str]]) -> dict:
        seen, layer, tree = {symbol}, [symbol], {}
        for _ in range(depth):
            nxt: list[str] = []
            for n in layer:
                kids = sorted(graph.get(n, set()) - seen)[:12]
                if kids:
                    tree[n] = kids
                seen.update(kids)
                nxt += kids
            layer = nxt
        return tree
    return {"symbol": symbol, "callees": walk(edges), "callers": walk(rev)}
