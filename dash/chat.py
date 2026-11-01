"""Chat backend for the web UI (standard library only): persistent chats that can be linked to projects.

Chats live in projects/.ui/chats/<id>.json. Linked projects give the model their goal, architecture, backlog and recent
activity as context. Answers are streamed (SSE) from the same OpenAI-compatible endpoint the agent uses.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.request

from dash import toolchat
from infra import knowledge
from pathlib import Path
from collections.abc import Callable, Iterator

CHAT_ID = re.compile(r"^[a-f0-9]{12}$")
MAX_MESSAGES = 200
CONTEXT_CHARS = 6000   # per linked project
HISTORY_CHARS = 24000  # conversation part sent to the model
MAX_USER_CHARS = 20000

SYSTEM = ("You are the assistant inside CodeFactory, a local autonomous coding agent. You help the user think through ideas, "
          "architecture and plans, and answer questions about their projects. Be concrete and concise. When the user wants "
          "something built, propose a clear task description they can send to the agent. Answer in the user's language.")

PROJECT_ADDON = ("You cannot change files yourself. When the user wants something changed or built in this project, discuss it briefly, "
                 "then propose exactly one self-contained task as a fenced block starting with ```task and ending with ```: 2-6 concrete "
                 "sentences with what to do and how to check it. The user sends it to the project's agent with one button, which plans, "
                 "implements, tests and merges it. Never claim something is already changed.")

FILE_LOCK = threading.Lock()


class ChatError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "\n[...]"


class ChatStore:
    def __init__(self, projects: Path, project_ok: Callable[[str], bool]):
        self.dir = projects / ".ui" / "chats"
        self.project_ok = project_ok

    def path(self, cid: str) -> Path:
        if not CHAT_ID.match(cid or ""):
            raise ChatError("Chat not found", 404)
        return self.dir / f"{cid}.json"

    def load(self, cid: str) -> dict:
        p = self.path(cid)
        if not p.exists():
            raise ChatError("Chat not found", 404)
        return json.loads(p.read_text("utf-8"))

    def save(self, chat: dict) -> None:
        with FILE_LOCK:
            self.dir.mkdir(parents=True, exist_ok=True)
            p = self.path(chat["id"])
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(chat, ensure_ascii=False), "utf-8")
            tmp.replace(p)

    def clean_links(self, names) -> list[str]:
        out: list[str] = []
        for n in names or []:
            if not isinstance(n, str) or not self.project_ok(n):
                raise ChatError(f"Unknown project: {n}")
            if n not in out:
                out.append(n)
        return out

    def list(self) -> dict:
        items = []
        for f in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if self.dir.exists() else []:
            try:
                c = json.loads(f.read_text("utf-8"))
                items.append({"id": c["id"], "title": c["title"], "projects": c["projects"], "model": c["model"],
                              "messages": len(c["messages"]), "updated": c["updated"], "tool": bool(c.get("tool")), "project": c.get("project", "")})
            except (ValueError, KeyError):
                continue
        return {"chats": items}

    def create(self, body: dict, default_model: str) -> dict:
        chat = {"id": secrets.token_hex(6), "title": str(body.get("title") or "New chat")[:80],
                "projects": self.clean_links(body.get("projects")),
                "model": str(body.get("model") or default_model), "messages": [], "updated": time.time()}
        if body.get("tool"):
            chat["tool"] = True
        if body.get("project") and body["project"] in chat["projects"]:
            chat["project"] = body["project"]
        self.save(chat)
        return chat

    def project_chat(self, name: str, default_model: str) -> dict:
        """The chat that belongs to one project (created on first use)."""
        for f in self.dir.glob("*.json") if self.dir.exists() else []:
            try:
                c = json.loads(f.read_text("utf-8"))
            except ValueError:
                continue
            if c.get("project") == name:
                return c
        return self.create({"title": f"{name} chat", "projects": [name], "project": name}, default_model)

    def tool_chat(self, default_model: str) -> dict:
        """The one chat about CodeFactory itself (created on first use)."""
        for f in self.dir.glob("*.json") if self.dir.exists() else []:
            try:
                c = json.loads(f.read_text("utf-8"))
            except ValueError:
                continue
            if c.get("tool"):
                return c
        return self.create({"title": "CodeFactory assistant", "tool": True}, default_model)

    def update(self, cid: str, body: dict) -> dict:
        chat = self.load(cid)
        if "projects" in body:
            chat["projects"] = self.clean_links(body["projects"])
        if body.get("model"):
            chat["model"] = str(body["model"])[:100]
        if body.get("title"):
            chat["title"] = str(body["title"])[:80]
        if "web" in body:
            chat["web"] = bool(body["web"])
        self.save(chat)
        return chat

    def delete(self, cid: str) -> None:
        self.path(cid).unlink(missing_ok=True)


def project_context(ws: Path) -> str:
    """Compact picture of one project for the system prompt."""
    def read(p: Path, n: int) -> str:
        return clip(p.read_text("utf-8", "replace"), n) if p.exists() else ""
    a = ws / "agent"
    parts = [f"## Project `{ws.name}`", "Goal:\n" + read(a / "GOAL.md", 1500)]
    arch = read(ws / "repo" / "ARCHITECTURE.md", 1500) or read(ws / "repo" / "README.md", 1500)
    if arch:
        parts.append("Architecture/README:\n" + arch)
    try:
        backlog = json.loads((a / "backlog.json").read_text("utf-8"))
        parts.append("Backlog:\n" + "\n".join(f"- [{i['status']}] {i['title']}" for i in backlog[:25]))
    except (OSError, ValueError, KeyError):
        pass
    ideas = read(ws / "IDEAS.md", 800)
    if ideas:
        parts.append("Ideas and feedback:\n" + ideas)
    log = read(a / "agent.log", 100_000)[-700:]
    if log:
        parts.append("Recent agent log:\n" + log)
    return clip("\n\n".join(parts), CONTEXT_CHARS)


def knowledge_context(ws: Path, query: str, api: str, key: str, embed_model: str = "nomic-embed-text") -> str:
    """Hits from the project's knowledge index (code, decisions, lessons, web sources) for the user's question."""
    kb = knowledge.open_readonly(ws / "agent" / "memory" / "knowledge.db")
    if not kb:
        return ""
    try:
        if any(v["embedded"] for v in kb.stats().values()):
            kb.embed = knowledge.ollama_embedder(api, embed_model, key, timeout=20)
        hits = kb.search(query, None, 6)
    except Exception:  # noqa: BLE001  (a broken index must never break the chat)
        return ""
    finally:
        kb.close()
    lines = []
    for h in hits:
        src = h["meta"].get("url") or h["meta"].get("file") or h["meta"].get("item") or ""
        lines.append(f"- [{h['kind']}{' ' + str(src) if src else ''}] {clip(h['text'].replace(chr(10), ' '), 450)}")
    return ("Knowledge base hits for the latest question:\n" + "\n".join(lines)) if lines else ""


def build_messages(chat: dict, ws_of: Callable[[str], Path | None], api: str = "", key: str = "", tool_ctx: Callable[[str], str] | None = None, web_ctx: str = "") -> list[dict]:
    last = next((m["content"] for m in reversed(chat["messages"]) if m["role"] == "user"), "")
    ctx = []
    for w in (ws_of(n) for n in chat["projects"]):
        if w:
            k = knowledge_context(w, last, api, key) if last else ""
            ctx.append(project_context(w) + ("\n\n" + k if k else ""))
    if chat.get("tool") and tool_ctx:
        system = toolchat.SYSTEM + "\n\n" + tool_ctx(last)
    else:
        system = SYSTEM + ("\n\nLinked projects (current state):\n\n" + "\n\n".join(ctx) if ctx else "")
    if chat.get("project"):
        system += "\n\n" + PROJECT_ADDON
    if web_ctx:
        system += "\n\n" + web_ctx
    kept, used = [], 0
    for m in reversed(chat["messages"]):
        used += len(m["content"])
        if used > HISTORY_CHARS and kept:
            break
        kept.append({"role": m["role"], "content": m["content"]})
    return [{"role": "system", "content": system}] + kept[::-1]


def stream_completion(api: str, key: str, model: str, messages: list[dict], timeout: int = 600) -> Iterator[str]:
    """Yields text deltas from an OpenAI-compatible streaming endpoint."""
    req = urllib.request.Request(
        api, data=json.dumps({"model": model, "messages": messages, "stream": True, "temperature": 0.5}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
            except (ValueError, KeyError, IndexError):
                continue
            if delta:
                yield delta
