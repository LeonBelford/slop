"""Chat agent: the chat model proposes actions as ```action {json}``` blocks; the user confirms them and the server runs them.

The model never executes anything itself. Every action is validated against this registry and against the projects linked to the
chat, so "build X in project Y" becomes concrete, checked calls such as add_task, start or set_priority.
"""
from __future__ import annotations

import json
import re

# tool -> (description with args, needs a project, changes something)
TOOLS: dict[str, tuple[str, bool, bool]] = {
    "status": ("status of the linked project: agent state, task counts, current task", True, False),
    "search_code": ('args: "query": find symbols in the project code', True, False),
    "add_task": ('args: "text": queue a task for the agent (it plans, builds, tests and merges it)', True, True),
    "start": ("start the agent", True, True),
    "stop": ("stop the agent after the current task", True, True),
    "pause": ("pause the running agent between tasks", True, True),
    "resume": ("resume a paused agent", True, True),
    "step_mode": ('args: "on": true or false. The agent waits after every task when on', True, True),
    "run_next": ('args: "task": task id such as T0012. Make that task the next one', True, True),
    "set_priority": ('args: "task": id, "priority": a number or "top"', True, True),
    "hint": ('args: "text": an instruction pinned into the prompts of the current task', True, True),
    "generate_tests": ('args: "file": queue a task that writes tests for that source file', True, True),
    "create_project": ('args: "idea": text, "name": optional, "template": cli, web-api or library (optional)', False, True),
}
ACTION_RE = re.compile(r"```action[ \t]*\n(.*?)```", re.S)

ADDON = ("You can act, not only talk. When the user wants something done, answer briefly and add ONE action block per step, exactly like this:\n"
         '```action\n{"tool": "add_task", "project": "<linked project name>", "text": "what to build and how to check it"}\n```\n'
         "The user confirms every action with a button; never claim it already happened. Available tools:\n"
         + "\n".join(f"- {n}: {d[0]}" + (' (needs "project")' if d[1] else "") for n, d in TOOLS.items())
         + "\nOnly use projects linked to this chat. For \"build X in project Y\": add_task (and start if the agent is stopped).")


def extract(text: str) -> list[dict]:
    """Valid action blocks of a model answer (invalid JSON or unknown tools are dropped)."""
    out = []
    for m in ACTION_RE.finditer(text or ""):
        try:
            c = json.loads(m.group(1))
            validate(c, None)
            out.append(c)
        except (ValueError, TypeError):
            continue
    return out


def validate(call: dict, linked: list[str] | None) -> tuple[str, dict]:
    """Returns (tool, args). Raises ValueError for an unknown tool, a missing argument or a project that is not linked to the chat."""
    if not isinstance(call, dict) or call.get("tool") not in TOOLS:
        raise ValueError("Unknown tool")
    tool = call["tool"]
    _, needs_project, _ = TOOLS[tool]
    args = {k: v for k, v in call.items() if k != "tool"}
    if needs_project:
        p = str(args.get("project") or "")
        if not p or (linked is not None and p not in linked):
            raise ValueError(f"Project '{p}' is not linked to this chat")
    need = {"search_code": "query", "add_task": "text", "hint": "text", "run_next": "task", "set_priority": "task", "generate_tests": "file",
            "create_project": "idea"}.get(tool)
    if need and not str(args.get(need) or "").strip():
        raise ValueError(f"'{need}' is missing")
    if tool == "set_priority" and args.get("priority") != "top":
        try:
            float(args.get("priority"))
        except (TypeError, ValueError):
            raise ValueError('"priority" must be a number or "top"') from None
    return tool, args


def describe(tool: str, args: dict) -> str:
    detail = args.get("text") or args.get("idea") or args.get("query") or args.get("task") or args.get("file") or ""
    return f"{tool}" + (f" on {args['project']}" if args.get("project") else "") + (f": {str(detail)[:200]}" if detail else "")
