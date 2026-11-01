"""Per-project agent profile and context files (standard library only).

`agent/profile.json`  title, description, tone and free-form instructions that go into every build prompt.
`agent/context/`      files the user uploads as background knowledge (specs, API docs, style guides, data samples).
Both are user-provided and therefore trusted like IDEAS.md; the context files are only used as reference material.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.cfutil import clip, read_json, words, write_json

MAX_FILE = 200_000
MAX_FILES = 40
MAX_TOTAL = 1_500_000
TONES = ("neutral", "concise", "thorough", "playful")
NAME_RE = re.compile(r"^[\w][\w .()-]{0,80}$")
TEXT_EXT = {".md", ".txt", ".json", ".csv", ".yml", ".yaml", ".toml", ".xml", ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".sql",
            ".rst", ".ini", ".cfg", ".env.example", ".sh", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".rb", ".php", ".log"}
DEFAULT = {"title": "", "description": "", "tone": "neutral", "instructions": ""}


class ProfileError(ValueError):
    """Raised when a profile or context-file request is invalid (e.g. bad id, oversized upload)."""

    pass


def load_profile(agent_dir: Path) -> dict:
    d = read_json(Path(agent_dir) / "profile.json", {})
    return {k: str(d.get(k, v)) for k, v in DEFAULT.items()}


def save_profile(agent_dir: Path, body: dict) -> dict:
    p = load_profile(agent_dir)
    for k in DEFAULT:
        if k in body:
            p[k] = str(body[k]).strip()
    limits = {"title": 80, "description": 400, "instructions": 8000}
    for k, n in limits.items():
        if len(p[k]) > n:
            raise ProfileError(f"{k} is too long (max {n} characters)")
    if p["tone"] not in TONES:
        raise ProfileError(f"tone must be one of {', '.join(TONES)}")
    write_json(Path(agent_dir) / "profile.json", p)
    return p


def profile_block(agent_dir: Path) -> str:
    p = load_profile(agent_dir)
    lines = []
    if p["description"]:
        lines.append(f"Role of this agent: {p['description']}")
    if p["tone"] != "neutral":
        lines.append(f"Style of answers and code comments: {p['tone']}.")
    if p["instructions"]:
        lines.append(p["instructions"])
    return "## Custom instructions of the owner (binding)\n" + clip("\n".join(lines), 3500) if lines else ""


def _dir(agent_dir: Path) -> Path:
    return Path(agent_dir) / "context"


def list_context(agent_dir: Path) -> list[dict]:
    out = []
    for f in sorted(_dir(agent_dir).glob("*")) if _dir(agent_dir).is_dir() else []:
        if f.is_file():
            out.append({"name": f.name, "bytes": f.stat().st_size})
    return out


def add_context(agent_dir: Path, name: str, text: str) -> dict:
    name = str(name or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not NAME_RE.match(name) or name.startswith("."):
        raise ProfileError("Invalid file name")
    if Path(name).suffix.lower() not in TEXT_EXT and not name.lower().endswith(".env.example"):
        raise ProfileError("Only text files are supported (md, txt, json, csv, code, ...)")
    if "\0" in text:
        raise ProfileError("Binary files are not supported")
    data = text.encode("utf-8")
    if len(data) > MAX_FILE:
        raise ProfileError(f"File is too large (max {MAX_FILE // 1000} KB); split it or upload the relevant part")
    cur = list_context(agent_dir)
    others = [c for c in cur if c["name"] != name]
    if len(others) >= MAX_FILES:
        raise ProfileError(f"At most {MAX_FILES} context files")
    if sum(c["bytes"] for c in others) + len(data) > MAX_TOTAL:
        raise ProfileError("Context files are too large in total")
    d = _dir(agent_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return {"name": name, "bytes": len(data)}


def delete_context(agent_dir: Path, name: str) -> None:
    f = _dir(agent_dir) / str(name)
    if f.parent != _dir(agent_dir) or not f.is_file():
        raise ProfileError("File not found")
    f.unlink()


def read_context(agent_dir: Path, name: str) -> str:
    f = _dir(agent_dir) / str(name)
    if f.parent != _dir(agent_dir) or not f.is_file():
        raise ProfileError("File not found")
    return f.read_text("utf-8", "replace")


def context_block(agent_dir: Path, query: str, budget: int = 5000) -> str:
    """The parts of the context files that fit the task best: small files whole, big ones as their best-matching paragraphs."""
    files = list_context(agent_dir)
    if not files:
        return ""
    q = words(query)
    chunks = []
    for c in files:
        text = _dir(agent_dir).joinpath(c["name"]).read_text("utf-8", "replace")
        if len(text) <= 1200:
            chunks.append((len(q & words(text)) + 0.5, c["name"], text))
            continue
        for para in re.split(r"\n\s*\n", text):
            if para.strip():
                chunks.append((len(q & words(para)), c["name"], para.strip()))
    chunks.sort(key=lambda x: -x[0])
    out, used = [], 0
    for score, name, text in chunks:
        if score <= 0 and out:
            break
        piece = f"[{name}]\n{clip(text, 1500)}"
        if used + len(piece) > budget:
            continue
        out.append(piece)
        used += len(piece)
    head = "## Context files from the owner (reference material; names: " + ", ".join(c["name"] for c in files[:12]) + ")\n"
    return head + "\n\n".join(out) if out else ""


def overview(agent_dir: Path) -> dict:
    return {"profile": load_profile(agent_dir), "context": list_context(agent_dir), "limits": {"file": MAX_FILE, "files": MAX_FILES}}


__all__ = ["ProfileError", "add_context", "context_block", "delete_context", "list_context", "load_profile", "overview", "profile_block",
           "read_context", "save_profile", "TONES"]
