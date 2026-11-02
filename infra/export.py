"""Export a project as a clean, publishable repo: sources and docs only, plus README, CI and LICENSE if missing."""
from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

LICENSE = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy,
modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.
"""
CI = """name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: {cmd}
"""


def export(repo: Path, dest: Path, name: str, test_cmd: str = "echo no tests configured", holder: str = "the authors") -> list[str]:
    """Copies the tracked files of `repo` to `dest` (fresh git repo, one commit). Returns the written file list."""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("The destination folder is not empty")
    files = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True).stdout.splitlines()
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f.startswith(("agent/", ".codefactory")):
            continue
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / f, dest / f)
    extras = {"README.md": f"# {name}\n\nGenerated with CodeFactory.\n", "LICENSE": LICENSE.format(year=date.today().year, holder=holder),
              ".github/workflows/ci.yml": CI.format(cmd=test_cmd)}
    for rel, text in extras.items():
        if not (dest / rel).exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_text(text, "utf-8")
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", "-c", "user.name=CodeFactory", "-c", "user.email=codefactory@localhost", "commit", "-qm", "Initial export"], cwd=dest, check=True)
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() and ".git/" not in p.as_posix() + "/")
