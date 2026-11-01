"""Hand-offs between pipeline steps: each step tells the next one what was decided and why, what to do, what to avoid and when it is done,
so the reasoning of an earlier step is not lost when the next step starts with a fresh prompt."""
from __future__ import annotations

from core.cfutil import clip

LIST_MAX = 6
ITEM_CHARS = 260
BLOCK_CHARS = 1300


def render(to: str, frm: str, goal: str = "", decided=(), do=(), do_not=(), done_when=(), open_=()) -> str:
    """The text block a step passes on. Empty sections are left out; lists and items are bounded."""
    def sec(title: str, rows) -> list[str]:
        rows = [" ".join(str(r).split()) for r in rows if str(r).strip()][:LIST_MAX]
        return [f"{title}:"] + [f"  - {clip(r, ITEM_CHARS)}" for r in rows] if rows else []
    lines = [f"INSTRUCTIONS from the {frm} step to the {to} step"]
    if goal:
        lines.append(f"Goal: {clip(' '.join(goal.split()), ITEM_CHARS)}")
    lines += sec("Decided, with reasons (keep these unless the evidence says otherwise)", decided)
    lines += sec("Do", do) + sec("Do not", do_not) + sec("Done when", done_when) + sec("Still open", open_)
    return clip("\n".join(lines), BLOCK_CHARS)


class HandoffMixin:
    def handoff(self, to: str, frm: str, **parts) -> None:
        """Pins the instructions for the next step: it is put in front of every coder prompt and every model call until replaced."""
        self.pinned = render(to, frm, **{("open_" if k == "open" else k): v for k, v in parts.items()})
        self.note("handoff", f"{frm} -> {to}: " + clip(" | ".join(str(x) for v in parts.values() for x in ([v] if isinstance(v, str) else v)), 300))

    def handoff_text(self) -> str:
        p = getattr(self, "pinned", "")
        return p + "\n\n" if p else ""
