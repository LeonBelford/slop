"""Optional model catalog and routing for CodeFactory (standard library only).

Nothing here is required: the agent works with the models from config.json / .env. When extra models from this
catalog are installed in Ollama they are detected and used automatically:

  light          small, fast coder for simple tasks (docs, tests, tiny edits, cleanup)
  standard       the configured default coder (config.json `coder_model`)
  heavy          stronger coder for complex tasks and for retries after a failed attempt
  final_reviewer strongest available model for the last independent review of every change

Sizes are approximate 4-bit downloads. Check names with `ollama list`; the library is at https://ollama.com/library.
"""
from __future__ import annotations

CATALOG = [
    # name, tier, approx. size in GB, hardware note (12 GB VRAM / 16 GB RAM), what it is good for
    {"name": "qwen2.5-coder:1.5b", "tier": "light", "gb": 1.0, "fit": "fast on any GPU", "note": "tiny edits, docs, formatting"},
    {"name": "qwen2.5-coder:3b", "tier": "light", "gb": 1.9, "fit": "fast on any GPU", "note": "small edits, tests, docs"},
    {"name": "qwen2.5-coder:7b", "tier": "light", "gb": 4.7, "fit": "fully in 12 GB VRAM, fast", "note": "simple features, tests, cleanup"},
    {"name": "qwen2.5-coder:14b", "tier": "standard", "gb": 9.0, "fit": "fully in 12 GB VRAM", "note": "all-round default coder"},
    {"name": "deepseek-coder-v2:16b", "tier": "standard", "gb": 8.9, "fit": "fits 12 GB VRAM (MoE, fast)", "note": "alternative all-rounder, many languages"},
    {"name": "codestral:22b", "tier": "heavy", "gb": 12.9, "fit": "partly offloaded to RAM, slower", "note": "strong code generation, 80+ languages"},
    {"name": "devstral:24b", "tier": "heavy", "gb": 14.0, "fit": "partly offloaded to RAM, slower", "note": "built for multi-file agentic coding"},
    {"name": "qwen2.5-coder:32b", "tier": "heavy", "gb": 20.0, "fit": "mostly in RAM, slow on 16 GB", "note": "best qwen2.5 coder, complex refactors"},
    {"name": "qwen3-coder:30b", "tier": "heavy", "gb": 19.0, "fit": "MoE (3B active): usable with offload, tight on 16 GB RAM", "note": "strong agentic coder, long context"},
    {"name": "deepseek-r1:14b", "tier": "reviewer", "gb": 9.0, "fit": "fully in 12 GB VRAM", "note": "reasoning model: good final reviewer, slow, too chatty for coding"},
    {"name": "phi4:14b", "tier": "reviewer", "gb": 9.1, "fit": "fully in 12 GB VRAM", "note": "good at reasoning and critique"},
]

# Preference order per role; the first installed model wins. The reviewer list starts with the strongest models.
PREFERENCE = {
    "light": ["qwen2.5-coder:7b", "qwen2.5-coder:3b", "qwen2.5-coder:1.5b"],
    "heavy": ["qwen3-coder:30b", "devstral:24b", "qwen2.5-coder:32b", "codestral:22b"],
    "final_reviewer": ["qwen3-coder:30b", "devstral:24b", "qwen2.5-coder:32b", "codestral:22b", "deepseek-r1:14b", "phi4:14b"],
}


def _norm(name: str) -> str:
    return name.removesuffix(":latest")


def is_installed(name: str, installed: set[str]) -> bool:
    return _norm(name) in {_norm(i) for i in installed}


def resolve(installed: set[str], routing: dict | None = None, default_coder: str = "") -> dict:
    """Picks light/heavy/final_reviewer from explicit config first, then from what is installed. Missing -> None.

    Models equal to the default coder are dropped: switching to the model that is already loaded gains nothing.
    """
    routing = routing or {}
    if routing.get("mode", "auto") == "off":
        return {"light": None, "heavy": None, "final_reviewer": None}
    out: dict[str, str | None] = {}
    for role, prefs in PREFERENCE.items():
        explicit = str(routing.get(role) or "").strip()
        pick = explicit or next((p for p in prefs if is_installed(p, installed)), None)
        if pick and role != "final_reviewer" and _norm(pick) == _norm(default_coder.split("/", 1)[-1]):
            pick = None
        out[role] = pick
    return out


def classify(item_type: str, files: int, attempt: int, heavy_from_attempt: int = 2, complex_files: int = 4) -> str:
    """light | standard | heavy for one task, from facts the plan already contains (no extra model call)."""
    if attempt >= heavy_from_attempt:
        return "heavy"  # the cheaper model already failed once: escalate
    if item_type in ("docs", "test") or files <= 1 and item_type != "bug":
        return "light"
    if files >= complex_files or item_type == "refactor" and files >= 3:
        return "heavy"
    return "standard"


def coder_string(name: str) -> str:
    """Aider model string for an Ollama model name."""
    return name if "/" in name else f"ollama_chat/{name}"


def catalog_view(installed: set[str]) -> list[dict]:
    return [{**m, "installed": is_installed(m["name"], installed), "pull": f"ollama pull {m['name']}"} for m in CATALOG]


def installed_from_ollama(base: str = "http://localhost:11434") -> set[str] | None:
    """Names of installed Ollama models, or None if the server cannot be reached."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as r:
            return {m["name"] for m in json.loads(r.read()).get("models", [])}
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    import os
    inst = installed_from_ollama(os.getenv("OLLAMA_API_BASE", "http://localhost:11434"))
    print("Optional models (CodeFactory uses them automatically once they are installed)\n")
    if inst is None:
        print("(Ollama is not reachable, so installed models cannot be shown)\n")
    for tier in ("light", "standard", "heavy", "reviewer"):
        print(f"[{tier}]")
        for m in (x for x in CATALOG if x["tier"] == tier):
            mark = "installed" if inst is not None and is_installed(m["name"], inst) else "         "
            print(f"  {mark}  ollama pull {m['name']:<24} ~{m['gb']:>4} GB  {m['fit']}; {m['note']}")
        print()
    if inst is not None:
        print("Active routing:", resolve(inst))


if __name__ == "__main__":
    main()
