"""Failure classes for failed task attempts: one fixed vocabulary instead of free text, so retries, the dashboard and the
diagnostics can treat each kind of failure differently. Pure functions, no agent needed."""
from __future__ import annotations

import re

from core.cfutil import tail

RULES = [  # (class, pattern); the first match wins
    ("no_edit", r"no usable edit|made no (?:usable )?edit"),
    ("timeout", r"TIMEOUT after|timed out"),
    ("policy", r"\[policy\]"),
    ("setup_fail", r"\[setup\] FAILED"),
    ("build_fail", r"\[build\] FAILED"),
    ("lint_fail", r"\[lint\] FAILED"),
    ("test_fail", r"\[test\] FAILED|Build/test red"),
    ("security", r"Security gate"),
    ("browser", r"(?:Browser|UI) test"),
    ("acceptance", r"acceptance"),
    ("mutation", r"mutation"),
    ("review", r"[Rr]eview"),
    ("merge", r"[Ff]ast-forward|after merge|Build on main red"),
    ("exception", r"^Exception:|Traceback"),
]
ADVICE = {
    "no_edit": "The coder produced no edit last time. Name the exact files and the exact function to change, and keep the task smaller.",
    "timeout": "A command ran into the time limit. Avoid long-running work and make the change smaller.",
    "policy": "A project rule rejected the change (tests missing or deleted). Satisfy the rule instead of working around it.",
    "build_fail": "The build failed. Fix the compile/import error first, before any other change.",
    "test_fail": "Tests failed. Read the first failing assertion and fix the cause in the production code.",
    "lint_fail": "Lint failed. Fix exactly the reported lines.",
    "security": "The security gate found a problem. Remove the risky pattern instead of suppressing the finding.",
    "review": "The review rejected the change. Address each listed issue before anything else.",
    "acceptance": "An acceptance criterion is not met. Re-read the criteria and implement the missing behaviour.",
}


def classify(why: str) -> str:
    """The failure class of a failure report (see RULES); `other` if nothing matches."""
    for name, pat in RULES:
        if re.search(pat, why or "", re.M):
            return name
    return "other"


def signature(why: str) -> str:
    """Stable fingerprint of a failure: class plus the first meaningful line with numbers and addresses removed. Two attempts with
    the same signature failed for the same reason, so a third try the same way is pointless."""
    for line in (why or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("$", "[")) and len(line) > 8:
            return classify(why) + ":" + re.sub(r"0x[0-9a-f]+|\d+|\s+", "", line)[:120]
    return classify(why)


def focus(out: str, n: int) -> str:
    """The part of a long build/test output that matters: compilers print the root cause FIRST and cascades after it, so from the
    first `error` line on, not the tail. Without such a line the tail is kept (test runners summarise at the end)."""
    if len(out) <= n:
        return out
    m = re.search(r"^error(?:\[\w+\])?:", out, re.M)
    if not m:
        return tail(out, n)
    head = out[m.start(): m.start() + n * 2 // 3]
    rest = out[m.start() + len(head):]
    return head + (f"\n...[{len(rest)} more characters]...\n" + tail(rest, n // 3) if rest.strip() else "")


LOCATIONS = [re.compile(x, re.M) for x in (
    r'File "([^"\n]+)", line (\d+)',  # python traceback
    r"--> ([\w./\\-]+):(\d+):\d+",  # rustc
    r"^([\w./\\-]+\.(?:go|c|cc|cpp|h|java|kt|cs|rb|php)):(\d+)(?::\d+)?:",  # go, gcc, javac, ruby
    r"^\s*([\w./\\-]+\.(?:ts|tsx|js|jsx)):(\d+)(?::\d+)?",  # node, tsc, jest
    r"([\w./\\-]+\.(?:ts|tsx))\((\d+),\d+\)",  # tsc
    r"^([\w./\\-]+\.py):(\d+): ",  # pytest short form
)]


def snippets(report: str, repo, limit: int = 3, around: int = 6) -> str:
    """The source lines a failure report points at (project files only, deepest frame first): the coder sees the failing code at once
    instead of guessing which part of the file the traceback means. "" if the report names no project file."""
    from pathlib import Path
    root = Path(repo).resolve()
    hits: list[tuple[str, int]] = []
    for pat in LOCATIONS:
        for m in pat.finditer(report or ""):
            path = Path(m.group(1))
            try:
                rel = (path.resolve() if path.is_absolute() else (root / path).resolve()).relative_to(root)
            except (ValueError, OSError):
                continue
            if (root / rel).is_file() and ".venv" not in rel.parts and "node_modules" not in rel.parts:
                hits.append((rel.as_posix(), int(m.group(2))))
    out, seen = [], set()
    for rel, line in reversed(hits):
        if (rel, line) in seen or len(out) >= limit:
            continue
        seen.add((rel, line))
        try:
            lines = (root / rel).read_text("utf-8", "replace").splitlines()
        except OSError:
            continue
        a, b = max(1, line - around), min(len(lines), line + around)
        out.append(f"--- {rel}, lines {a}-{b} (the failure points at line {line})\n" + "\n".join(f"{n:>4}| {lines[n - 1]}" for n in range(a, b + 1)))
    return "Code the failure points at:\n" + "\n".join(out) + "\n" if out else ""


def advice(cls: str) -> str:
    """One-line hint for a failure class (e.g. what usually fixes a timeout), or "" if there is none."""
    return ADVICE.get(cls, "")
