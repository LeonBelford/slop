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
    "list_tasks": ('args: "filter": optional status (open, doing, done, blocked); task ids, titles, status and priority', True, False),
    "show_task": ('args: "task": id. Full detail of one task: description, acceptance, notes, why it is blocked', True, False),
    "read_file": ('args: "path", optional "start" and "end" line numbers: a file of the project repo with line numbers (long files in parts)', True, False),
    "list_dir": ('args: optional "path": the files and folders of a repo folder (default: the repo root)', True, False),
    "read_docs": ("all important .md files of the project (goal, README, architecture, docs/): read them before you change code", True, False),
    "health": ("health score of the project: tests, module size, drift, blockers", True, False),
    "timeline": ("the latest events of the project (what happened, newest first)", True, False),
    "add_task": ('args: "text": queue a task for the agent (it plans, builds, tests and merges it)', True, True),
    "start": ("start the agent", True, True),
    "stop": ("stop the agent after the current task", True, True),
    "pause": ("pause the running agent between tasks", True, True),
    "resume": ("resume a paused agent", True, True),
    "step_mode": ('args: "on": true or false. The agent waits after every task when on', True, True),
    "run_next": ('args: "task": task id such as T0012. Make that task the next one', True, True),
    "set_priority": ('args: "task": id, "priority": a number or "top"', True, True),
    "reopen_task": ('args: "task": id. Puts a blocked or cancelled task back into the queue', True, True),
    "delete_task": ('args: "task": id. Removes a blocked or cancelled task for good', True, True),
    "edit_task": ('args: "task": id, "title" and/or "text": new title or description of an open or blocked task', True, True),
    "feedback": ('args: "text": a remark for the project\'s ideas/feedback list; the agent considers it when it plans', True, True),
    "design": ('args: "approve": true or false. Answers the design (spec) that waits for review', True, True),
    "run_command": ('args: "command": shell command in a throwaway copy of the repo (tests, linters, scripts)', True, True),
    "cancel_task": ('args: "task": id. Cancels a task that is not wanted anymore', True, True),
    "hint": ('args: "text": an instruction pinned into the prompts of the current task', True, True),
    "generate_tests": ('args: "file": queue a task that writes tests for that source file', True, True),
    "write_note": ('args: "text", optional "file" (default notes.md): a project note kept outside the repo, always available', True, True),
    "make_folder": ('args: "path": create an (empty) folder in the project repo, for example "docs/notes"', True, True),
    "write_file": ('args: "path", "content": write a file straight into the repo, skipping plan/build/test. Only works if '
                   '"Let chat write files directly" is on in Settings', True, True),
    "edit_file": ('args: "path", "old", "new": replace the text "old" (must occur exactly once) with "new" in a repo file; commits', True, True),
    "delete_file": ('args: "path": delete a repo file; commits', True, True),
    "create_project": ('args: "idea": text, "name": optional, "template": cli, web-api or library (optional)', False, True),
}
ACTION_RE = re.compile(r"```action[ \t]*\n(.*?)```", re.S)
READ_ONLY = frozenset(n for n, d in TOOLS.items() if not d[2])

ADDON = ("You are in agent mode: you can act on the linked projects, not only talk. Look before you act: use the read-only tools (status, list_tasks, "
         "show_task, search_code, read_file, health, timeline) first; they run automatically and you get their results back, then you continue. "
         "Never invent task ids or file names. When the user wants something done, answer briefly and add one action block per step, exactly like this:\n"
         '```action\n{"tool": "add_task", "project": "<linked project name>", "text": "what to build and how to check it"}\n```\n'
         "The user confirms every action with a button; never claim it already happened. Available tools:\n"
         + "\n".join(f"- {n}: {d[0]}" + (' (needs "project")' if d[1] else "") for n, d in TOOLS.items())
         + "\nOnly use projects linked to this chat. For \"build X in project Y\": add_task (and start if the agent is stopped).")

DIRECT_ADDON = ("You are in DIRECT mode: you work on the linked project yourself, like a pair-programming assistant, and skip the plan/build/test pipeline. "
                "Your actions run automatically and you get each result back, then you continue until the job is done. Workflow:\n"
                "1. The project's .md files are given below: read them and follow their rules. Call read_docs if you need more of them.\n"
                "2. Explore before you change anything: list_dir, search_code, read_file (with start/end for long files). Never guess file contents.\n"
                "3. Implement in small steps with edit_file (exact old text -> new text) or write_file (whole new file). Every change is committed.\n"
                "4. Check your work: run_command with the project's test or lint command, read the result and fix what fails.\n"
                "5. When done, answer with a short summary of what changed and which files.\n"
                "Notes for the user: write_note. New folders: make_folder. Add exactly the action blocks of the next step to your answer, like this:\n"
                '```action\n{"tool": "read_file", "project": "<project>", "path": "src/app.py"}\n```\n'
                "Never claim a change that you did not make with a tool. Tools:\n"
                + "\n".join(f"- {n}: {d[0]}" + (' (needs "project")' if d[1] else "") for n, d in TOOLS.items() if n not in ("add_task", "create_project"))
                + "\nOnly use projects linked to this chat. Pipeline tools (add_task, start, ...) exist too if the user explicitly asks for the pipeline.")


def parse(text: str) -> list[dict]:
    """The action blocks of a model answer that are valid JSON objects, in order. The UI numbers its cards with the same list."""
    out = []
    for m in ACTION_RE.finditer(text or ""):
        try:
            c = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(c, dict):
            out.append(c)
    return out


def extract(text: str) -> list[dict]:
    """Valid action blocks of a model answer (unknown tools or missing arguments are dropped)."""
    out = []
    for c in parse(text):
        try:
            validate(c, None)
        except (ValueError, TypeError):
            continue
        out.append(c)
    return out


def results_text(msg: dict) -> str:
    """What happened to the action blocks of a stored answer, as text for the model's next turn."""
    calls, acts = parse(msg.get("content", "")), msg.get("acts") or {}
    lines = []
    for k, c in enumerate(calls):
        a = acts.get(str(k)) or {}
        state = a.get("status", "not run yet")
        lines.append(f"- {describe(str(c.get('tool')), c)} -> {state}" + (f": {a['result'][:1500]}" if a.get("result") else ""))
    return ("\n\n[Results of your actions]\n" + "\n".join(lines)) if lines else ""


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
            "create_project": "idea", "read_file": "path", "cancel_task": "task", "show_task": "task", "reopen_task": "task",
            "delete_task": "task", "feedback": "text", "run_command": "command", "write_note": "text", "make_folder": "path",
            "write_file": "path", "edit_file": "path", "delete_file": "path", "list_dir": ""}.get(tool)
    if need and not str(args.get(need) or "").strip():
        raise ValueError(f"'{need}' is missing")
    if tool == "edit_task" and not (str(args.get("title") or "").strip() or str(args.get("text") or "").strip()):
        raise ValueError("'title' or 'text' is missing")
    if tool == "write_file" and not str(args.get("content") or "").strip():
        raise ValueError("'content' is missing")
    if tool == "edit_file" and (not str(args.get("old") or "") or "new" not in args):
        raise ValueError("'old' and 'new' are needed")
    if tool == "design" and not isinstance(args.get("approve"), bool):
        raise ValueError('"approve" must be true or false')
    if tool == "set_priority" and args.get("priority") != "top":
        try:
            float(args.get("priority"))
        except (TypeError, ValueError):
            raise ValueError('"priority" must be a number or "top"') from None
    return tool, args


def describe(tool: str, args: dict) -> str:
    detail = args.get("text") or args.get("command") or args.get("idea") or args.get("query") or args.get("task") or args.get("file") or args.get("path") or ""
    return f"{tool}" + (f" on {args['project']}" if args.get("project") else "") + (f": {str(detail)[:200]}" if detail else "")
