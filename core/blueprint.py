"""Architecture blueprint: a layered build plan (core first, features on top) kept in `agent/blueprint.json`.

Layer 0 is the kernel everything else needs (data model, core logic, interfaces, error handling, test harness). Each
further layer builds only on the public interfaces of the layers below. Backlog items carry `layer` and `depends_on`, and
`Backlog.next` works through the layers from the bottom up instead of hopping between features.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from core.cfutil import clip, read_json, write_json

MAX_LAYERS = 6
MAX_TASKS = 8


def _s(v, n: int = 300) -> str:
    return str(v or "").strip()[:n]


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize(data: dict, mvp: list[dict] | None = None) -> dict:
    """Cleans the model's answer: bounded sizes, unique keys, dependencies only on earlier tasks, every MVP feature present."""
    arch = data.get("architecture") if isinstance(data.get("architecture"), dict) else {}
    out_arch = {"principles": [_s(x) for x in arch.get("principles", []) if x][:8],
                "modules": [{"name": _s(m.get("name"), 60), "responsibility": _s(m.get("responsibility"))}
                            for m in arch.get("modules", []) if isinstance(m, dict) and m.get("name")][:12],
                "contracts": [_s(x) for x in arch.get("contracts", []) if x][:10],
                "decisions": [_s(x) for x in arch.get("decisions", []) if x][:8]}
    layers, seen = [], set()
    for li, layer in enumerate(l for l in data.get("layers", []) if isinstance(l, dict)):
        if li >= MAX_LAYERS:
            break
        tasks = []
        for ti, t in enumerate(x for x in layer.get("tasks", []) if isinstance(x, dict) and x.get("title")):
            if ti >= MAX_TASKS:
                break
            key = _s(t.get("key"), 40) or f"l{li}t{ti}"
            if key in seen:
                key = f"l{li}t{ti}"
            deps = [d for d in (t.get("depends_on") or []) if isinstance(d, str) and d in seen and d != key]
            seen.add(key)
            tasks.append({"key": key, "title": _s(t["title"], 120), "description": _s(t.get("description"), 800),
                          "acceptance": [_s(a) for a in (t.get("acceptance") or []) if a][:6], "depends_on": deps,
                          "type": t.get("type") if t.get("type") in ("feature", "refactor", "test", "docs") else "feature",
                          "priority": _num(t.get("value"), 5) * 2 - _num(t.get("effort"), 5)})
        if tasks:
            layers.append({"name": _s(layer.get("name"), 60) or f"Layer {li}", "goal": _s(layer.get("goal")), "tasks": tasks})
    if layers and mvp:
        titles = [t["title"].lower() for ly in layers for t in ly["tasks"]]
        for f in mvp:
            title = _s(f.get("title"), 120)
            if title and not any(difflib.SequenceMatcher(None, title.lower(), t).ratio() > 0.6 for t in titles):
                layers[-1]["tasks"].append({"key": f"mvp{len(titles)}", "title": title, "description": _s(f.get("description"), 800),
                                            "acceptance": [_s(a) for a in (f.get("acceptance") or []) if a][:6], "depends_on": [],
                                            "type": "feature", "priority": _num(f.get("value"), 5) * 2 - _num(f.get("effort"), 5)})
                titles.append(title.lower())
    return {"architecture": out_arch, "layers": layers}


def normalize_reevaluation(data: dict, items: list[dict]) -> dict:
    """Cleans a re-evaluation answer against the real backlog `items`: layers, per-task layer/priority, new tasks and cancellations.

    Layer numbers are clamped, unknown ids dropped; cancelling tasks that came from the user (or IDEAS.md) is ignored.
    """
    base = normalize({"architecture": data.get("architecture"), "layers": [
        {"name": ly.get("name"), "goal": ly.get("goal"), "tasks": [{"title": "-"}]} for ly in data.get("layers", []) if isinstance(ly, dict)]})
    layers = [{"name": ly["name"], "goal": ly["goal"]} for ly in base["layers"]]
    if not layers:
        return {"architecture": base["architecture"], "layers": [], "assign": {}, "new": [], "cancel": []}
    ids = {i["id"] for i in items}
    top = len(layers) - 1

    def lay(v) -> int:
        try:
            return max(0, min(int(v), top))
        except (TypeError, ValueError):
            return top

    assign = {}
    for a in data.get("assign", []):
        if isinstance(a, dict) and a.get("id") in ids:
            assign[a["id"]] = {"layer": lay(a.get("layer")), "priority": _num(a.get("priority"), 5),
                               "depends_on": [d for d in (a.get("depends_on") or []) if isinstance(d, str) and d != a["id"]]}
    new, keys = [], set()
    for n, t in enumerate(x for x in data.get("new_tasks", []) if isinstance(x, dict) and x.get("title")):
        key = _s(t.get("key"), 40) or f"new{n}"
        keys.add(key)
        new.append({"key": key, "title": _s(t["title"], 120), "description": _s(t.get("description"), 800),
                    "acceptance": [_s(a) for a in (t.get("acceptance") or []) if a][:6], "layer": lay(t.get("layer")),
                    "type": t.get("type") if t.get("type") in ("feature", "refactor", "test", "docs") else "feature",
                    "priority": _num(t.get("value"), 5) * 2 - _num(t.get("effort"), 5),
                    "depends_on": [d for d in (t.get("depends_on") or []) if isinstance(d, str) and (d in ids or d in keys) and d != key]})
    protected = {i["id"] for i in items if i.get("source") in ("user", "ideas")}
    cancel = []
    for c in data.get("cancel", []):
        cid = c.get("id") if isinstance(c, dict) else c
        if cid in ids and cid not in protected and cid not in cancel:
            cancel.append(cid)
    return {"architecture": base["architecture"], "layers": layers, "assign": assign, "new": new[:20], "cancel": cancel,
            "assessment": _s(data.get("assessment"), 800)}


def load(path: Path) -> dict | None:
    bp = read_json(path, None)
    return bp if isinstance(bp, dict) and bp.get("layers") else None


def save(path: Path, bp: dict) -> None:
    write_json(path, bp)


def clamp_layer(bp: dict | None, value) -> int | None:
    """A layer index from the model, kept inside the blueprint; anything else (or no blueprint) means 'on top of everything'."""
    if not bp:
        return None
    try:
        return max(0, min(int(value), len(bp["layers"])))
    except (TypeError, ValueError):
        return len(bp["layers"])


def progress(bp: dict | None, items: list[dict]) -> list[dict]:
    """Per layer: name, goal and how many of its tasks are done (cancelled tasks do not count), plus one row for extension work."""
    if not bp:
        return []
    rows = []
    for n, ly in enumerate(bp["layers"] + [{"name": "Extensions", "goal": "Ideas, reviews and your wishes on top of the plan"}]):
        mine = [i for i in items if i.get("layer") == n and i["status"] != "cancelled"]
        if mine or n < len(bp["layers"]):
            rows.append({"layer": n, "name": ly["name"], "goal": ly.get("goal", ""), "done": sum(i["status"] == "done" for i in mine), "total": len(mine)})
    return rows


def render_md(bp: dict) -> str:
    """Human-readable version for docs/BLUEPRINT.md."""
    a = bp.get("architecture", {})
    lines = ["# Architecture blueprint", "", "Built bottom-up: each layer uses only the public interfaces of the layers below.", ""]
    if bp.get("assessment"):
        lines += ["## Assessment of the previous plan", bp["assessment"], ""]
    for title, key in (("Principles", "principles"), ("Contracts (stable interfaces)", "contracts"), ("Decisions", "decisions")):
        if a.get(key):
            lines += [f"## {title}", *[f"- {x}" for x in a[key]], ""]
    if a.get("modules"):
        lines += ["## Modules", *[f"- **{m['name']}**: {m['responsibility']}" for m in a["modules"]], ""]
    for i, ly in enumerate(bp["layers"]):
        lines += [f"## Layer {i}: {ly['name']}", ly.get("goal", ""), "", *[f"- {t['title']}" for t in ly["tasks"]], ""]
    return "\n".join(lines)


def context_for(bp: dict | None, item: dict, limit: int = 2500) -> str:
    """Prompt text that places one task inside the blueprint (empty for items without a layer or projects without a blueprint)."""
    layer = item.get("layer")
    if not bp or layer is None:
        return ""
    layers = bp["layers"]
    a = bp.get("architecture", {})
    here = f"Layer {layer}: {layers[layer]['name']}" if layer < len(layers) else "above the planned layers (extension)"
    parts = ["Architecture blueprint (binding): this project is built bottom-up in layers.", f"This task belongs to {here}."]
    if layer < len(layers) and layers[layer].get("goal"):
        parts.append("Goal of this layer: " + layers[layer]["goal"])
    below = [f"{i}: {ly['name']}" for i, ly in enumerate(layers[:layer])]
    if below:
        parts.append("Already built below (use their public interfaces, do not reach into their internals, do not change their contracts "
                     "unless this task says so): " + "; ".join(below))
    above = [ly["name"] for ly in layers[layer + 1:]]
    if above:
        parts.append("Planned for later (do not build it now, but do not block it): " + "; ".join(above))
    for title, key in (("Principles", "principles"), ("Contracts", "contracts")):
        if a.get(key):
            parts.append(f"{title}: " + " | ".join(a[key]))
    return clip("\n".join(parts), limit)
