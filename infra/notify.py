"""Optional webhook notifications (ntfy, Slack, Discord, Home Assistant, ...): one small JSON POST per event, never blocking the agent."""
from __future__ import annotations

import json
import threading
import urllib.request

EVENTS = ("done", "blocked", "stalled", "stopped")


def valid_url(url: str) -> bool:
    return url.startswith(("http://", "https://")) and len(url) < 500 and " " not in url


def _post(url: str, payload: dict) -> None:
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:  # noqa: BLE001  # a dead webhook must never disturb the agent
        pass


def send(cfg: dict, event: str, project: str, text: str, wait: bool = False) -> bool:
    """Posts {title, text, content, event, project} to notify.webhook if that event is enabled. Returns True if a request was started."""
    n = cfg.get("notify") or {}
    url = str(n.get("webhook") or "")
    if not url or not valid_url(url) or event not in (n.get("events") or EVENTS):
        return False
    payload = {"title": f"CodeFactory: {project}", "text": f"{project}: {text}", "content": f"{project}: {text}", "event": event, "project": project}
    t = threading.Thread(target=_post, args=(url, payload), daemon=True)
    t.start()
    if wait:
        t.join(10)
    return True
