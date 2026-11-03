"""Optional webhook notifications (ntfy, Slack, Discord, Home Assistant, ...): one small JSON POST per event, never blocking the agent."""
from __future__ import annotations

import json
import threading
import urllib.request

EVENTS = ("done", "blocked", "stalled", "stopped", "report", "digest", "idle")
PRIORITY = {"done": 2, "report": 3, "digest": 3, "blocked": 4, "stalled": 4, "stopped": 4, "idle": 4}
TAGS = {"done": "white_check_mark", "blocked": "no_entry", "stalled": "warning", "stopped": "stop_button", "report": "bar_chart", "digest": "bar_chart", "idle": "hourglass"}


def valid_url(url: str) -> bool:
    return url.startswith(("http://", "https://")) and len(url) < 500 and " " not in url


def _post(url: str, payload: dict) -> None:
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:  # noqa: BLE001  # a dead webhook must never disturb the agent
        pass


def is_ntfy(n: dict) -> bool:
    return str(n.get("format") or "auto") == "ntfy" or (str(n.get("format") or "auto") == "auto" and "ntfy" in str(n.get("webhook") or "").lower())


def _post_ntfy(url: str, title: str, text: str, event: str) -> None:
    """ntfy takes the message as the plain body; title, priority and tags are headers (ASCII only)."""
    try:
        req = urllib.request.Request(url, data=text.encode("utf-8"), headers={
            "Title": title.encode("ascii", "replace").decode(), "Priority": str(PRIORITY.get(event, 3)), "Tags": TAGS.get(event, "robot")})
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:  # noqa: BLE001
        pass


def send(cfg: dict, event: str, project: str, text: str, wait: bool = False) -> bool:
    """Posts {title, text, content, event, project} to notify.webhook if that event is enabled. Returns True if a request was started."""
    n = cfg.get("notify") or {}
    url = str(n.get("webhook") or "")
    if not url or not valid_url(url) or event not in (n.get("events") or EVENTS):
        return False
    if event == "done" and float(n.get("digest_minutes", 60) or 0) > 0:
        return False  # finished tasks are part of the periodic digest, not one push each
    if is_ntfy(n):
        t = threading.Thread(target=_post_ntfy, args=(url, f"CodeFactory: {project}", text, event), daemon=True)
        t.start()
        if wait:
            t.join(10)
        return True
    payload = {"title": f"CodeFactory: {project}", "text": f"{project}: {text}", "content": f"{project}: {text}", "event": event, "project": project}
    t = threading.Thread(target=_post, args=(url, payload), daemon=True)
    t.start()
    if wait:
        t.join(10)
    return True
