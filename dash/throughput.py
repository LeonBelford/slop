"""Throughput figures from agent/metrics.json (written by core.cfllm): tokens, words, generation speed and GPU share."""


def perf(m: dict) -> dict:
    gen, wall = float(m.get("gen_seconds", 0) or 0), float(m.get("wall_seconds", 0) or 0)
    wall = max(wall, gen)
    tokens, words = int(m.get("tokens", 0) or 0), int(m.get("words", 0) or 0)
    return {"calls": int(m.get("calls", 0) or 0), "tokens": tokens, "prompt_tokens": int(m.get("prompt_tokens", 0) or 0), "words": words,
            "day_tokens": int(m.get("day_tokens", 0) or 0),
            "gen_minutes": round(gen / 60, 1), "wall_minutes": round(wall / 60, 1),
            "tps": round(tokens / gen, 1) if gen >= 1 else None,  # speed while the model is generating
            "wps": round(words / gen, 1) if gen >= 1 else None,
            "tokens_per_min": round(tokens / (wall / 60)) if wall >= 30 else None,  # averaged over the time the agent was working
            "avg_call_s": round(gen / m["calls"], 1) if m.get("calls") else None,
            "gpu_share": round(min(gen / wall, 1.0), 2) if wall >= 30 else None}  # share of working time the model was generating
