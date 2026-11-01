"""Constants and helpers shared by every CodeFactory module (config defaults, shell, JSON, process control)."""
from __future__ import annotations

from pathlib import Path
from infra import gpusched
import gzip
import hashlib
import json
import os
import re
import signal
from infra import stacks
import subprocess
import threading
import time


# --------------------------------------------------------------------------- Configuration

DEFAULT_CONFIG = {
    "preset": "auto",
    "doc_language": "English",
    "llm_api": os.getenv("LLM_API", "http://localhost:11434/v1/chat/completions"),
    "api_key": os.getenv("LLM_API_KEY", "ollama"),
    "models": {  # roles -> model name at the LLM endpoint (feel free to pick a stronger one for planning)
        "planner": os.getenv("PLANNER_MODEL", "qwen2.5-coder:14b"),
        "reviewer": os.getenv("REVIEWER_MODEL", "qwen2.5-coder:14b"),
        "summarizer": os.getenv("SUMMARIZER_MODEL", "qwen2.5-coder:14b"),
    },
    "coder_model": os.getenv("CODER_MODEL", "ollama_chat/qwen2.5-coder:14b"),  # Aider model string
    # Optional model routing (see models.py): installed extra models are detected and used automatically.
    # mode "auto" = use what is installed, "off" = always the models above. light/heavy/final_reviewer force a model name.
    "routing": {"mode": "auto", "light": "", "heavy": "", "final_reviewer": "", "heavy_from_attempt": 2, "complex_files": 4},
    "limits": {
        "max_iterations": 500,
        "max_hours": 24,
        "max_attempts_per_task": 3,
        "max_debug_rounds": 3,
        "max_consecutive_failures": 5,
        "coder_timeout": 1500,
        "cmd_timeout": 900,
        "llm_timeout": 900,
        "think_chars": 6000,   # a model that reasons longer than this is cut off and asked to answer directly
        "max_answer_tokens": 6000,
        "ideation_batch": 4,
        "ideation_min_score": 6,
        "refactor_every": 4,
        "coverage_every": 5,
        "security_every": 3,
        "product_review_every": 6,
        "architecture_refresh_every": 3,
        "health_every": 5,
        "ideas_review_every": 5,
        "min_review_score": 7,
        "max_files_per_task": 8,
        "reflect_every": 6,
    },
    "context": {"max_chars": 26000, "file_clip": 6000},
    # Internet access. mode: "off" | "sources" (only URLs listed in SOURCES.md) | "open" (also web search by the agent)
    # How long the autonomous loop runs. "default": until 3 idea rounds in a row find nothing new (product saturated);
    # "rounds": stop once the backlog is empty after `rounds` idea rounds; "endless": keep building until you stop it
    # (ignores max_iterations/max_hours; still stops on repeated failures).
    "run": {"mode": "default", "rounds": 3},
    # GPU scheduling between agents: "main" keeps priority, "side" only uses the model while main is idle for min_idle seconds
    "scheduler": {"priority": "main", "min_idle": 20, "slots": 2},
    "web": {"mode": "sources", "refresh_hours": 24, "max_chars": 20000, "timeout": 20, "research_every": 4,
            "queries_per_research": 3, "results_per_query": 2, "max_new_pages_per_session": 40,
            "search_url": "https://html.duckduckgo.com/html/?q={q}", "blocked_domains": []},
    # Knowledge index (knowledge.py): embeddings "auto" = use embed_model if Ollama has it, "off" = keyword search only.
    # Lessons fade out: weight halves every half_life_days without confirmation; archived below min_weight.
    "knowledge": {"embeddings": "auto", "embed_model": "nomic-embed-text", "half_life_days": 60, "min_weight": 0.15,
                  "hits_per_kind": {"code": 4, "decision": 4, "lesson": 5, "web": 3, "feature": 3}},
    "features": {"cleanup_pass": True, "require_tests": True, "plan_critique": True, "e2e": "auto", "security": "on",
                 "test_first": "auto", "step_by_step": "auto", "acceptance_check": True, "retry_replan": True, "protect_tests": True,
                 "targeted_tests": True, "flaky_retry": True, "verify_cache": True, "coverage_gate": "off", "mutation_check": False},
    "commands": {},  # filled by the preset, overridable in config.json
    "source_exts": [".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".toml", ".yml", ".yaml"],
    "src_dirs": ["app"],
    "ignore_dirs": [".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build", "docs"],
}

# Limits the agent may tune itself during reflection, with hard bounds (min, max). Nothing else is ever self-modified.
TUNABLE = {"max_attempts_per_task": (2, 5), "max_debug_rounds": (2, 6), "max_files_per_task": (4, 12),
           "min_review_score": (6, 9), "ideation_batch": (2, 6), "refactor_every": (3, 10), "coverage_every": (3, 10)}

PRESETS = stacks.STACKS  # one entry per language/framework, see stacks.py

QUALITY_CHECKLIST = [
    "clear architecture & module boundaries", "input validation & error handling", "performance & profiling hot paths",
    "caching & efficient data access", "memory and resource usage", "test coverage (unit + integration)",
    "CI config & reproducible builds", "structured logging & health checks", "security hardening & dependency hygiene",
    "accessibility & UX polish", "first-run experience & setup docs", "public API / CLI ergonomics",
    "configuration & sane defaults", "DB migrations / schema versioning", "Dockerfile & deployment docs",
    "documentation & examples", "contributor guide & license", "internationalisation where useful",
]
LENSES = [
    "performance & efficiency", "code quality & clean architecture", "first-run experience for everyone",
    "reliability & error handling", "security & privacy", "accessibility & UX polish",
    "testing & CI", "documentation & examples", "extensibility & integrations", "simplicity & removing complexity",
]

# --------------------------------------------------------------------------- Helpers


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n // 2] + "\n...[truncated]...\n" + s[-n // 2:]


def tail(s: str, n: int) -> str:
    return s if len(s) <= n else "...[truncated]...\n" + s[-n:]


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def last_seq(path: Path) -> int:
    """Number (n) of the newest recorded call; survives compaction, which removes old rows from the file."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - 200_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            try:
                return int(json.loads(line)["n"])
            except (ValueError, KeyError, TypeError):
                continue
    except OSError:
        pass
    return 0


def compact_thoughts(path: Path, keep: int = 150, max_bytes: int = 4_000_000) -> bool:
    """Moves old calls out of thoughts.jsonl into thoughts-archive/<first>-<last>.jsonl.gz (gzip, prompts cut to 1500 chars)
    so the live file and the UI stay fast. The newest `keep` calls stay; the full reasoning and answers are preserved."""
    try:
        if path.stat().st_size < max_bytes // 8 and sum(1 for _ in open(path, "rb")) <= keep * 2:
            return False
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return False
    if len(lines) <= keep:
        return False
    old, new = lines[:-keep], lines[-keep:]
    packed, nums = [], []
    for line in old:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        row["prompt"] = (row.get("prompt") or "")[-1500:]
        nums.append(row.get("n", 0))
        packed.append(json.dumps(row, ensure_ascii=False))
    if not packed:
        return False
    arch = path.parent / "thoughts-archive"
    arch.mkdir(exist_ok=True)
    with gzip.open(arch / f"{min(nums):06d}-{max(nums):06d}.jsonl.gz", "wt", encoding="utf-8") as g:
        g.write("\n".join(packed) + "\n")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(new) + "\n", "utf-8")
    os.replace(tmp, path)
    return True


def aider_tokens(out: str) -> tuple[int, int]:
    """(sent, received) summed over all "Tokens: 1.2k sent, 345 received." lines of an Aider run."""
    def num(t: str) -> int:
        t = t.replace(",", "").lower()
        return int(float(t[:-1]) * 1000) if t.endswith("k") else int(float(t))
    sent = recv = 0
    for m in re.finditer(r"Tokens:\s*([\d.,]+k?) sent,\s*([\d.,]+k?) received", out):
        sent, recv = sent + num(m.group(1)), recv + num(m.group(2))
    return sent, recv


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def parse_json(text: str):
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return dec.raw_decode(text[i:])[0]
            except ValueError:
                continue
    raise ValueError("no JSON found")


def sh(cmd, cwd: Path, timeout: int = 900, shell: bool = False, env: dict | None = None):
    """Runs a command. Returns (returncode, stdout+stderr)."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), shell=shell, capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s: {cmd}"
    except FileNotFoundError as e:
        return 127, f"Command not found: {e}"


def sh_live(cmd, cwd: Path, timeout: int, live: Path, env: dict | None = None, shell: bool = False, gate=None, idle: bool = False,
            hard_cap: float = 6 * 3600):
    """Like sh(), but mirrors the output into `live` while the command runs so the UI can show it as it is written.
    idle=True: `timeout` only counts silence (a model that is still writing is never cut off); `hard_cap` still ends a runaway."""
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", shell=shell,
                             env={**os.environ, **(env or {})})
    except FileNotFoundError as e:
        return 127, f"Command not found: {e}"
    chunks: list[str] = []
    last_out = [time.time()]

    def pump() -> None:
        with open(live, "w", encoding="utf-8") as f:
            for line in p.stdout:
                last_out[0] = time.time()
                chunks.append(line)
                f.write(line)
                f.flush()

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    t0 = time.time()
    while True:
        try:
            p.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            pass
        waited = time.time() - (last_out[0] if idle else t0)
        if waited > timeout or (idle and time.time() - t0 > hard_cap):
            force_stop_pid(p.pid, grace=2.0)
            th.join(2)
            return 124, f"TIMEOUT after {int(waited)}s{' without output' if idle else ''}: {cmd}"
        if gate is not None:
            try:
                gate.check()
            except gpusched.Preempted:
                force_stop_pid(p.pid, grace=2.0)
                th.join(2)
                p.poll()
                return gpusched.PREEMPTED_RC, "PREEMPTED"
    th.join(5)
    return p.returncode, "".join(chunks)


def _children() -> dict[int, list[int]]:
    tree: dict[int, list[int]] = {}
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                ppid = int((d / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            tree.setdefault(ppid, []).append(int(d.name))
    return tree


def process_tree(pid: int) -> list[int]:
    """pid plus all its descendants (Linux /proc), children last-to-first."""
    tree, order, todo = _children(), [], [pid]
    while todo:
        cur = todo.pop()
        order.append(cur)
        todo.extend(tree.get(cur, []))
    return order


def force_stop_pid(pid: int, grace: float = 3.0) -> list[int]:
    """Terminates pid and everything it started (aider, builds, tests). SIGTERM first, SIGKILL after `grace` seconds."""
    pids = [x for x in process_tree(pid) if x != os.getpid()]
    try:  # the run.sh wrapper would otherwise keep a tmux pane or log open
        parent = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        if b"run.sh" in Path(f"/proc/{parent}/cmdline").read_bytes():
            pids.append(parent)
    except (OSError, ValueError, IndexError):
        pass
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 1.0)):
        for x in pids:
            try:
                os.kill(x, sig)
            except OSError:
                pass
        end = time.time() + wait
        while time.time() < end and any(Path(f"/proc/{x}").exists() and not _zombie(x) for x in pids):
            time.sleep(0.1)
    return pids


def _zombie(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return True


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z_]{4,}", text.lower())}


IDEAS_TEMPLATE = """# Ideas and directives for this project

<!-- The agent reads this file in every iteration. When you change something, it re-plans: it adds tasks, changes
priorities, cancels outdated open tasks and refactors existing code where needed. In addition, every few
merges it checks whether the product still matches your directives. Everything here is binding until it is deleted.
Comments like this one and empty sections are ignored. Editable at any time, even while the agent is running. -->

Start idea: {idea}

## Wishes and new features
<!-- e.g. - Export invoices as PDF, with a custom logo -->

## How it should be implemented
<!-- e.g. - Render server-side with Jinja, no JavaScript framework
     - Every route gets a test; monetary amounts always as integer cents -->

## Refactoring wishes
<!-- e.g. - Move database access into its own module
     - Switch auth from session cookies to tokens -->

## Do not
<!-- e.g. - do not integrate any external payment providers -->
"""


MASTER_TEMPLATE = """# Master prompt (applies to ALL projects)

<!-- Global directives that the agent treats as binding in every session (every `run`) for every project.
The current content is picked up when a session starts; changes take effect from the next start.
e.g. - Answer and document in English
     - Prefer simple, readable solutions over clever tricks
     - Every new function needs tests; no new dependencies without justification -->
"""


def read_master(path: Path) -> str:
    """Reads MASTER.md without comments and headings; empty if there is no real content."""
    try:
        text = re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.S)
    except OSError:
        return ""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    if not [l for l in lines if not l.lstrip().startswith("#")]:
        return ""
    return "\n".join(lines)


SOURCES_TEMPLATE = """# Sources the agent should read

<!-- One URL per line, optionally followed by a note on what to use it for. The agent fetches these pages
(again after web.refresh_hours), summarizes what matters for your product and uses it when planning and coding.
Examples:
https://stripe.com/docs/api   how to model subscriptions
https://htmx.org/docs/        use this for interactivity instead of a JS framework
Lines starting with # and comments are ignored. Web access is configured in agent/config.json ("web"). -->
"""


def read_sources(path: Path) -> list[tuple[str, str]]:
    """Returns [(url, note)] from SOURCES.md, ignoring comments and headings, without duplicates."""
    try:
        text = re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.S)
    except OSError:
        return []
    out, seen = [], set()
    for line in text.splitlines():
        m = re.search(r"https?://[^\s<>\"')\]]+", line)
        if line.lstrip().startswith("#") or not m or m.group(0) in seen:
            continue
        seen.add(m.group(0))
        out.append((m.group(0), (line[:m.start()] + line[m.end():]).strip(" -*:\t")))
    return out


def read_ideas(path: Path) -> str:
    """Reads IDEAS.md without comments. Empty if it only contains headings and the start idea."""
    try:
        text = re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.S)
    except OSError:
        return ""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    if not [l for l in lines if not l.lstrip().startswith("#") and not l.startswith("Start idea:")]:
        return ""
    return "\n".join(lines)


# --------------------------------------------------------------------------- LLM


def _strip_think(text: str) -> str:
    """Removes finished <think>...</think> blocks and an unfinished trailing one."""
    return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S)


def _open_think(text: str) -> str:
    """Reasoning text found inside <think> tags so far (shown live)."""
    return "".join(re.findall(r"<think>(.*?)(?:</think>|$)", text, flags=re.S))
