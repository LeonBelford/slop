"""Test generator support: finds source files without tests, queues "write tests" tasks, and checks with a mutation run
whether the tests would notice a change (a test suite that survives every mutant checks nothing)."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.cfa_verify import make_mutants
from dash import codesearch

TEST_RE = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build"}


def is_test(rel: str) -> bool:
    return bool(TEST_RE.search(rel))


def untested(repo: Path, limit: int = 30) -> list[dict]:
    """Source files that no test file mentions by module name; the biggest first (they matter most)."""
    allf = codesearch.files(repo)
    tests = "\n".join(codesearch._read(repo, f) for f in allf if is_test(f))
    out = []
    for f in allf:
        stem = Path(f).stem
        if is_test(f) or stem in ("__init__", "conftest", "setup") or re.search(r"\b" + re.escape(stem) + r"\b", tests):
            continue
        n = len(codesearch._read(repo, f).splitlines())
        if n >= 5:
            out.append({"file": f, "lines": n})
    return sorted(out, key=lambda x: -x["lines"])[:limit]


def task_for(rel: str) -> dict:
    return {"title": f"Write tests for {rel}", "type": "test", "priority": 4.0,
            "description": f"{rel} has no tests. Write focused tests for its public functions: normal cases, edge cases and errors. "
                           "Do not change the production code except for real bugs the tests reveal.",
            "acceptance": [f"tests that import {Path(rel).stem} exist and pass", "the tests fail if the logic is changed (no empty assertions)"]}


def mutants(source: str, limit: int = 8) -> list[tuple[int, str, str, str]]:
    """Up to `limit` single-change variants: (line number, old, new, mutated source). Uses the same operators as the build-time mutation check."""
    lines = source.splitlines()
    out = []
    for n, new in make_mutants(lines, list(range(1, len(lines) + 1)), limit):
        mutated = "\n".join(lines[:n - 1] + [new] + lines[n:]) + "\n"
        out.append((n, lines[n - 1].strip(), new.strip(), mutated))
    return out


def _run(cmd: str, cwd: Path, timeout: int) -> bool:
    try:
        return subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def mutation_check(repo: Path, rel: str, test_cmd: str, limit: int = 8, timeout: int = 120) -> dict:
    """Runs the tests against mutated copies of one file. Score = share of mutants the tests catch (they fail)."""
    src = repo / rel
    if not src.is_file() or not test_cmd:
        raise ValueError("File or test command missing")
    original = src.read_text("utf-8")
    cands = [m for m in mutants(original, limit) if _compiles(rel, m[3])]
    if not cands:
        return {"file": rel, "total": 0, "killed": 0, "score": None, "survivors": [], "note": "nothing to mutate in this file"}
    with tempfile.TemporaryDirectory(prefix="mutate-") as t:
        work = Path(t) / "w"
        shutil.copytree(repo, work, ignore=shutil.ignore_patterns(*SKIP_DIRS), symlinks=True)
        if not _run(test_cmd, work, timeout):
            return {"file": rel, "total": 0, "killed": 0, "score": None, "survivors": [], "note": "the tests already fail without any change"}
        survivors, killed = [], 0
        for line, old, new, text in cands:
            (work / rel).write_text(text, "utf-8")
            if _run(test_cmd, work, timeout):
                survivors.append({"line": line, "change": f"{old} -> {new}"})
            else:
                killed += 1
        (work / rel).write_text(original, "utf-8")
    return {"file": rel, "total": len(cands), "killed": killed, "score": round(killed / len(cands), 2), "survivors": survivors, "note": ""}


def _compiles(rel: str, text: str) -> bool:
    if not rel.endswith(".py"):
        return True
    try:
        compile(text, rel, "exec")
        return True
    except (SyntaxError, ValueError):
        return False
