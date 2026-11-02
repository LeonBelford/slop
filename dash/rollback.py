"""Layer rollback: resets main to a layer-N tag (the agent must be stopped; the old state stays reachable via a backup tag)."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise ValueError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def layers(repo: Path) -> list[int]:
    try:
        tags = _git(repo, "tag", "--list", "layer-*").split()
    except (ValueError, OSError):
        return []
    return sorted(int(m.group(1)) for t in tags if (m := re.fullmatch(r"layer-(\d+)", t)))


def rollback(repo: Path, layer: int) -> dict:
    """Hard-resets the current branch to layer-N. Raises ValueError if the tag is missing or the tree is dirty."""
    if layer not in layers(repo):
        raise ValueError(f"No tag layer-{layer}")
    if _git(repo, "status", "--porcelain"):
        raise ValueError("The working tree has uncommitted changes")
    backup = f"backup-before-rollback-{int(time.time())}"
    _git(repo, "tag", backup)
    _git(repo, "reset", "--hard", f"layer-{layer}")
    return {"ok": True, "layer": layer, "backup": backup}
