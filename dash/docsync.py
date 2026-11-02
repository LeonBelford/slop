"""Documentation upkeep after a layer. The per-task CHANGELOG entries and docs/ARCHITECTURE.md come from the memory step
(`Memory.changelog`, `refresh_architecture`); this adds a layer heading to the same changelog and docs/API.md from the code map."""
from __future__ import annotations

from datetime import date
from pathlib import Path


def mark_layer(repo: Path, layer: int, name: str) -> None:
    """Appends '## Layer N: name' to CHANGELOG.md once, so the task entries that follow are grouped under the finished layer."""
    f = repo / "CHANGELOG.md"
    cur = f.read_text("utf-8") if f.exists() else "# Changelog\n"
    head = f"## Layer {layer} done: {name}"
    if head not in cur:
        f.write_text(cur.rstrip() + f"\n\n{head} ({date.today().isoformat()})\n", "utf-8")


def api_markdown(codemap: dict) -> str:
    """docs/API.md: one line per source file with its purpose and its public symbols, straight from the code map."""
    lines = ["# Code reference", "", "Generated from the code map after every layer.", ""]
    for rel, e in sorted(codemap.items()):
        syms = ", ".join(f"`{s}`" for s in (e.get("symbols") or e.get("exports") or [])[:12])
        lines.append(f"- `{rel}`: {e.get('purpose', '')}" + (f" ({syms})" if syms else ""))
    return "\n".join(lines) + "\n"
