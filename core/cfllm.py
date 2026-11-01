"""The model client: streaming chat, reasoning capture, JSON answers."""
from __future__ import annotations

from pathlib import Path
from infra import gpusched
import json
import re
import sys
import threading
import time
import urllib.request
from core.cfutil import _open_think, _strip_think, aider_tokens, clip, compact_thoughts, last_seq, now, parse_json, read_json, tail, write_json


class LLM:
    """Thin OpenAI-compatible client. `mock(tag, role, system, user) -> str` replaces it in the selftest."""

    mock = None

    def __init__(self, cfg: dict, log):
        self.cfg, self.log = cfg, log
        self.installed: set[str] = set()  # model names reported by Ollama (set by preflight)
        self.think_cap = int(cfg.get("limits", {}).get("think_chars", 6000))
        self.cut_thinking = False
        self.overrides: dict[str, str] = {}  # role -> model chosen by routing
        self.live: Path | None = None  # file describing the model call in progress (for the UI)
        self.trace: Path | None = None  # set by Agent: every model call is appended here so you can watch the "thinking"
        self.thoughts: Path | None = None  # full, untruncated record of every call incl. the model's reasoning (thoughts.jsonl)
        self.stream: Path | None = None  # the answer being written right now, updated while it streams (llm_stream.json)
        self.sched = gpusched.NullScheduler()  # set by Agent
        self.seq = 0
        self.tok = {"tokens": 0, "chars": 0, "t0": None}
        self.ctx = lambda: ""  # returns the current task id (set by Agent)

    def record(self, tag: str, role: str, model: str, user: str, answer: str, thinking: str = "", seconds: float = 0.0) -> None:
        """Appends the complete thought (prompt, reasoning, answer) to thoughts.jsonl for the UI."""
        if not self.thoughts:
            return
        self._metrics(role, user, answer, thinking, seconds)
        if not self.seq and self.thoughts.exists():
            self.seq = last_seq(self.thoughts)
        self.seq += 1
        row = {"n": self.seq, "time": now(), "task": self.ctx(), "role": role, "tag": tag, "model": model, "seconds": round(seconds, 1),
               "prompt": user[-8000:], "thinking": thinking[-40000:], "answer": answer[:30000]}
        with open(self.thoughts, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self.seq % 25 == 0:
            compact_thoughts(self.thoughts)

    def _metrics(self, role: str, user: str, answer: str, thinking: str, seconds: float) -> None:
        """Running totals for the dashboard (agent/metrics.json): calls, tokens (counted from the stream, else estimated), characters."""
        chars = len(answer) + len(thinking)
        usage = self.tok.get("usage") or {}
        tokens = usage.get("completion_tokens") or (self.tok["tokens"] if self.tok["tokens"] and self.tok["chars"] else chars // 4)
        sent = usage.get("prompt_tokens", 0)
        if role == "coder":  # Aider prints "Tokens: 12k sent, 1.2k received." per request
            sent, tokens = aider_tokens(answer)
            chars = len(answer)
        f = self.thoughts.parent / "metrics.json"
        m = read_json(f, {})
        day = time.strftime("%Y-%m-%d")
        if m.get("day") != day:
            m.update(day=day, day_calls=0, day_tokens=0, day_chars=0)
        t = time.time()
        gap = min(t - m["last_ts"], 600) if m.get("last_ts") else seconds  # wall time covered by this call; long idle gaps are capped
        m["last_ts"] = round(t, 1)
        for k, v in (("words", len(answer.split()) + len(thinking.split())), ("wall_seconds", round(max(gap, seconds), 1)), ("calls", 1), ("prompt_tokens", sent), ("tokens", tokens), ("chars", chars), ("gen_seconds", round(seconds, 1)),
                     ("day_calls", 1), ("day_tokens", tokens), ("day_chars", chars)):
            m[k] = round(m.get(k, 0) + v, 1) if k in ("gen_seconds", "wall_seconds") else m.get(k, 0) + v
        write_json(f, m)
        self.tok = {"tokens": 0, "chars": 0, "t0": None}

    def _publish(self, tag: str, role: str, model: str, since: float, thinking: str, answer: str, stats: dict | None = None) -> None:
        if self.stream:
            write_json(self.stream, {"tag": tag, "role": role, "model": model, "since": since,
                                     "thinking": thinking[-60000:], "answer": answer[-60000:], "updated": time.time(),
                                     **(stats or {"tokens": 0, "chars": 0, "t0": None})})

    def _read(self, r, tag: str, role: str, model: str, since: float) -> tuple[str, str]:
        """Reads a streamed (SSE) or plain JSON answer. Returns (answer, reasoning); <think> blocks count as reasoning."""
        ctype = r.headers.get("Content-Type", "")
        self.tok = {"tokens": 0, "chars": 0, "t0": None}  # live counters for the dashboard: Ollama streams about one token per chunk
        if "event-stream" not in ctype:
            msg = json.loads(r.read())["choices"][0]["message"]
            content, thinking = msg.get("content") or "", msg.get("reasoning") or msg.get("reasoning_content") or ""
            self.tok.update(tokens=(len(content) + len(thinking)) // 4, chars=len(content) + len(thinking))
        else:
            content, thinking, last_pub = "", "", 0.0
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                if obj.get("usage"):
                    self.tok["usage"] = obj["usage"]
                try:
                    delta = obj["choices"][0].get("delta", {})
                except (KeyError, IndexError, TypeError):
                    continue
                piece, think_piece = delta.get("content") or "", delta.get("reasoning") or delta.get("reasoning_content") or ""
                content += piece
                thinking += think_piece
                if piece or think_piece:
                    self.tok["tokens"] += 1
                    self.tok["chars"] += len(piece) + len(think_piece)
                    self.tok["t0"] = self.tok["t0"] or time.time()
                self.sched.check()  # a side agent aborts here when the main agent needs the GPU
                if len(thinking) + len(_open_think(content)) > self.think_cap and not _strip_think(content).strip():
                    self.cut_thinking = True  # endless reasoning: stop the stream; json() asks again for a direct answer
                    break
                if time.time() - last_pub > 0.4:
                    last_pub = time.time()
                    self._publish(tag, role, model, since, thinking + _open_think(content), _strip_think(content), self.tok)
        m = re.search(r"<think>(.*?)</think>", content, re.S)
        if m:
            thinking += m.group(1)
        return _strip_think(content).strip() if m or "<think>" in content else content, thinking

    def _traced(self, tag: str, role: str, user: str, answer: str) -> str:
        if self.trace:
            with open(self.trace, "a", encoding="utf-8") as f:
                f.write(f"\n===== {now()} [{role}/{tag}] =====\n-- prompt (end) --\n{tail(user, 600)}\n-- answer --\n{clip(answer, 3000)}\n")
        return answer

    def preflight(self) -> None:
        """Fails early with a clear message if the model server is unreachable or a configured model is not installed."""
        if LLM.mock:
            return
        u = urllib.parse.urlparse(self.cfg["llm_api"])
        try:
            with urllib.request.urlopen(f"{u.scheme}://{u.netloc}/api/tags", timeout=10) as r:
                have = {m["name"] for m in json.loads(r.read()).get("models", [])}
        except urllib.error.HTTPError:
            return  # not Ollama (no /api/tags): cannot check, let the real call decide
        except Exception as e:  # noqa: BLE001
            sys.exit(f"Cannot reach the model server at {u.scheme}://{u.netloc} ({e}).\n"
                     "Start Ollama (`ollama serve`, or `systemctl start ollama`) and check LLM_API in .env.")
        self.installed = set(have)
        have |= {n.removesuffix(":latest") for n in have}
        need = {n for n in self.cfg["models"].values() if n}
        missing = sorted(n for n in need if n not in have and n.removesuffix(":latest") not in have)
        if missing:
            sys.exit(f"Model(s) not installed in Ollama: {', '.join(missing)}\nInstalled: {', '.join(sorted(have)) or 'none'}\n"
                     "Fix the names in .env (PLANNER_MODEL etc.) to match `ollama list`, or pull/create the model "
                     "(`ollama pull <name>`; scripts/setup.sh creates qwen2.5-coder-agent).")
        self.log(f"Model server OK, models available: {', '.join(sorted(need))}. "
                 "The first answer can take several minutes while the model loads (see `ollama ps`).")

    def chat(self, tag: str, role: str, system: str, user: str, temperature: float = 0.2) -> str:
        if LLM.mock:
            mocked = LLM.mock(tag, role, system, user)
            self.record(tag, role, "mock", user, mocked)
            return self._traced(tag, role, user, mocked)
        model = self.overrides.get(role) or self.cfg["models"].get(role) or self.cfg["models"]["reviewer"]
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": int(self.cfg["limits"].get("max_answer_tokens", 6000)),
            "stream": True,
            "stream_options": {"include_usage": True},  # exact token counts for the dashboard
        }).encode()
        t_start = time.time()
        last = None
        done = threading.Event()
        if self.live:
            write_json(self.live, {"role": role, "tag": tag, "model": model, "since": t_start})
        self._publish(tag, role, model, t_start, "", "")
        self.log(f"🧠 [{role}/{tag}] asking {model} ...")

        def heartbeat():
            t0 = time.time()
            while not done.wait(30):
                if self.sched.waiting:
                    continue
                self.log(f"... still waiting for the model ({role}/{tag}, {int(time.time() - t0)}s). Normal for big models; see `ollama ps`.")

        threading.Thread(target=heartbeat, daemon=True).start()
        attempt = 0
        while attempt < 4:
            try:
                req = urllib.request.Request(
                    self.cfg["llm_api"], data=body,
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.cfg["api_key"]})
                with self.sched.gpu(f"{role}/{tag}", model):
                    t_start = time.time()
                    with urllib.request.urlopen(req, timeout=self.cfg["limits"]["llm_timeout"]) as r:
                        answer, thinking = self._read(r, tag, role, model, t_start)
                done.set()
                if self.live:
                    self.live.unlink(missing_ok=True)
                if self.stream:
                    self.stream.unlink(missing_ok=True)
                self.record(tag, role, model, user, answer, thinking, time.time() - t_start)
                self.log(f"🧠 [{role}/{tag}] answered in {time.time() - t_start:.0f}s"
                         + (f", reasoned {len(thinking)} chars" if thinking else "") + f": {clip(' '.join(answer.split()), 160)}")
                return self._traced(tag, role, user, answer)
            except gpusched.Preempted:
                self._publish(tag, role, model, t_start, "", "")
                continue  # not an error: main needs the GPU, wait for it to be idle and ask again
            except Exception as e:  # network/model briefly unavailable: wait and retry
                last = e
                attempt += 1
                self.log(f"LLM error ({tag}), attempt {attempt}: {e}")
                time.sleep(5 * (attempt + 1))
        done.set()
        for f in (self.live, self.stream):
            if f:
                f.unlink(missing_ok=True)
        raise RuntimeError(f"LLM unreachable: {last}")

    def json(self, tag: str, role: str, system: str, user: str):
        system += "\nRespond ONLY with valid JSON, without any explanatory text."
        prompt = user
        for _ in range(3):
            text = self.chat(tag, role, system, prompt, temperature=0.2)
            try:
                return parse_json(text)
            except ValueError:
                prompt = user + "\n\nYour last answer was not valid JSON. Output JSON only." + (
                    " /no_think Do not reason at length: answer directly with the JSON." if self.cut_thinking else "")
                self.cut_thinking = False
        raise RuntimeError(f"Model returned no JSON ({tag})")
