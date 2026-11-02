"""Graph ideas (AI suggestions from a re-evaluation) and standard processes (routines you write yourself); both become task graphs."""
from __future__ import annotations

import re
from pathlib import Path

TYPES = ("feature", "refactor", "test")
MAX_IDEAS = 6


def _clean(s, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def norm_steps(raw, limit: int = 6) -> list[dict]:
    """Steps as {title, description, type, acceptance, needs (indexes of earlier steps), why}; anything unusable is dropped."""
    out: list[dict] = []
    for s in (raw if isinstance(raw, list) else [])[:limit]:
        if not isinstance(s, dict) or not _clean(s.get("title"), 120):
            continue
        typ = s.get("type") if s.get("type") in TYPES else "feature"
        acc = [_clean(a, 160) for a in (s.get("acceptance") or []) if _clean(a, 160)][:4] if isinstance(s.get("acceptance"), list) else []
        needs = sorted({n for n in (s.get("needs") or []) if isinstance(n, int) and 0 <= n < len(out)}) if isinstance(s.get("needs"), list) else []
        out.append({"title": _clean(s.get("title"), 120), "description": _clean(s.get("description"), 600) or _clean(s.get("title"), 120),
                    "type": typ, "acceptance": acc, "needs": needs, "why": _clean(s.get("why"), 160)})
    return out


def norm_ideas(raw, known_titles: set[str], dismissed: list[str]) -> list[dict]:
    """AI graph ideas from a re-evaluation: at least two steps, no repeat of an existing or dismissed title."""
    out: list[dict] = []
    seen = {t.lower() for t in known_titles} | {t.lower() for t in dismissed}
    for r in (raw if isinstance(raw, list) else [])[:MAX_IDEAS]:
        if not isinstance(r, dict):
            continue
        title, steps = _clean(r.get("title"), 100), norm_steps(r.get("steps"), 5)
        if not title or len(steps) < 2 or title.lower() in seen:
            continue
        seen.add(title.lower())
        out.append({"id": "I" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30], "title": title, "why": _clean(r.get("why"), 240), "steps": steps})
    return out


def steps_from_text(text: str, sequential: bool = True, typ: str = "refactor") -> list[dict]:
    """One step per line (`title` or `title: what to do`); sequential steps build on each other."""
    steps: list[dict] = []
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        if not line:
            continue
        title, _, desc = line.partition(":")
        steps.append({"title": title.strip()[:120], "description": (desc.strip() or title.strip())[:600], "type": typ if typ in TYPES else "refactor",
                      "acceptance": [], "needs": [len(steps) - 1] if sequential and steps else [], "why": ""})
        if len(steps) >= 8:
            break
    return steps


def add_graph(backlog, steps: list[dict], rationale: str, source: str, why: str = "") -> list[dict]:
    """Adds the steps as one graph (each step depends on the earlier steps it needs). Returns the created items."""
    out: list[dict] = []
    for s in steps:
        item = backlog.add(s["title"], s.get("description") or s["title"], s.get("type", "feature"), 4.5, (s.get("acceptance") or []) + ["Build and tests green"], rationale, source, force=True,
                           depends_on=[out[j]["id"] for j in s.get("needs", []) if j < len(out)], why=s.get("why") or why)
        if item:
            out.append(item)
    return out


def load_ideas(path: Path, read) -> dict:
    d = read(path, {})
    return {"ideas": d.get("ideas", []) if isinstance(d, dict) else [], "dismissed": d.get("dismissed", []) if isinstance(d, dict) else []}


def norm_process(raw: dict, pid: str) -> dict:
    """A standard process: a name, its steps (text, one per line), whether the steps follow each other, and how often it runs by itself (every N merged tasks, 0 = only on click)."""
    name = _clean(raw.get("name"), 60)
    steps = steps_from_text(raw.get("steps", ""), bool(raw.get("sequential", True)), str(raw.get("type", "refactor")))
    if not name or not steps:
        raise ValueError("A process needs a name and at least one step")
    every = raw.get("every", 0)
    return {"id": pid, "name": name, "steps": str(raw.get("steps", ""))[:2000], "sequential": bool(raw.get("sequential", True)),
            "type": str(raw.get("type", "refactor")) if raw.get("type") in TYPES else "refactor", "every": max(0, min(int(every), 500)) if isinstance(every, int) else 0}


def process_steps(p: dict) -> list[dict]:
    return steps_from_text(p.get("steps", ""), p.get("sequential", True), p.get("type", "refactor"))
