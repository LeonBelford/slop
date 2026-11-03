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


def _dependencies(data: dict) -> list[dict]:
    out = []
    for d in data.get("dependencies") or []:
        if isinstance(d, dict) and d.get("name"):
            out.append({"name": _s(d["name"], 60), "version": _s(d.get("version"), 30), "reason": _s(d.get("reason"), 160)})
    return out[:15]


def _foundation(data: dict, layer: dict, arch: dict, deps: list[dict], skeleton: bool, contracts: bool) -> list[dict]:
    """Tasks that must open layer 0: chosen dependencies, then the walking skeleton, then the contracts as code."""
    tasks = [t for t in layer.get("tasks", []) if isinstance(t, dict) and t.get("title")]
    pre = []
    if deps:
        pre.append({"key": "deps", "title": "Declare and install the chosen dependencies",
                    "description": "Add exactly these dependencies to the dependency manifest of the stack and make build and tests pass: "
                                   + "; ".join(f"{d['name']} {d['version']} ({d['reason']})" for d in deps) + ". Do not invent other versions.",
                    "acceptance": ["manifest lists the dependencies", "build and tests are green"], "type": "feature", "value": 10, "effort": 2})
    if skeleton:
        flagged = [t for t in tasks if t.get("skeleton")]
        if flagged:
            tasks.remove(flagged[0])
            pre.append(flagged[0])
        else:
            pre.append({"key": "skeleton", "skeleton": True, "title": "Walking skeleton: one end-to-end path",
                        "description": "Build the thinnest path through the whole product: input -> logic -> storage -> output, with one test "
                                       "that runs it end to end. Minimal code, real wiring, no extras.",
                        "acceptance": ["the end-to-end test passes"], "type": "feature", "value": 10, "effort": 4})
    if contracts and arch.get("contracts"):
        pre.append({"key": "contracts", "title": "Contracts as code: signatures, types, stubs and contract tests",
                    "description": "Turn these contracts into real signatures, types and stubs and write contract tests in tests/contracts/ "
                                   "that later work must not break: " + " | ".join(arch["contracts"]),
                    "acceptance": ["tests/contracts/ exists and passes"], "type": "feature", "value": 9, "effort": 3})
    return pre + tasks


def normalize(data: dict, mvp: list[dict] | None = None, chain: bool = True, skeleton: bool = False, deps_task: bool = False,
              contracts_task: bool = False) -> dict:
    """Cleans the model's answer: bounded sizes, unique keys, dependencies only on earlier tasks, every MVP feature present."""
    arch = data.get("architecture") if isinstance(data.get("architecture"), dict) else {}
    out_arch = {"principles": [_s(x) for x in arch.get("principles", []) if x][:8],
                "modules": [{"name": _s(m.get("name"), 60), "responsibility": _s(m.get("responsibility")),
                             "uses": [_s(u, 60) for u in (m.get("uses") or []) if isinstance(u, str)][:8]}
                            for m in arch.get("modules", []) if isinstance(m, dict) and m.get("name")][:12],
                "contracts": [_s(x) for x in arch.get("contracts", []) if x][:10],
                "decisions": [_s(x) for x in arch.get("decisions", []) if x][:8]}
    layers, seen, last = [], set(), ""
    deps_list = _dependencies(data)
    raw = [dict(l) for l in data.get("layers", []) if isinstance(l, dict)]
    if raw:
        raw[0]["tasks"] = _foundation(data, raw[0], out_arch, deps_list if deps_task else [], skeleton, contracts_task)
    for li, layer in enumerate(raw):
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
            if chain and last and last not in deps:
                deps.append(last)  # strictly sequential: every task builds on its direct predecessor
            last = key
            seen.add(key)
            tasks.append({"key": key, "title": _s(t["title"], 120), "description": _s(t.get("description"), 800),
                          "acceptance": [_s(a) for a in (t.get("acceptance") or []) if a][:6], "depends_on": deps, "why": _s(t.get("why"), 200),
                          "type": t.get("type") if t.get("type") in ("feature", "refactor", "test", "docs") else "feature",
                          "risk": "high" if str(t.get("risk", "")).lower() == "high" else "low", "skeleton": bool(t.get("skeleton")),
                          "priority": _num(t.get("value"), 5) * 2 - _num(t.get("effort"), 5)})
        if tasks:
            layers.append({"name": _s(layer.get("name"), 60) or f"Layer {li}", "goal": _s(layer.get("goal")), "tasks": tasks})
    if layers and mvp:
        titles = [t["title"].lower() for ly in layers for t in ly["tasks"]]
        for f in mvp:
            title = _s(f.get("title"), 120)
            if title and not any(difflib.SequenceMatcher(None, title.lower(), t).ratio() > 0.6 for t in titles):
                layers[-1]["tasks"].append({"key": f"mvp{len(titles)}", "title": title, "description": _s(f.get("description"), 800),
                                            "acceptance": [_s(a) for a in (f.get("acceptance") or []) if a][:6],
                                            "depends_on": [last] if chain and last else [], "type": "feature", "priority": _num(f.get("value"), 5) * 2 - _num(f.get("effort"), 5)})
                titles.append(title.lower())
                last = f"mvp{len(titles) - 1}"
    return {"architecture": out_arch, "layers": layers, "dependencies": deps_list}


def as_layers(raw) -> list[dict]:
    """Layers as the model wrote them: a plain name ("Core") counts as a layer without a goal, anything else unusable is dropped."""
    return [dict(ly) if isinstance(ly, dict) else {"name": ly.strip()} for ly in (raw if isinstance(raw, list) else []) if isinstance(ly, dict) or (isinstance(ly, str) and ly.strip())]


def normalize_reevaluation(data: dict, items: list[dict]) -> dict:
    """Cleans a re-evaluation answer against the real backlog `items`: layers, per-task layer/priority, new tasks and cancellations.

    Layer numbers are clamped, unknown ids dropped; cancelling tasks that came from the user (or IDEAS.md) is ignored.
    """
    base = normalize({"architecture": data.get("architecture"), "layers": [
        {"name": ly.get("name"), "goal": ly.get("goal"), "tasks": [{"title": "-"}]} for ly in as_layers(data.get("layers"))]})
    layers = [{"name": ly["name"], "goal": ly["goal"]} for ly in base["layers"]]
    if not layers:
        return {"architecture": base["architecture"], "layers": [], "assign": {}, "new": [], "cancel": [], "merge": [], "split": [], "keep": False}
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
            assign[a["id"]] = {"layer": lay(a.get("layer")), "priority": _num(a.get("priority"), 5), "why": _s(a.get("why"), 200),
                               "depends_on": [d for d in (a.get("depends_on") or []) if isinstance(d, str) and d != a["id"]]}
    new, keys = [], set()
    for n, t in enumerate(x for x in data.get("new_tasks", []) if isinstance(x, dict) and x.get("title")):
        key = _s(t.get("key"), 40) or f"new{n}"
        keys.add(key)
        new.append({"key": key, "title": _s(t["title"], 120), "description": _s(t.get("description"), 800),
                    "acceptance": [_s(a) for a in (t.get("acceptance") or []) if a][:6], "layer": lay(t.get("layer")), "why": _s(t.get("why"), 200),
                    "type": t.get("type") if t.get("type") in ("feature", "refactor", "test", "docs") else "feature",
                    "priority": _num(t.get("value"), 5) * 2 - _num(t.get("effort"), 5),
                    "depends_on": [d for d in (t.get("depends_on") or []) if isinstance(d, str) and (d in ids or d in keys) and d != key],
                    "files": [_s(f, 120) for f in (t.get("files") or []) if isinstance(f, str) and f.strip()][:6]})
    protected = {i["id"] for i in items if i.get("source") in ("user", "ideas")}
    cancel = []
    for c in data.get("cancel", []):
        cid = c.get("id") if isinstance(c, dict) else c
        if cid in ids and cid not in protected and cid not in cancel and isinstance(c, dict) and _s(c.get("why"), 200):
            cancel.append(cid)  # a cancellation needs a stated reason
    pending = {i["id"] for i in items if i.get("status") == "pending"} - protected
    used: set[str] = set()
    merge = []
    for m in data.get("merge", []):
        mids = [x for x in dict.fromkeys(m.get("ids") or []) if x in pending and x not in used and x not in cancel] if isinstance(m, dict) else []
        if len(mids) >= 2 and m.get("title"):
            used.update(mids)
            merge.append({"ids": mids[:5], "title": _s(m["title"], 120), "description": _s(m.get("description"), 800),
                          "acceptance": [_s(a) for a in (m.get("acceptance") or []) if a][:6], "why": _s(m.get("why"), 200)})
    split = []
    for sp in data.get("split", []):
        steps = [st for st in (sp.get("steps") or []) if isinstance(st, dict) and st.get("title")] if isinstance(sp, dict) else []
        if isinstance(sp, dict) and sp.get("id") in pending and sp["id"] not in used and sp["id"] not in cancel and len(steps) >= 2:
            used.add(sp["id"])
            split.append({"id": sp["id"], "steps": [{"title": _s(st["title"], 120), "description": _s(st.get("description"), 800),
                                                     "acceptance": [_s(a) for a in (st.get("acceptance") or []) if a][:6], "why": _s(st.get("why"), 200)} for st in steps[:4]]})
    return {"architecture": base["architecture"], "layers": layers, "assign": assign, "new": new[:20], "cancel": cancel,
            "merge": merge[:6], "split": split[:6], "assessment": _s(data.get("assessment"), 800), "keep": data.get("keep_plan") is True}


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
    if bp.get("dependencies"):
        lines += ["## Dependencies (chosen up front)", *[f"- `{d['name']}` {d['version']}: {d['reason']}" for d in bp["dependencies"]], ""]
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


def closed_layers(items: list[dict]) -> set[int]:
    """Layers whose tasks are all finished: that part of the plan is built and must not be rewritten by a re-evaluation."""
    live = [i for i in items if i["status"] != "cancelled" and i.get("layer") is not None]
    return {n for n in {i["layer"] for i in live} if all(i["status"] == "done" for i in live if i["layer"] == n)}


def open_floor(items: list[dict]) -> int:
    """The lowest layer that still has unfinished work: new work starts there, not in a layer that is already built."""
    layers = [i["layer"] for i in items if i["status"] in ("pending", "blocked", "in_progress") and i.get("layer") is not None]
    return min(layers) if layers else 0


def keep_closed(layers: list[dict], old: list[dict], closed: set[int]) -> list[dict]:
    """The new layer list with every closed layer exactly as it was; the model may only change or add layers above them."""
    out = as_layers(layers)
    for n in sorted(closed):
        if n < len(old):
            while len(out) <= n:
                out.append({"name": old[len(out)]["name"], "goal": old[len(out)].get("goal", "")})
            out[n] = {"name": old[n]["name"], "goal": old[n].get("goal", "")}
    return out


def finished_rows(items: list[dict], limit: int = 40) -> list[dict]:
    """What is already implemented, for the re-evaluation prompt: finished tasks with their layer, files and result."""
    done = [i for i in items if i["status"] == "done"]
    return [{k: v for k, v in {"id": i["id"], "layer": i.get("layer"), "title": _s(i["title"], 70), "files": [f for f in (i.get("files") or [])][:3],
                               "result": _s(i.get("result"), 90)}.items() if v not in (None, [], "")} for i in done[-limit:]]
