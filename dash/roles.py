"""Role editor backend: per-project overrides of temperature, reasoning and an extra prompt for each agent role.

Stored in agent/roles.json as {role: {temperature, think, prompt}}; the model of a role stays in the existing model settings; the LLM client reads it before every call, so changes
apply immediately. Resetting a role deletes its entry and the defaults from the config apply again.
"""
from __future__ import annotations

from pathlib import Path

from core.cfutil import read_json, write_json

THINK = ("default", "off")
PROMPT_MAX = 1500


def load(path: Path) -> dict:
    d = read_json(path, {})
    return d if isinstance(d, dict) else {}


def clean(data: dict) -> dict:
    """Validates one role entry; raises ValueError with a readable message."""
    out: dict = {}
    if data.get("temperature") not in (None, ""):
        try:
            t = float(data["temperature"])
        except (TypeError, ValueError):
            raise ValueError("Temperature must be a number") from None
        if not 0 <= t <= 1.5:
            raise ValueError("Temperature must be between 0 and 1.5")
        out["temperature"] = round(t, 2)
    think = str(data.get("think") or "default")
    if think not in THINK:
        raise ValueError("Reasoning must be 'default' or 'off'")
    if think != "default":
        out["think"] = think
    prompt = str(data.get("prompt") or "").strip()
    if len(prompt) > PROMPT_MAX:
        raise ValueError(f"Prompt addition is limited to {PROMPT_MAX} characters")
    if prompt:
        out["prompt"] = prompt
    return out


def save(path: Path, roles: list[str], role: str, data: dict) -> dict:
    if role not in roles:
        raise ValueError(f"Unknown role '{role}'")
    allr = load(path)
    c = clean(data)
    if c:
        allr[role] = c
    else:
        allr.pop(role, None)
    write_json(path, allr)
    return allr


def reset(path: Path, role: str | None = None) -> dict:
    allr = {} if role is None else {k: v for k, v in load(path).items() if k != role}
    write_json(path, allr)
    return allr


def apply(entry: dict, system: str, user: str, temperature: float) -> tuple[str, str, float]:
    """The call parameters after the role's overrides: (system, user, temperature)."""
    if not entry:
        return system, user, temperature
    if entry.get("prompt"):
        system += "\n\nAdditional instructions from the user for this role:\n" + entry["prompt"]
    if entry.get("think") == "off":
        user += "\n/no_think"
    return system, user, float(entry.get("temperature", temperature))
