"""Failure classes for failed task attempts: one fixed vocabulary instead of free text, so retries, the dashboard and the
diagnostics can treat each kind of failure differently. Pure functions, no agent needed."""
from __future__ import annotations

import re

RULES = [  # (class, pattern); the first match wins
    ("no_edit", r"no usable edit|made no (?:usable )?edit"),
    ("timeout", r"TIMEOUT after|timed out"),
    ("policy", r"\[policy\]"),
    ("setup_fail", r"\[setup\] FAILED"),
    ("build_fail", r"\[build\] FAILED"),
    ("lint_fail", r"\[lint\] FAILED"),
    ("test_fail", r"\[test\] FAILED|Build/test red"),
    ("security", r"Security gate"),
    ("browser", r"Browser test"),
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


def advice(cls: str) -> str:
    return ADVICE.get(cls, "")
