"""Context window auto-tuner: reads what Ollama and the GPU offer and recommends `num_ctx` per model.

A too small window (Ollama's default is often 4096) makes every long prompt blind. The recommendation fits the KV cache
into the VRAM that is left after the model weights; it is an estimate, so it stays below the limit with a safety margin.
It can write a Modelfile and the `ollama create` command for a ready-made variant such as `qwen2.5-coder:14b-ctx12288`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

STEPS = (4096, 6144, 8192, 12288, 16384, 24576, 32768)
OVERHEAD_MB = 1200  # CUDA context, compute buffers, the desktop
KV_MB_PER_B_PARAMS = 0.0125  # rough KV cache cost per token and billion parameters (grouped-query attention models)


def _get(url: str, body: dict | None = None, timeout: float = 5.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  (local Ollama URL from the config)
        return json.loads(r.read().decode("utf-8"))


def vram_mb() -> tuple[int, int] | None:
    """(total, used) in MB of the first NVIDIA GPU, None without nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
        total, used = (int(x) for x in out.split(","))
        return total, used
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def recommend(weights_mb: float, params_b: float, total_vram_mb: int | None, max_ctx: int = 32768) -> dict:
    """The largest standard window whose KV cache fits next to the weights. Without a GPU reading: 8192 as a safe default."""
    if not total_vram_mb:
        return {"num_ctx": min(8192, max_ctx), "reason": "no GPU reading (nvidia-smi); 8192 is a safe default"}
    free = total_vram_mb - weights_mb - OVERHEAD_MB
    per_tok = max(0.03, params_b * KV_MB_PER_B_PARAMS)
    fit = int(free / per_tok) if free > 0 else 0
    fits = [s for s in STEPS if s <= fit and s <= max_ctx]
    if not fits:
        return {"num_ctx": 4096, "reason": f"only ~{max(0, int(free))} MB VRAM left after the weights: stay at 4096 or use a smaller or more quantized model"}
    return {"num_ctx": fits[-1], "reason": f"{int(free)} MB VRAM left after weights and overhead, ~{per_tok:.2f} MB per token of KV cache fits ~{fit} tokens"}


def modelfile(model: str, num_ctx: int, temperature: float = 0.2) -> str:
    if not re.fullmatch(r"[\w.:/-]{1,100}", model) or not 1024 <= int(num_ctx) <= 131072:
        raise ValueError("Invalid model name or num_ctx")
    return f"FROM {model}\nPARAMETER num_ctx {int(num_ctx)}\nPARAMETER temperature {temperature}\n"


def variant_name(model: str, num_ctx: int) -> str:
    return f"{model}-ctx{num_ctx}"


def write_modelfile(directory: Path, model: str, num_ctx: int) -> dict:
    """Writes scripts/Modelfile.<model>-ctx<N>; returns the path and the command that builds the variant."""
    text = modelfile(model, num_ctx)
    path = directory / ("Modelfile." + re.sub(r"[^\w.-]", "_", variant_name(model, num_ctx)))
    path.write_text(text, "utf-8")
    return {"path": str(path), "command": f"ollama create {variant_name(model, num_ctx)} -f {path}", "variant": variant_name(model, num_ctx)}


def detect(base: str = "http://localhost:11434") -> dict:
    """Installed models with size, parameter count and maximum window, the loaded window, and the GPU memory. Errors are reported, not raised."""
    gpu = vram_mb()
    out: dict = {"gpu": {"total_mb": gpu[0], "used_mb": gpu[1]} if gpu else None, "models": [], "error": ""}
    try:
        tags = _get(base + "/api/tags").get("models", [])
        loaded = {m.get("name"): m for m in _get(base + "/api/ps").get("models", [])}
    except (OSError, ValueError) as e:
        out["error"] = f"Ollama not reachable at {base}: {e}"
        return out
    for t in tags[:30]:
        name = t.get("name", "")
        info, current = {}, 0
        try:
            info = _get(base + "/api/show", {"name": name})
        except (OSError, ValueError):
            pass
        mi = info.get("model_info") or {}
        max_ctx = next((int(v) for k, v in mi.items() if k.endswith(".context_length") and isinstance(v, int)), 32768)
        m = re.search(r"num_ctx\s+(\d+)", info.get("parameters") or "")
        if m:
            current = int(m.group(1))
        if name in loaded:
            current = int(loaded[name].get("context_length") or current)
        params_b = float(re.sub(r"[^\d.]", "", str((info.get("details") or {}).get("parameter_size") or "0")) or 0)
        weights_mb = int(t.get("size", 0) / 1e6)
        rec = recommend(weights_mb, params_b, gpu[0] if gpu else None, max_ctx)
        out["models"].append({"name": name, "weights_mb": weights_mb, "params_b": params_b, "max_ctx": max_ctx, "current_ctx": current or 4096,
                              "loaded": name in loaded, **{"recommended": rec["num_ctx"], "why": rec["reason"]}})
    return out
