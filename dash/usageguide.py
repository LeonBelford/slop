"""AI-written "how to start and use this app" guide with concrete commands, stored in agent/USAGE.md and shown in the Testing tab."""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.cfutil import clip

SYSTEM = ("You write a short, practical user guide for the app below, for the person who owns it. Sections: '## Start' (exact commands "
          "to install, build and start it from the repo folder; for a CLI the real command lines, for a web app the URL), '## Use' (the main "
          "things you can do, each with a concrete example command or click path and the expected result), '## Tests' (how to run the tests). "
          "Use only commands, files and options that really exist in the material; never invent flags. Put every command in a ```bash block. "
          "If something is not implemented yet, say so in one line. Max 60 lines, no preamble.")


def path(ws: Path) -> Path:
    return ws / "agent" / "USAGE.md"


def read(ws: Path) -> str:
    try:
        return path(ws).read_text("utf-8")
    except OSError:
        return ""


def _files(repo: Path) -> str:
    r = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=False)
    return "\n".join(r.stdout.splitlines()[:80])


def _read(p: Path, n: int) -> str:
    try:
        return p.read_text("utf-8", "replace")[:n]
    except OSError:
        return ""


def material(repo: Path, plan: dict) -> str:
    """What the guide may rely on: run plan, file list, README, manifests and the entry file."""
    parts = [f"Detected kind: {plan.get('mode')} ({plan.get('kind')}); start command: {plan.get('run')}; URL: {plan.get('url')}; "
             f"examples from README: {plan.get('examples')}", f"Files:\n{_files(repo)}"]
    for name, n in (("README.md", 3500), ("pyproject.toml", 1200), ("package.json", 1200), ("Cargo.toml", 1000), ("Makefile", 1200)):
        t = _read(repo / name, n)
        if t:
            parts.append(f"--- {name}\n{t}")
    entry = plan.get("entry", "").split()
    for tok in entry:
        t = _read(repo / tok, 3500) if tok.endswith((".py", ".js", ".ts")) else ""
        if t:
            parts.append(f"--- {tok}\n{t}")
    return clip("\n\n".join(parts), 12000)


def generate(ws: Path, agent) -> str:
    """Writes agent/USAGE.md from the repo's current state (one planner call)."""
    plan = agent.detect_run_plan()
    text = agent.llm.chat("usage_guide", "planner", SYSTEM, material(agent.repo, plan)).strip()
    if not text:
        raise ValueError("The model returned nothing")
    path(ws).write_text(text + "\n", "utf-8")
    return text
