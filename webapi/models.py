"""Ollama model catalogue API: list installed models, who uses them, pull/cancel/delete."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import codefactory as sa
from infra import models

from webapi.common import MODEL_RE, PULL_CANCEL, PULLS, UiError


class ModelsMixin:
    """Service mixin: the Ollama model catalogue (list, who uses a model, pull/cancel/delete)."""

    @staticmethod
    def installed_models(api: str) -> list[str]:
        u = urllib.parse.urlparse(api)
        try:
            with urllib.request.urlopen(f"{u.scheme}://{u.netloc}/api/tags", timeout=3) as r:
                return sorted(m["name"] for m in json.loads(r.read()).get("models", []))
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def ollama_base() -> str:
        u = urllib.parse.urlparse(sa.DEFAULT_CONFIG["llm_api"])
        return f"{u.scheme}://{u.netloc}"

    def models_overview(self) -> dict:
        try:
            with urllib.request.urlopen(self.ollama_base() + "/api/tags", timeout=3) as r:
                tags = json.loads(r.read()).get("models", [])
            ok = True
        except Exception:  # noqa: BLE001
            tags, ok = [], False
        inst = {m["name"] for m in tags}
        return {"ok": ok, "url": self.ollama_base(),
                "installed": sorted(({"name": m["name"], "gb": round(m.get("size", 0) / 1e9, 1), "used_by": self.model_users(m["name"])}
                                     for m in tags), key=lambda m: m["name"]),
                "catalog": models.catalog_view(inst), "pulls": {k: dict(v) for k, v in PULLS.items()}}

    def model_users(self, name: str) -> list[dict]:
        """Projects whose settings use this model (running ones block removal)."""
        out = []
        for d in sorted(self.projects.glob("*/agent/GOAL.md")):
            ws = d.parent.parent
            try:
                eff = sa.Agent(ws).cfg
            except Exception:  # noqa: BLE001
                continue
            used = set(eff["models"].values()) | {str(eff["coder_model"]).split("/", 1)[-1]} \
                | {str(eff["routing"].get(k, "")).split("/", 1)[-1] for k in ("light", "heavy", "final_reviewer")}
            if name in used:
                out.append({"project": ws.name, "running": self.summary(ws)["running"]})
        return out

    def model_pull(self, body: dict) -> dict:
        name = self.model_name(body.get("name"))
        if PULLS.get(name, {}).get("active"):
            raise UiError("Already downloading", 409)
        PULLS[name] = {"status": "starting", "completed": 0, "total": 0, "active": True, "error": ""}
        PULL_CANCEL.discard(name)
        threading.Thread(target=self.pull_worker, args=(name,), daemon=True).start()
        return {"ok": True}

    def pull_worker(self, name: str) -> None:
        st = PULLS[name]
        req = urllib.request.Request(self.ollama_base() + "/api/pull", data=json.dumps({"model": name, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                for raw in r:
                    if name in PULL_CANCEL:
                        st["error"] = "cancelled"
                        break
                    j = json.loads(raw)
                    if j.get("error"):
                        st["error"] = str(j["error"])[:300]
                        break
                    st["status"] = j.get("status", st["status"])
                    if j.get("total"):
                        st["total"], st["completed"] = j["total"], j.get("completed", 0)
                    if j.get("status") == "success":
                        st["status"] = "done"
        except Exception as e:  # noqa: BLE001
            st["error"] = f"Model server not reachable or download interrupted: {e}"[:300]
        st["active"] = False
        self._ms = None

    def model_cancel(self, body: dict) -> dict:
        PULL_CANCEL.add(self.model_name(body.get("name")))
        return {"ok": True}

    def model_delete(self, body: dict) -> dict:
        name = self.model_name(body.get("name"))
        busy = [u["project"] for u in self.model_users(name) if u["running"]]
        if busy:
            raise UiError(f"In use by running project(s): {', '.join(busy)}. Stop them first.", 409)
        req = urllib.request.Request(self.ollama_base() + "/api/delete", data=json.dumps({"model": name}).encode(), method="DELETE",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30).close()
        except urllib.error.HTTPError as e:
            raise UiError("Model not found" if e.code == 404 else f"Ollama refused: HTTP {e.code}", 404 if e.code == 404 else 502) from e
        except OSError as e:
            raise UiError(f"Model server not reachable: {e}", 502) from e
        PULLS.pop(name, None)
        self._ms = None
        return {"ok": True}

    @staticmethod
    def model_name(v) -> str:
        v = str(v).strip()
        if not MODEL_RE.match(v):
            raise UiError(f"Invalid model name: {v!r}")
        return v

