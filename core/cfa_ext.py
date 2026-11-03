"""Agent mixin that groups the control and doc-sync hooks (kept out of codefactory.py to respect the module size cap)."""
from __future__ import annotations

from core.cfutil import clip, write_text
from core.control import ControlMixin
from dash import docsync


class DocSyncMixin:
    def doc_sync(self, layer: int, name: str) -> None:
        """After a finished layer: README (model), docs/API.md and a layer heading in the existing CHANGELOG.md. Never blocks the build."""
        if not self.feat("doc_sync"):
            return
        self.phase("Syncing README, API docs and changelog", "docs", f"layer {layer}")
        try:
            goal = (self.dir / "GOAL.md").read_text("utf-8") if (self.dir / "GOAL.md").exists() else ""
            self.mem.flush_docs()
            self.mem.refresh_readme(goal)
            write_text(self.repo / "docs" / "API.md", docsync.api_markdown(self.mem.codemap))
            docsync.mark_layer(self.repo, layer, name)
            self.git.commit_all(f"docs: sync after layer {layer}")
        except Exception as e:  # noqa: BLE001  (documentation must never stop the build)
            self.log(f"Doc sync skipped: {clip(str(e), 200)}")


class ExtMixin(ControlMixin, DocSyncMixin):
    pass
