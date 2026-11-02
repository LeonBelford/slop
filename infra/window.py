"""GPU time windows for the multi-project scheduler: heavy models only run inside e.g. "22:00-07:00"."""
from __future__ import annotations

import re
from datetime import datetime


def parse(spec: str) -> tuple[int, int] | None:
    """'22:00-07:00' -> (minute of day start, end). Empty or invalid: None (always allowed)."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec or "")
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    return h1 * 60 + m1, h2 * 60 + m2


def open_now(spec: str, now: datetime | None = None) -> bool:
    w = parse(spec)
    if not w:
        return True
    t = (now or datetime.now()).hour * 60 + (now or datetime.now()).minute
    a, b = w
    return a <= t < b if a <= b else (t >= a or t < b)


def pick(models: dict[str, str], heavy: str, spec: str, now: datetime | None = None) -> str:
    """Model to use: the heavy one inside the window, else the light one. `models` = {"light": ..., "heavy": ...}."""
    return models.get(heavy if open_now(spec, now) else "light", "")
