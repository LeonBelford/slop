"""Last-resort file writer: when the coder only printed files into the chat, take them from its answer and write them to disk."""
from __future__ import annotations

import re
from pathlib import Path

FENCE = re.compile(r"(?P<name>[\w./\\-]+\.\w+)[ \t]*\n[ \t]*(?P<fence>`{3,4})[\w+-]*[ \t]*\n(?P<body>.*?)\n[ \t]*(?P=fence)", re.S)


def extract_files(answer: str, allowed: list[str]) -> dict[str, str]:
    """{path: content} for every `path` line directly followed by a fenced block; a single unnamed block goes to the only allowed file."""
    found = {}
    for m in FENCE.finditer(answer):
        name = m.group("name").lstrip("./")
        if name in allowed:
            found[name] = m.group("body") + "\n"
    if not found and len(allowed) == 1:
        blocks = re.findall(r"```[\w+-]*[ \t]*\n(.*?)\n```", answer, re.S)
        if len(blocks) == 1 and len(blocks[0].strip().splitlines()) >= 3:
            found[allowed[0]] = blocks[0] + "\n"
    return found


def write_files(repo: Path, files: dict[str, str]) -> list[str]:
    done = []
    for name, body in files.items():
        p = (repo / name).resolve()
        if repo.resolve() in p.parents:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, "utf-8")
            done.append(name)
    return done
