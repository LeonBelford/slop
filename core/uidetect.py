"""Does the project have a user interface that UI tests can drive? CLI tools, APIs and libraries do not, so they get no UI tests."""
from __future__ import annotations

import os
from pathlib import Path

from core.cfutil import read_json, write_json

UI_EXT = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"}
UI_DIRS = {"templates", "static", "public"}
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", "vendor", "e2e", "docs", "tests", "test"}
INTERFACES = ("web", "cli", "api", "library", "desktop", "other")
MAX_FILES = 4000


def repo_has_ui_files(repo: Path) -> bool:
    """True if the repo contains web UI sources (html/jsx/tsx/vue/svelte files or templates/static/public folders)."""
    seen = 0
    for _root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP]
        if UI_DIRS & set(dirs) or any(Path(f).suffix.lower() in UI_EXT for f in files):
            return True
        seen += len(files)
        if seen > MAX_FILES:
            break
    return False


def save(path: Path, brief: dict) -> dict:
    """Stores what the product brief says about the interface (`interface`, `has_ui`)."""
    kind = str(brief.get("interface", "")).lower()
    info = {"interface": kind if kind in INTERFACES else "", "has_ui": brief.get("has_ui") if isinstance(brief.get("has_ui"), bool) else None}
    write_json(path, info)
    return info


def has_ui(repo: Path, info_path: Path) -> bool:
    """The brief can rule UI out (CLI, API, library); otherwise UI files must actually exist, so early core layers get no UI tests."""
    info = read_json(info_path, {})
    if info.get("has_ui") is False or info.get("interface") in ("cli", "api", "library"):
        return False
    return repo_has_ui_files(repo)
