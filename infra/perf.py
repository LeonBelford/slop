"""Speed diagnostics and settings for local models (standard library only).

Why local agents feel slow while the GPU idles, and what this module does about it:
  * Ollama reloads a model whenever a request asks for a different context size (num_ctx). Aider and the planner used to ask for
    different sizes, so every switch cost a full reload. `aider_args` pins ONE num_ctx for Aider and `diagnose` checks that the
    model itself uses the same one.
  * A model plus a big KV cache that does not fit into 12 GB VRAM is partly computed on the CPU: the GPU waits. `diagnose` reads
    `/api/ps` and reports the CPU share together with the fix (flash attention + quantised KV cache, smaller context).
  * Models are unloaded after 5 idle minutes: `OLLAMA_KEEP_ALIVE` keeps them in VRAM (see scripts/ollama-tune.sh).

`python3 -m infra.perf` (or `make doctor`) prints the findings.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

NUM_CTX = int(os.getenv("CF_NUM_CTX", "16384"))  # one context size for everything: no reload when the caller changes
KEEP_ALIVE = os.getenv("CF_KEEP_ALIVE", "30m")
MAP_TOKENS = int(os.getenv("CF_MAP_TOKENS", "1024"))  # Aider's repo map defaults to 4096 tokens per call; the plan already names the files
CHARS_PER_TOKEN = 3.5


def aider_args(model: str, directory: Path) -> list[str]:
    """Aider options: fixed num_ctx/keep_alive for Ollama models (no reload per call) and no side requests that only cost time."""
    args = ["--no-suggest-shell-commands", "--no-detect-urls", "--map-tokens", str(MAP_TOKENS)]
    if "ollama" in model:
        directory.mkdir(parents=True, exist_ok=True)
        f = directory / "aider_models.json"
        f.write_text(json.dumps([{"name": model, "extra_params": {"num_ctx": NUM_CTX, "keep_alive": KEEP_ALIVE}}]), "utf-8")  # JSON is valid YAML
        args += ["--model-settings-file", str(f)]
    return args


def tokens_of(path: Path | str) -> int:
    try:
        return int(Path(path).stat().st_size / CHARS_PER_TOKEN)
    except OSError:
        return 0


def fit_context(edit: list[str], read: list[str], prompt: str, repo: Path, num_ctx: int = NUM_CTX, reserve: int = 5000) -> tuple[list[str], list[str], list[str]]:
    """Keeps the Aider chat inside the pinned context window: read-only files are dropped from the end (the first one is the
    run context and stays) until files + prompt + map + answer fit. Returns (edit, read, dropped). Edit files are never dropped
    silently; if they alone do not fit the caller gets them unchanged and Aider warns."""
    budget = num_ctx - reserve - MAP_TOKENS - int(len(prompt) / CHARS_PER_TOKEN)
    used = sum(tokens_of(repo / f) for f in edit)
    keep: list[str] = []
    dropped: list[str] = []
    for i, f in enumerate(read):
        t = tokens_of(f if Path(f).is_absolute() else repo / f)
        if i == 0 or used + t <= budget:
            keep.append(f)
            used += t
        else:
            dropped.append(f)
    return edit, keep, dropped


def _get(base: str, path: str, data: dict | None = None) -> dict | None:
    try:
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def analyse(ps: dict | None, shows: dict[str, dict | None], num_ctx: int = NUM_CTX) -> list[dict]:
    """Findings [{level, text}] from the Ollama state. Pure function, so it can be tested without a server."""
    out: list[dict] = []
    if ps is None:
        return [{"level": "warn", "text": "Ollama is not reachable on localhost:11434."}]
    loaded = ps.get("models", [])
    if len(loaded) > 1:
        out.append({"level": "warn", "text": f"{len(loaded)} models are loaded at the same time ({', '.join(m['name'] for m in loaded)}): "
                                             "they compete for 12 GB VRAM. Set OLLAMA_MAX_LOADED_MODELS=1."})
    for m in loaded:
        size, vram = m.get("size") or 0, m.get("size_vram") or 0
        if size and vram < size * 0.98:
            cpu = round(100 * (1 - vram / size))
            out.append({"level": "bad" if cpu >= 15 else "warn",
                        "text": f"{m['name']}: {cpu}% runs on the CPU, so the GPU waits for it. Fix: OLLAMA_FLASH_ATTENTION=1, "
                                f"OLLAMA_KV_CACHE_TYPE=q8_0, smaller CF_NUM_CTX (now {num_ctx}), or a smaller model (scripts/ollama-tune.sh)."})
        ctx = m.get("context_length")
        if ctx and ctx != num_ctx:
            out.append({"level": "warn", "text": f"{m['name']} is loaded with context {ctx}, Aider asks for {num_ctx}: every switch reloads the model. "
                                                 "Re-create the model with scripts/Modelfile.qwen-coder-14b (num_ctx must equal CF_NUM_CTX)."})
    for name, show in shows.items():
        mm = re.search(r"num_ctx\s+(\d+)", (show or {}).get("parameters", ""))
        if mm and int(mm.group(1)) != num_ctx:
            out.append({"level": "warn", "text": f"{name} has num_ctx {mm.group(1)} in its Modelfile but Aider uses {num_ctx}: "
                                                 f"model reloads on every switch. Run: ollama create {name} -f scripts/Modelfile.qwen-coder-14b"})
    if not out:
        out.append({"level": "ok", "text": "Ollama setup looks fine: one context size, no CPU offload detected."})
    return out


def diagnose(base: str | None = None, names: list[str] | None = None) -> list[dict]:
    base = base or os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
    ps = _get(base, "/api/ps")
    names = names or [m["name"] for m in (ps or {}).get("models", [])]
    return analyse(ps, {n: _get(base, "/api/show", {"model": n}) for n in names})


def main() -> None:
    for f in diagnose():
        print({"ok": "✓", "warn": "!", "bad": "✗"}.get(f["level"], "-"), f["text"])


if __name__ == "__main__":
    main()
