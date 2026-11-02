"""Gates after merging: the layer gate (integration, smoke, strategist) and the deterministic architecture drift check."""
from __future__ import annotations

import re

from core.cfutil import clip, sh

MAX_GATE_FAILS = 2
MAX_FILE_LINES = 800
MAX_DRIFT_TASKS = 2
GATE_SCHEMA = '{"met": true, "missing": [{"title": "", "description": "exact files and behaviour"}]}'
CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go")
IMPORT_RES = (re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.M), re.compile(r"""(?:require\(|from\s+)['"]\.{0,2}/?([\w./-]+)['"]"""),
              re.compile(r"^\s*(?:pub\s+)?(?:use|mod)\s+(?:crate::|super::)?(\w+)", re.M), re.compile(r'^\s*"[\w./-]*/(\w+)"', re.M))


def module_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def imports_of(text: str) -> set[str]:
    out = set()
    for rx in IMPORT_RES:
        for m in rx.findall(text):
            out.update(module_key(p) for p in re.split(r"[./]", m) if p)
    return out


def drift(modules: list[dict], files: dict[str, str], max_lines: int = MAX_FILE_LINES) -> list[dict]:
    """Violations of the blueprint: files that are too long, and imports between modules that a module's `uses` does not allow."""
    keys = {module_key(m["name"]): m for m in modules}
    out = []
    for path, text in files.items():
        n = text.count("\n") + 1
        if n > max_lines:
            out.append({"file": path, "kind": "size", "problem": f"{n} lines (limit {max_lines}): split it into smaller modules"})
        parts = {module_key(p.rsplit(".", 1)[0]) for p in path.split("/")}
        owner = next((keys[k] for k in keys if k in parts), None)
        if owner and owner.get("uses"):
            allowed = {module_key(u) for u in owner["uses"]} | {module_key(owner["name"])}
            bad = sorted((imports_of(text) & set(keys)) - allowed)
            if bad:
                out.append({"file": path, "kind": "import", "problem": f"module '{owner['name']}' imports {', '.join(bad)}, which the blueprint does not allow"})
    return out[:20]


class GateMixin:
    def layer_gate(self, layer: int, name: str) -> bool:
        """Before climbing to the next layer: build+tests (integration), the smoke command if the project has one, and the strategist
        checks the layer goal. Red: fix tasks are queued in this layer and the layer stays open. After MAX_GATE_FAILS it passes with a note."""
        if not self.feat("layer_gate"):
            return True
        bp = self.blueprint() or {}
        goal = bp["layers"][layer].get("goal", "")
        self.phase(f"Layer gate: is layer {layer} '{name}' really done?", "build")
        ok, report = self.verify()
        smoke = self.cfg["commands"].get("smoke")
        if ok and smoke:
            rc, out = sh(smoke, self.repo, self.lim["cmd_timeout"], shell=True)
            if rc:
                ok, report = False, f"[smoke] FAILED rc={rc}\n$ {smoke}\n{clip(out, 3000)}"
        missing: list[dict] = []
        if ok:
            try:
                d = self.llm.json("layer_gate", "strategist",
                                  "A layer of the product is finished. Is its goal really met by what exists? List only what is clearly missing.",
                                  f"Vision:\n{clip(self.goal, 1500)}\nLayer {layer} '{name}' goal: {goal}\nBuilt: "
                                  f"{[f['title'] for f in self.mem.features.values()][-30:]}\nSchema: {GATE_SCHEMA}")
                missing = [m for m in (d.get("missing") or []) if isinstance(m, dict) and m.get("title")][:2] if d.get("met") is False else []
            except Exception as e:  # noqa: BLE001
                self.log(f"Layer gate review skipped: {clip(str(e), 120)}")
        else:
            missing = [{"title": f"Fix the red layer gate of layer {layer}", "description": clip(report, 700)}]
        gates = self.state.setdefault("layer_gates", {})
        rec = gates.setdefault(str(layer), {"name": name, "fails": 0})
        if not missing:
            rec.update(status="passed", note="")
            self.log(f"Layer gate {layer} passed")
            return True
        rec["fails"] += 1
        rec["note"] = clip("; ".join(m["title"] for m in missing), 300)
        if rec["fails"] > MAX_GATE_FAILS:
            rec["status"] = "passed with open problems"
            self.log(f"Layer gate {layer} failed {rec['fails']} times: climbing on, the problems stay in the report")
            return True
        rec["status"] = "failed"
        for m in missing:
            self.backlog.add(str(m["title"])[:120], str(m.get("description", ""))[:800], "bug", 9, None, f"Layer gate {layer}: {name}",
                             "gate", force=True, layer=layer)
        self.log(f"Layer gate {layer} red: {len(missing)} fix task(s) queued, not climbing yet")
        self.metric(kind="layer_gate_red", layer=layer, missing=[m["title"] for m in missing])
        return False

    def drift_check(self, item: dict) -> None:
        """Deterministic after every merge: file size and module imports against the blueprint. Violations become fix tasks."""
        if not self.feat("drift_check"):
            return
        bp = self.blueprint()
        if not bp:
            return
        try:
            files = {}
            for f in self.git.out("ls-files").splitlines():
                if f.endswith(CODE_EXT) and "test" not in f.lower() and (self.repo / f).is_file():
                    files[f] = (self.repo / f).read_text("utf-8", "replace")
            found = drift(bp.get("architecture", {}).get("modules", []), files)
        except Exception as e:  # noqa: BLE001
            self.log(f"Drift check skipped: {clip(str(e), 120)}")
            return
        self.state["drift"] = [{**v, "merge": self.state.get("merged", 0)} for v in found]
        queued = 0
        for v in found:
            if queued >= MAX_DRIFT_TASKS:
                break
            if self.backlog.add(f"Fix architecture drift in {v['file']}", v["problem"], "refactor", 8, None, "Drift check", "drift",
                                layer=item.get("layer")):
                queued += 1
        if found:
            self.log(f"Drift check: {len(found)} violation(s), {queued} fix task(s) queued")
