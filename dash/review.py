"""Diff review: an inline comment on a changed line becomes a fix task (type bug, so it is built right after your own tasks)."""
from __future__ import annotations

from core.cfutil import clip

MAX_COMMENT = 1500


def comment_task(item_id: str, title: str, path: str, line: int, code: str, comment: str) -> dict:
    """The backlog entry for one review comment. Raises ValueError for an empty or oversized comment."""
    comment = comment.strip()
    if not comment or len(comment) > MAX_COMMENT:
        raise ValueError(f"The comment must have 1 to {MAX_COMMENT} characters")
    where = f"{path}:{line}" if path else "the change"
    return {"title": clip(f"Review fix for {item_id} at {where}: {comment.splitlines()[0]}", 90), "type": "bug", "priority": 20.0,
            "description": f"Review comment from the user on {item_id} ({title}), {where}.\nCode: {clip(code.strip(), 300)}\nComment: {comment}\n"
                           "Fix exactly what the comment asks. Do not change anything unrelated.",
            "acceptance": ["The comment is addressed in the code", "Build and tests green"]}
