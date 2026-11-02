"""Chat API: the free-form model chat in the sidebar, optionally linked to a project for context."""
from __future__ import annotations

import time
from pathlib import Path

import codefactory as sa
from dash import chat as chatlib, chatagent, chatweb, directtools, toolchat, toolmodels
from infra.webaccess import Web

from webapi import common
from webapi.common import UiError

MAX_STEPS = 14  # model turns of one direct-route run


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

    def chat_project(self, name: str) -> dict:
        """The chat of one project; its model defaults to the project's planner."""
        self.ws(name)
        return self.chats.project_chat(name, self.chat_default_model([name]))

    def chat_web(self, chat: dict, query: str) -> tuple[str, list[dict]]:
        """Web results for the question when the chat has web search on (the first linked project's web settings apply)."""
        if not chat.get("web"):
            return "", []
        ws = next((w for w in (self.chat_ws(n) for n in chat["projects"]) if w), None)
        cfg = {**sa.DEFAULT_CONFIG["web"], **(sa.read_json(ws / "agent" / "config.json", {}).get("web", {}) if ws else {})}
        return chatweb.search_context(Web(cfg), query)

    def chat_tool(self) -> dict:
        """The assistant chat about CodeFactory itself; its model defaults to the self-improve planner choice."""
        saved = toolmodels.load(common.ROOT / "self_improve").get("planner", "")
        return self.chats.tool_chat(saved or self.chat_default_model([]))

    def tool_context(self, last_user: str) -> str:
        return toolchat.context(common.ROOT, common.ROOT / "self_improve", common.alive("tool"), last_user)

    def chat_stream(self, cid: str, text: str, write, mode: str = "") -> None:
        """Streams an answer through write(dict) and stores it (also when cut off).

        mode "" appends the user message first; "regen" drops the last answer and asks again; "continue" answers after the results of
        agent actions (nothing is appended to the visible conversation, the model is just told to go on)."""
        text = text.strip()
        chat = self.chats.load(cid)
        if mode == "regen":
            while chat["messages"] and chat["messages"][-1]["role"] == "assistant":
                chat["messages"].pop()
            if not chat["messages"]:
                raise UiError("Nothing to regenerate")
        elif mode != "continue":
            if not text:
                raise UiError("Message is empty")
            if len(text) > chatlib.MAX_USER_CHARS:
                raise UiError("Message too long")
            chat["messages"].append({"role": "user", "content": text, "t": time.time()})
            if chat["title"] == "New chat":
                chat["title"] = text.replace("\n", " ")[:50]
        chat["messages"] = chat["messages"][-chatlib.MAX_MESSAGES:]
        chat["updated"] = time.time()
        self.chats.save(chat)
        cfg = sa.DEFAULT_CONFIG
        web_ctx, sources = self.chat_web(chat, text or next((m["content"] for m in reversed(chat["messages"]) if m["role"] == "user"), ""))
        if sources:
            write({"sources": sources})
        direct = bool(chat.get("agent") and chat["projects"] and not chat.get("tool") and chat.get("route") == "direct")
        if direct and mode == "":
            self._announce_docs(chat, write)
        for step in range(MAX_STEPS if direct else 1):
            if direct:
                write({"status": "Reading the project and deciding the next step…" if step == 0 else f"Step {step + 1}/{MAX_STEPS}: continuing…"})
            msgs = chatlib.build_messages(chat, self.chat_ws, cfg["llm_api"], cfg["api_key"], self.tool_context, web_ctx)
            if mode == "continue" or step:
                msgs.append({"role": "user", "content": "The results of your actions are above. Continue: take the next step, or if the job is done, answer with a short summary."})
            answer, err = self._stream_answer(chat, msgs, write, sources)
            if err:
                try:
                    write({"error": err})
                except OSError:
                    pass
                return
            if not (direct and answer):
                return
            calls = chatagent.parse(answer)
            if not calls:
                return
            write({"status": f"Running {len(calls)} action{'s' if len(calls) != 1 else ''}…"})
            if not self._run_calls(chat, calls, write):
                return
        write({"error": f"Stopped after {MAX_STEPS} steps. Say 'continue' to go on."})

    def _announce_docs(self, chat: dict, write) -> None:
        """Direct route: the docs of the linked projects are read first; the chat shows which ones."""
        write({"status": "Reading the project's docs (GOAL, README, architecture)…"})
        names = []
        for n in chat["projects"]:
            ws = self.chat_ws(n)
            if ws:
                names += [f"{n}: {x}" for x in directtools.read_docs(ws, 8000, 2500)[1]]
        if names:
            chat["messages"].append({"role": "assistant", "content": "Read first: " + ", ".join(names), "t": time.time(), "cmd": True, "ok": True, "tool": "read_docs"})
            self.chats.save(chat)
            write({"reload": True})

    def _stream_answer(self, chat: dict, msgs: list[dict], write, sources: list) -> tuple[str, str]:
        cfg = sa.DEFAULT_CONFIG
        answer, thinking, err = "", "", ""
        try:
            for kind, d in chatlib.stream_completion(cfg["llm_api"], cfg["api_key"], chat["model"], msgs):
                if kind == "think":
                    thinking += d
                    write({"think": d})
                else:
                    answer += d
                    write({"delta": d})
        except OSError as e:  # includes a closed browser connection: keep the partial answer
            err = f"Model server not reachable or connection closed: {e}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        finally:
            if answer:
                chat["messages"].append({"role": "assistant", "content": answer, "t": time.time(), "model": chat["model"],
                                         **({"thinking": thinking} if thinking else {}), **({"sources": sources} if sources else {})})
                chat["updated"] = time.time()
                self.chats.save(chat)
        return answer, err

    def _run_calls(self, chat: dict, calls: list[dict], write) -> bool:
        """Runs the action blocks of the last answer in order and stores each result on its card. False when the browser went away."""
        msg = chat["messages"][-1]
        acts = msg.setdefault("acts", {})
        for k, call in enumerate(calls):
            try:
                tool, args = chatagent.validate(call, chat["projects"])
                acts[str(k)] = {"status": "done", "result": self._run_tool(tool, args, True), "t": time.time()}
            except (ValueError, UiError) as e:
                acts[str(k)] = {"status": "failed", "result": str(e), "t": time.time()}
            except Exception as e:  # noqa: BLE001 - a tool crash must not end the whole run
                acts[str(k)] = {"status": "failed", "result": f"{type(e).__name__}: {e}", "t": time.time()}
        self.chats.save(chat)
        try:
            write({"reload": True})
        except OSError:
            return False
        return True
