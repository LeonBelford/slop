"""Tools of the chat agent's "direct" route: it reads the project docs, explores the repo and edits it itself, committing every change."""
from __future__ import annotations

from pathlib import Path

from dash.directedit import _git, _safe

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
DOC_FIRST = ("README.md", "ARCHITECTURE.md", "PROJECT_MAP.md", "CONTRIBUTING.md", "CLAUDE.md", "AGENTS.md", "CHANGELOG.md")


def doc_files(ws: Path) -> list[Path]:
    """The important .md files: the project goal, then well-known docs at the repo root, then the rest (shallow, docs first)."""
    repo, out = ws / "repo", []
    goal = ws / "agent" / "GOAL.md"
    if goal.is_file():
        out.append(goal)
    named = [repo / n for n in DOC_FIRST if (repo / n).is_file()]
    rest = sorted((p for p in repo.rglob("*.md") if not SKIP & set(p.relative_to(repo).parts) and len(p.relative_to(repo).parts) <= 3 and p not in named),
                  key=lambda p: (len(p.relative_to(repo).parts), str(p)))
    return out + named + rest[:20]


def _label(ws: Path, p: Path) -> str:
    return "GOAL.md (project goal)" if p == ws / "agent" / "GOAL.md" else str(p.relative_to(ws / "repo"))


def read_docs(ws: Path, limit: int = 20000, per_file: int = 5000) -> tuple[str, list[str]]:
    """(text, names) of the important .md files, each cut to per_file characters and the whole to limit."""
    parts, names, used = [], [], 0
    for p in doc_files(ws):
        room = min(per_file, limit - used)
        if room < 200:
            break
        body = p.read_text("utf-8", "replace")
        body = body[:room] + ("\n[...cut]" if len(body) > room else "")
        parts.append(f"### {_label(ws, p)}\n{body}")
        names.append(_label(ws, p))
        used += len(body)
    return "\n\n".join(parts), names


def list_dir(repo: Path, path: str = "") -> str:
    d = _safe(repo, path) if str(path or "").strip(" ./") else repo.resolve()
    if not d.is_dir():
        raise ValueError("Not a folder in the repo")
    rows = [f"{p.name}/" if p.is_dir() else f"{p.name} ({p.stat().st_size} B)" for p in sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name)) if p.name not in SKIP]
    return "\n".join(rows[:150]) or "(empty)"


def read_range(repo: Path, path: str, start: int = 1, end: int = 0, limit: int = 12000) -> str:
    """A file with line numbers; start/end select lines so a long file can be read in parts."""
    f = _safe(repo, path)
    if not f.is_file():
        raise ValueError("File not found in the project repo")
    lines = f.read_text("utf-8", "replace").splitlines()
    a = max(int(start or 1), 1)
    b = min(int(end), len(lines)) if end else len(lines)
    out, n = [], 0
    for k in range(a - 1, b):
        row = f"{k + 1}: {lines[k]}"
        n += len(row) + 1
        if n > limit:
            out.append(f"[cut here, continue with start={k + 1}; the file has {len(lines)} lines]")
            break
        out.append(row)
    return "\n".join(out) or "(empty file)"


def _commit(repo: Path, msg: str) -> None:
    try:
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.name=CodeFactory", "-c", "user.email=codefactory@localhost", "commit", "-qm", msg)
    except ValueError:
        pass  # not a git repo, or nothing changed


def edit_file(repo: Path, path: str, old: str, new: str) -> str:
    """Replaces one exact piece of text; it must occur exactly once, so the model cannot hit the wrong place."""
    f = _safe(repo, path)
    if not f.is_file():
        raise ValueError("File not found in the project repo")
    text = f.read_text("utf-8")
    n = text.count(str(old))
    if not old or n != 1:
        raise ValueError("'old' must match the file exactly once" + (f" (found {n}x)" if old else ""))
    f.write_text(text.replace(str(old), str(new), 1), "utf-8")
    _commit(repo, f"chat: edit {path}")
    return f"edited {path}"


def delete_file(repo: Path, path: str) -> str:
    f = _safe(repo, path)
    if not f.is_file():
        raise ValueError("File not found in the project repo")
    f.unlink()
    _commit(repo, f"chat: delete {path}")
    return f"deleted {path}"
