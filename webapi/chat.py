"""Chat API: the free-form model chat in the sidebar, optionally linked to a project for context."""
from __future__ import annotations

import time
from pathlib import Path

import codefactory as sa
from dash import chat as chatlib

from webapi.common import UiError


class ChatMixin:
    """Service mixin: the free-form model chat, optionally linked to a project for context."""

    def chat_ws(self, name: str) -> Path | None:
        try:
            return self.ws(name)
        except UiError:
            return None

    def chat_default_model(self, projects: list[str]) -> str:
        for n in projects:
            ws = self.chat_ws(n)
            if ws:
                return sa.Agent(ws).cfg["models"]["planner"]
        return sa.DEFAULT_CONFIG["models"]["planner"]

    def chat_list(self) -> dict:
        out = self.chats.list()
        out["installed_models"] = self.installed_models(sa.DEFAULT_CONFIG["llm_api"])
        out["default_model"] = self.chat_default_model([])
        return out

    def chat_create(self, body: dict) -> dict:
        names = self.chats.clean_links(body.get("projects"))
        return self.chats.create(body, self.chat_default_model(names))

    def chat_stream(self, cid: str, text: str, write) -> None:
        """Appends the user message, streams the answer through write(dict) and stores it (also when cut off)."""
        text = text.strip()
        if not text:
            raise UiError("Message is empty")
        if len(text) > chatlib.MAX_USER_CHARS:
            raise UiError("Message too long")
        chat = self.chats.load(cid)
        chat["messages"].append({"role": "user", "content": text, "t": time.time()})
        if chat["title"] == "New chat":
            chat["title"] = text.replace("\n", " ")[:50]
        chat["messages"] = chat["messages"][-chatlib.MAX_MESSAGES:]
        chat["updated"] = time.time()
        self.chats.save(chat)
        cfg = sa.DEFAULT_CONFIG
        msgs = chatlib.build_messages(chat, self.chat_ws, cfg["llm_api"], cfg["api_key"])
        answer, err = "", ""
        try:
            for d in chatlib.stream_completion(cfg["llm_api"], cfg["api_key"], chat["model"], msgs):
                answer += d
                write({"delta": d})
        except OSError as e:  # includes a closed browser connection: keep the partial answer
            err = f"Model server not reachable or connection closed: {e}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        finally:
            if answer:
                chat["messages"].append({"role": "assistant", "content": answer, "t": time.time(), "model": chat["model"]})
                chat["updated"] = time.time()
                self.chats.save(chat)
        if err:
            try:
                write({"error": err})
            except OSError:
                pass

