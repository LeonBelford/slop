"""How to start and try the product that is really being built (not the stack's default web server).

A CLI tool gets its own entry command plus example calls, a web app or API gets its server command and URL.
Purely static: reads the product brief (interface.json), the manifest files and the README, no model call.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.cfutil import read_json

CLI_LIBS = ("argparse", "click", "typer", "docopt", "fire", "optparse", "sys.argv", "commander", "yargs", "clap", "cobra", "flag.Parse", "os.Args")
WEB_LIBS = ("fastapi", "flask", "django", "uvicorn", "express(", "require('express')", 'require("express")', "http.server", "axum",
            "net/http", "actix", "sinatra", "aiohttp", "starlette", "bottle", "koa", "fastify")
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", "tests", "test", "docs", "vendor"}
PY_CANDIDATES = ("__main__.py", "main.py", "cli.py", "app.py", "run.py")
FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
MAX_EXAMPLES = 6


def _read(p: Path, limit: int = 40_000) -> str:
    try:
        return p.read_text("utf-8", "replace")[:limit]
    except OSError:
        return ""


def _files(repo: Path, exts: tuple[str, ...], depth: int = 3) -> list[Path]:
    out: list[Path] = []

    def walk(d: Path, n: int) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_dir() and e.name not in SKIP and not e.name.startswith(".") and n < depth:
                walk(e, n + 1)
            elif e.is_file() and e.suffix in exts:
                out.append(e)
    walk(repo, 0)
    return out


def guess_interface(repo: Path, brief: dict) -> str:
    """The brief wins; otherwise guess from the sources: web libraries mean web, argument parsing means cli."""
    kind = str(brief.get("interface") or "").lower()
    if kind in ("cli", "web", "api", "library", "desktop"):
        return kind
    text = "".join(_read(p, 8000) for p in _files(repo, (".py", ".js", ".ts", ".go", ".rs"))[:40])
    if any(w in text for w in WEB_LIBS):
        return "web"
    if any(w in text for w in CLI_LIBS):
        return "cli"
    return ""


def _py_entry(repo: Path) -> tuple[str, str]:
    """(command, name) of the Python entry point: console script, package __main__, or a script that parses arguments."""
    pyp = _read(repo / "pyproject.toml")
    m = re.search(r"\[project\.scripts\]\s*\n\s*([\w.-]+)\s*=\s*[\"']([\w.]+):", pyp)
    if m:
        return f"python3 -m {m.group(2)}", m.group(1)
    for p in _files(repo, (".py",), 2):
        if p.name == "__main__.py":
            rel = p.parent.relative_to(repo)
            return (f"python3 -m {'.'.join(rel.parts)}", rel.name) if rel.parts else ("python3 __main__.py", "app")
    for name in PY_CANDIDATES[1:]:
        for p in _files(repo, (".py",), 2):
            if p.name == name:
                src = _read(p, 12000)
                if "__main__" in src or any(w in src for w in CLI_LIBS):
                    return f"python3 {p.relative_to(repo)}", p.stem
    return "", ""


def _node_entry(repo: Path) -> tuple[str, str]:
    try:
        pkg = json.loads(_read(repo / "package.json") or "{}")
    except ValueError:
        return "", ""
    binp = pkg.get("bin")
    if isinstance(binp, str):
        return f"node {binp}", str(pkg.get("name", "app"))
    if isinstance(binp, dict) and binp:
        name, path = next(iter(binp.items()))
        return f"node {path}", name
    main = pkg.get("main")
    if main:
        return f"node {main}", str(pkg.get("name", "app"))
    return "", ""


def cli_entry(repo: Path, preset: str) -> tuple[str, str]:
    """(command prefix, tool name) to call the product's CLI. Arguments are appended by the caller."""
    if (repo / "Cargo.toml").exists():
        return "cargo run --quiet --", "app"
    if (repo / "go.mod").exists():
        return "go run .", "app"
    if (repo / "package.json").exists():
        cmd = _node_entry(repo)
        if cmd[0]:
            return cmd
    cmd = _py_entry(repo)
    if cmd[0]:
        return cmd
    return "", ""


def examples(repo: Path, prefix: str, name: str) -> list[str]:
    """Arguments worth trying: always --help, plus command lines the README shows for this tool."""
    out = ["--help"]
    readme = next((p for p in (repo / "README.md", repo / "readme.md") if p.exists()), None)
    if not readme or not name:
        return out
    for block in FENCE.findall(_read(readme)):
        for line in block.splitlines():
            line = line.strip().lstrip("$ ").strip()
            for lead in (name, prefix):
                if lead and line.startswith(lead + " "):
                    args = line[len(lead):].strip()
                    if args and args not in out and len(args) < 120 and not any(c in args for c in "|;&><`"):
                        out.append(args)
                    break
    return out[:MAX_EXAMPLES]


def server_entry(repo: Path) -> tuple[str, str]:
    """(command, url) of a Python or Node server script the stack preset does not know about (e.g. a plain http.server)."""
    for p in _files(repo, (".py", ".js"), 2):
        src = _read(p, 12000)
        if any(w in src for w in WEB_LIBS) and ("__main__" in src or "listen(" in src or "serve_forever" in src or ".run(" in src):
            port = re.search(r"port\W{0,6}(\d{4,5})|:(\d{4,5})\b|,\s*(\d{4,5})\s*\)", src, re.I)
            n = next((g for g in (port.groups() if port else ()) if g), "")
            return f"{'node' if p.suffix == '.js' else 'python3'} {p.relative_to(repo)}", f"http://127.0.0.1:{n}" if n else ""
    return "", ""


def plan(repo: Path, info_path: Path, stack: dict) -> dict:
    """{mode: cli|web|library|desktop, run, url, examples, entry, hint}: what the user should start to try the product now."""
    brief = read_json(info_path, {})
    mode = guess_interface(repo, brief)
    base = {"run": stack.get("run", ""), "url": stack.get("url", "")}
    if mode == "library":
        test = (stack.get("commands") or {}).get("test", "")
        return {"mode": "library", "run": test, "url": "", "entry": "", "name": "", "examples": [],
                "hint": "A library has nothing to start: the tests are the way to try it."}
    if mode == "cli":
        prefix, name = cli_entry(repo, stack.get("label", ""))
        if prefix:
            return {"mode": "cli", "run": prefix, "url": "", "entry": prefix, "name": name, "examples": examples(repo, prefix, name),
                    "hint": "A command line tool: enter arguments and press Run, the output appears right below."}
        return {"mode": "cli", "run": "", "url": "", "entry": "", "name": "", "examples": [],
                "hint": "No entry point found yet. The agent has not written a runnable command line tool so far."}
    if mode == "desktop":
        return {"mode": "desktop", **base, "examples": [], "hint": "A desktop app: start it with the command, it opens its own window."}
    if mode in ("web", "api") and not base["url"]:  # stack has no server (e.g. minimal): find the real one
        run, url = server_entry(repo)
        if run:
            base = {"run": run, "url": url}
            return {"mode": "web", **base, "examples": [], "hint": ""}
    return {"mode": "web" if base["url"] else "other", **base, "examples": [], "hint": ""}
