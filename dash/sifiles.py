"""File selection for self-improve: dependents, context budget and content-based candidates."""
from __future__ import annotations

import ast
import re
from pathlib import Path

MAP_FILE = "PROJECT_MAP.md"  # complete description of this repository; read before every plan and every change
CONTEXT_TOKENS = 7000  # budget for the files Aider may edit; the pinned model window (perf.NUM_CTX, 16k) also holds map, prompt and answer


def _dependents(work: Path, files: list[str]) -> dict[str, list[str]]:
    """Which other Python files import each file (the 'who breaks if I change this' map)."""
    out: dict[str, list[str]] = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        mod = re.escape(Path(f).stem)
        pat = re.compile(rf"^\s*(?:import\s+(?:\w+\.)*{mod}\b|from\s+(?:\w+\.)*{mod}\b|from\s+[\w.]+\s+import\s+[^\n]*\b{mod}\b)", re.M)
        out[f] = sorted(str(p.relative_to(work)) for p in work.rglob("*.py") if str(p.relative_to(work)) != f and ".venv" not in p.parts
                        and pat.search(p.read_text("utf-8", "replace")))
    return out


def _fit(work: Path, files: list[str], budget: int = CONTEXT_TOKENS) -> list[str]:
    """Only files that need edits, as many as fit the context budget. Docs and the project map are read-only (and updated in the docs pass)."""
    out, used = [], 0
    for f in dict.fromkeys(files):
        if f == MAP_FILE or f.startswith("docs/") or f.endswith(".md") and f != "README.md" or not (work / f).is_file():
            continue
        tokens = (work / f).stat().st_size // 3
        if out and used + tokens > budget:
            continue  # the first file is always kept (edited with the compact diff format), later big ones wait for their own step
        out.append(f)
        used += tokens
    return out


def symbol_words(body: str, name: str) -> set[str]:
    """Words of the top-level function/class names of a Python file (a cheap symbol index: a request about `scheduler` finds `def schedule_jobs`)."""
    if not name.endswith(".py"):
        return set()
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out |= {w for w in re.findall(r"[a-z]{5,}", n.name.lower().replace("_", " "))}
    return out


def _keyword_files(repo: Path, text: str, tracked: list[str], have: list[str], limit: int = 3) -> list[str]:
    """Content-based file candidates: files that mention the words of the request most often (cheap code search, no LLM)."""
    words = {w for w in re.findall(r"[a-zA-Z_]{5,}", text.lower()) if w not in {"should", "would", "please", "which", "there", "their", "about"}}
    score = {}
    for f in tracked:
        if f in have or not f.endswith((".py", ".html", ".md", ".sh")):
            continue
        try:
            body = (repo / f).read_text("utf-8", "replace").lower()
        except OSError:
            continue
        n = sum(min(body.count(w), 5) for w in words) + 6 * len(words & symbol_words(body, f))
        if n:
            score[f] = n
    return sorted(score, key=score.get, reverse=True)[:limit]
