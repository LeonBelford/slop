"""Live control of a running agent: `agent/control.json` is written by the UI and read by the agent between steps.

Fields: paused, step_mode (pause after every task), next (task id to run next), spec_review (wait for approval of every design),
hints (instructions that are pinned into every prompt until the current task is finished).
"""
from __future__ import annotations

import time
from pathlib import Path

from core.cfutil import now, read_json, write_json

DEFAULT = {"paused": False, "step_mode": False, "next": "", "spec_review": False, "hints": []}
MAX_HINTS = 5
HINT_CHARS = 400


def path(agent_dir: Path) -> Path:
    return agent_dir / "control.json"


def load(agent_dir: Path) -> dict:
    return {**DEFAULT, **(read_json(path(agent_dir), {}) or {})}


def update(agent_dir: Path, **kw) -> dict:
    """Applies a validated partial update and returns the new control state."""
    c = load(agent_dir)
    for k in ("paused", "step_mode", "spec_review"):
        if k in kw:
            c[k] = bool(kw[k])
    if "next" in kw:
        c["next"] = str(kw["next"] or "")[:20]
    if kw.get("hint"):
        text = " ".join(str(kw["hint"]).split())[:HINT_CHARS]
        c["hints"] = (c["hints"] + [{"text": text, "at": now()}])[-MAX_HINTS:]
    if kw.get("clear_hints"):
        c["hints"] = []
    write_json(path(agent_dir), c)
    return c


def hints_block(agent_dir: Path) -> str:
    h = load(agent_dir)["hints"]
    return "Instructions from the user for this task (they override your own plan):\n" + "\n".join(f"  - {x['text']}" for x in h) if h else ""


def spec_paths(agent_dir: Path) -> tuple[Path, Path]:
    return agent_dir / "spec_pending.json", agent_dir / "spec_decision.json"


def decide_spec(agent_dir: Path, approve: bool, spec: dict | None = None) -> None:
    """The UI answers a pending design: approve (optionally with edited files, signatures and edge cases) or reject (the agent re-plans)."""
    pending, decision = spec_paths(agent_dir)
    if not pending.exists():
        raise ValueError("No design is waiting for approval")
    clean = {k: [str(x)[:300] for x in (spec or {}).get(k, [])][:12] for k in ("files", "signatures", "edge_cases")} if spec else {}
    write_json(decision, {"approve": bool(approve), "spec": clean, "at": now()})


class ControlMixin:
    def control(self) -> dict:
        return load(self.dir)

    def control_wait(self) -> None:
        """Blocks while the user has paused the agent; a STOP file always ends the wait."""
        announced = False
        while self.control()["paused"] and not (self.dir / "STOP").exists():
            if not announced:
                self.log("Paused from the UI: waiting for Resume.")
                self.phase("Paused (waiting for you)", None)
                announced = True
            time.sleep(2)
        if announced:
            self.log("Resumed.")

    def control_pick(self, item):
        """A task picked with 'run next' beats the normal order once."""
        want = self.control()["next"]
        if not want:
            return item
        update(self.dir, next="")
        pick = next((i for i in self.backlog.items if i["id"] == want and i["status"] == "pending"), None)
        if pick:
            self.log(f"Run next (your choice): {pick['id']} {pick['title']}")
            return pick
        return item

    def control_after_task(self) -> None:
        """Hints only live for one task; step mode pauses after every task."""
        c = self.control()
        if c["hints"]:
            update(self.dir, clear_hints=True)
        if c["step_mode"]:
            update(self.dir, paused=True)

    def hints_text(self) -> str:
        b = hints_block(self.dir)
        return b + "\n\n" if b else ""

    def spec_gate(self, item: dict, files: list, sigs: list, edge: list) -> tuple[list, list, list]:
        """With spec_review on, the design waits for your approval or edit. Rejected: the task is retried with a note."""
        if not self.control()["spec_review"]:
            return files, sigs, edge
        pending, decision = spec_paths(self.dir)
        decision.unlink(missing_ok=True)
        write_json(pending, {"item": item["id"], "title": item["title"], "spec": {"files": files, "signatures": sigs, "edge_cases": edge}, "at": now()})
        self.phase("Waiting for your approval of the design", "plan", item["title"])
        self.log(f"Design of {item['id']} waits for your approval in the UI (Control tab).")
        waited = 0
        while not decision.exists() and not (self.dir / "STOP").exists() and waited < 1800:
            time.sleep(2)
            waited += 2
        d = read_json(decision, {}) if decision.exists() else {}
        pending.unlink(missing_ok=True)
        decision.unlink(missing_ok=True)
        if d.get("approve") is False:
            self.note("spec", "the user rejected the design; make a different one")
            return [], [], []
        s = d.get("spec") or {}
        if s:
            self.note("spec", "the user edited the design; follow it exactly")
            return s.get("files", files), s.get("signatures", sigs), s.get("edge_cases", edge)
        return files, sigs, edge
