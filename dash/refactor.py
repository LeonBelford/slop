"""Refactor runs: rename a symbol, find dead code, split a big module. Every run is guarded: tests must pass before and after,
otherwise everything is reset to the starting commit. The model is only needed for the module split (queued as a refactor task)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from dash import codesearch, testgen

IDENT = re.compile(r"[A-Za-z_]\w*")


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise ValueError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def _tests_ok(repo: Path, cmd: str, timeout: int = 600) -> bool:
    try:
        return subprocess.run(["bash", "-c", cmd], cwd=repo, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def rename_plan(repo: Path, old: str, new: str) -> dict:
    """What a rename would touch: the files and the number of word-boundary hits. Raises for invalid or clashing names."""
    if not IDENT.fullmatch(old) or not IDENT.fullmatch(new):
        raise ValueError("Names must be identifiers (letters, digits, underscore)")
    if old == new:
        raise ValueError("Old and new name are the same")
    if any(s["name"] == new for s in codesearch.symbols(repo)):
        raise ValueError(f"'{new}' already exists in the project")
    word = re.compile(r"\b" + re.escape(old) + r"\b")
    hits = {f: len(word.findall(codesearch._read(repo, f))) for f in codesearch.files(repo)}
    return {"old": old, "new": new, "files": {f: n for f, n in hits.items() if n}}


def rename(repo: Path, old: str, new: str, test_cmd: str) -> dict:
    """Applies the rename on a clean tree, runs the tests and commits; on red tests everything is reset."""
    if _git(repo, "status", "--porcelain"):
        raise ValueError("The working tree has uncommitted changes")
    plan = rename_plan(repo, old, new)
    if not plan["files"]:
        raise ValueError(f"'{old}' was not found")
    if test_cmd and not _tests_ok(repo, test_cmd):
        raise ValueError("The tests fail before the rename: fix them first")
    word = re.compile(r"\b" + re.escape(old) + r"\b")
    for f in plan["files"]:
        p = repo / f
        p.write_text(word.sub(new, p.read_text("utf-8")), "utf-8")
    if test_cmd and not _tests_ok(repo, test_cmd):
        _git(repo, "reset", "--hard", "HEAD")
        raise ValueError("The tests fail after the rename: everything was reset")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=CodeFactory", "-c", "user.email=codefactory@localhost", "commit", "-qm", f"refactor: rename {old} to {new}")
    return {"ok": True, "files": len(plan["files"]), "hits": sum(plan["files"].values())}


def dead_code(repo: Path, limit: int = 40) -> list[dict]:
    """Python functions and classes that are mentioned nowhere else (not even in tests). Candidates only: dynamic use is invisible."""
    texts = {f: codesearch._read(repo, f) for f in codesearch.files(repo)}
    blob = "\n".join(texts.values())
    out = []
    for f, t in texts.items():
        if not f.endswith(".py") or testgen.is_test(f):
            continue
        for d in codesearch.definitions(repo, f, t):
            n = d["name"]
            if n.startswith("__") or n in ("main", "setUp", "tearDown") or n.startswith("test"):
                continue
            if len(re.findall(r"\b" + re.escape(n) + r"\b", blob)) <= 1:
                out.append(d)
    return out[:limit]


def dead_code_task(items: list[dict]) -> dict:
    lines = "\n".join(f"- {d['file']}:{d['line']} {d['kind']} {d['name']}" for d in items[:15])
    return {"title": "Remove dead code", "type": "refactor", "priority": 3.0,
            "description": "These definitions are not used anywhere else. Check each one (dynamic use, public API, entry points) and delete only "
                           f"the ones that are really unused:\n{lines}",
            "acceptance": ["Build and tests green", "no behavior change", "only definitions without any use were removed"]}


def split_task(rel: str, lines: int) -> dict:
    return {"title": f"Split {rel} into smaller modules", "type": "refactor", "priority": 3.0,
            "description": f"{rel} has {lines} lines. Split it along its responsibilities into modules of under 400 lines each. Keep the public names "
                           "importable from the old module so nothing else has to change.",
            "acceptance": ["Build and tests green", "no behavior change", "every new module is under 400 lines"]}


def big_modules(repo: Path, limit_lines: int = 500) -> list[dict]:
    out = [{"file": f, "lines": len(codesearch._read(repo, f).splitlines())} for f in codesearch.files(repo) if not testgen.is_test(f)]
    return sorted((x for x in out if x["lines"] > limit_lines), key=lambda x: -x["lines"])[:20]
