"""Git and backlog helpers of one project."""
from __future__ import annotations

from pathlib import Path
import difflib
from core.cfutil import now, read_json, sh, write_json


# --------------------------------------------------------------------------- Git


class Git:
    """Thin wrapper around the `git` CLI for one project repo (small, deliberately dependency-free)."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.id = ["-c", "user.name=codefactory", "-c", "user.email=agent@local"]

    def run(self, *args, timeout: int = 120):
        return sh(["git", *self.id, *args], self.repo, timeout)

    def out(self, *args) -> str:
        return self.run(*args)[1].strip()

    def ensure(self) -> None:
        if not (self.repo / ".git").exists():
            self.repo.mkdir(parents=True, exist_ok=True)
            self.run("init", "-q")
            self.run("symbolic-ref", "HEAD", "refs/heads/main")

    def sha(self) -> str:
        return self.out("rev-parse", "HEAD")

    def commit_all(self, msg: str) -> bool:
        self.run("add", "-A")
        if not self.out("status", "--porcelain"):
            return False
        return self.run("commit", "-q", "-m", msg)[0] == 0

    def hard_reset(self, ref: str = "HEAD") -> None:
        self.run("reset", "--hard", ref)
        self.run("clean", "-fdq")

    def branch_from_main(self, branch: str) -> None:
        self.hard_reset()
        self.run("checkout", "-q", "main")
        self.run("checkout", "-q", "-B", branch, "main")

    def changed_files(self, base: str = "main") -> list[str]:
        out = self.out("diff", "--name-only", base)
        untracked = self.out("ls-files", "--others", "--exclude-standard")
        return sorted({f for f in (out + "\n" + untracked).splitlines() if f.strip()})

    def diff(self, base: str = "main") -> str:
        self.run("add", "-A")
        return self.run("diff", "--cached", base)[1]

    def merge_ff(self, branch: str) -> bool:
        self.run("checkout", "-q", "main")
        return self.run("merge", "--ff-only", "-q", branch)[0] == 0

    def churn(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for line in self.out("log", "--name-only", "--pretty=format:").splitlines():
            if line.strip():
                c[line.strip()] = c.get(line.strip(), 0) + 1
        return c


# --------------------------------------------------------------------------- Backlog


CHAIN_BONUS = 1.5  # a task that continues the chain of the last finished task goes first among equals
UNLAYERED = 50  # items without a layer are scheduled after every planned layer


class Backlog:
    """The project's task queue (`agent/backlog.json`): add, dedupe, prioritise and pick the next item."""

    def __init__(self, path: Path):
        self.path = path
        self.items: list[dict] = read_json(path, [])

    def save(self) -> None:
        write_json(self.path, self.items)

    def titles(self) -> list[str]:
        """Titles of every backlog item, used for duplicate checks and prompt context."""
        return [i["title"] for i in self.items]

    def is_dup(self, title: str, extra: list[str] | None = None) -> bool:
        """True if `title` is a near-duplicate (fuzzy match) of an existing or `extra` title."""
        t = title.lower().strip()
        for other in self.titles() + (extra or []):
            if difflib.SequenceMatcher(None, t, other.lower().strip()).ratio() > 0.78:
                return True
        return False

    def add(self, title, description, type_="feature", priority=5.0, acceptance=None, rationale="",
            source="ideation", extra_titles=None, force=False, layer=None, depends_on=None, why=""):
        """Appends a new backlog item and saves, or returns `None` if it looks like a duplicate (unless `force`)."""
        if not force and self.is_dup(title, extra_titles):
            return None
        item = {"id": f"T{max([int(i["id"][1:]) for i in self.items if str(i.get("id", ""))[1:].isdigit()] + [0]) + 1:04d}", "title": title, "description": description,
                "type": type_, "priority": float(priority), "status": "pending", "attempts": 0,
                "acceptance": acceptance or [], "rationale": rationale, "source": source,
                "created": now(), "notes": []}
        if layer is not None:
            item["layer"] = int(layer)
        if depends_on:
            item["depends_on"] = list(depends_on)
        if why:
            item["why"] = str(why)[:200]
        self.items.append(item)
        self.save()
        return item

    def reload(self) -> None:
        """Merges changes made from outside (web UI: new, cancelled or re-prioritised tasks) keeping the item objects in use."""
        disk = read_json(self.path, None)
        if not isinstance(disk, list):
            return
        mine = {i["id"]: i for i in self.items}
        merged = []
        for d in disk:
            cur = mine.get(d.get("id"))
            if cur is None:
                merged.append(d)
            else:
                cur.clear()
                cur.update(d)
                merged.append(cur)
        self.items[:] = merged

    def pending(self) -> list[dict]:
        """Backlog items still waiting to be picked up."""
        return [i for i in self.items if i["status"] == "pending"]

    deadlock = False

    def by_id(self, id_: str) -> dict | None:
        return next((i for i in self.items if i["id"] == id_), None)

    def ready(self, pending: list[dict]) -> list[dict]:
        """Pending items whose dependencies are finished (done or cancelled)."""
        return [i for i in pending if all(self.satisfied(d) for d in i.get("depends_on", []))]

    def satisfied(self, dep_id: str, depth: int = 0) -> bool:
        """A dependency is met when it is done. A cancelled one counts as met only if nothing replaced it: a split task hands over to its
        last slice, a merged one to the task it was merged into."""
        it = self.by_id(dep_id)
        if it is None or depth > 6:
            return False
        if it["status"] == "done":
            return True
        if it["status"] != "cancelled":
            return False
        heir = (it.get("split_into") or [None])[-1] if it.get("split_into") else it.get("merged_into")
        return self.satisfied(heir, depth + 1) if isinstance(heir, str) and heir != dep_id else True

    def dead_deps(self, item: dict) -> list[str]:
        """Dependencies that were cancelled without a replacement: the task was planned around work that will never exist."""
        out = []
        for d in item.get("depends_on", []):
            it = self.by_id(d)
            if it and it["status"] == "cancelled" and not it.get("split_into") and not it.get("merged_into"):
                out.append(d)
        return out

    def next(self, prefer_light: bool | None = None, chain_from: str | None = None):
        """User tasks first, then bugs. After that the lowest layer first (core before features, items without a layer come last),
        then by priority; items wait for their dependencies. Among near-equal priorities the task that suits the model already
        loaded (light = docs/tests, else regular) wins, which saves model switches on a small GPU. A task that builds directly on
        `chain_from` (the last finished task) gets a bonus: the agent stays inside one graph while the code is still in its head."""
        self.reload()
        p = self.pending()
        if not p:
            return None
        ready = self.ready(p)
        self.deadlock = not ready  # strictly sequential: only a real deadlock (a blocked dependency) may break the order
        # layer barrier: no higher layer (ui, features) starts while a lower one (foundation, base functions) still has open work
        low = min((i["layer"] for i in p if i.get("layer") is not None), default=None)
        if low is not None and ready:
            held = [i for i in ready if i.get("source") == "user" or i["type"] == "bug" or (i.get("layer") is not None and i["layer"] <= low)]
            ready = held or ready
        p = ready or p

        def key(i):
            light = i["type"] in ("docs", "test")
            bonus = 0.75 if prefer_light is not None and light == prefer_light else 0
            layer = i["layer"] if i.get("layer") is not None else UNLAYERED
            chain = CHAIN_BONUS if chain_from and chain_from in (i.get("depends_on") or []) else 0
            return (i.get("source") == "user", i["type"] == "bug", -layer, bool(i.get("hot")), i["priority"] + bonus + chain + i.get("dyn", 0))
        return max(p, key=key)

    def set(self, item: dict, **kw) -> None:
        """Updates `item`'s fields in place and persists the backlog."""
        item.update(kw)
        if kw.get("status") in ("blocked", "cancelled") and not item.get("reason"):
            item["reason"] = (item.get("notes") or ["no reason recorded"])[-1]  # every block/cancel says why
        elif kw.get("status") in ("pending", "in_progress", "done"):
            item.pop("reason", None)
        self.save()
