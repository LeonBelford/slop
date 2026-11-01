"""Git and backlog helpers of one project."""
from __future__ import annotations

from pathlib import Path
import difflib
from core.cfutil import now, read_json, sh, write_json


# --------------------------------------------------------------------------- Git


class Git:
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


class Backlog:
    def __init__(self, path: Path):
        self.path = path
        self.items: list[dict] = read_json(path, [])

    def save(self) -> None:
        write_json(self.path, self.items)

    def titles(self) -> list[str]:
        return [i["title"] for i in self.items]

    def is_dup(self, title: str, extra: list[str] | None = None) -> bool:
        t = title.lower().strip()
        for other in self.titles() + (extra or []):
            if difflib.SequenceMatcher(None, t, other.lower().strip()).ratio() > 0.78:
                return True
        return False

    def add(self, title, description, type_="feature", priority=5.0, acceptance=None, rationale="",
            source="ideation", extra_titles=None, force=False):
        if not force and self.is_dup(title, extra_titles):
            return None
        item = {"id": f"T{len(self.items) + 1:04d}", "title": title, "description": description,
                "type": type_, "priority": float(priority), "status": "pending", "attempts": 0,
                "acceptance": acceptance or [], "rationale": rationale, "source": source,
                "created": now(), "notes": []}
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
        return [i for i in self.items if i["status"] == "pending"]

    def next(self):
        self.reload()
        p = self.pending()
        if not p:
            return None
        return max(p, key=lambda i: i["priority"] + (1000 if i["type"] == "bug" else 0) + (2000 if i.get("source") == "user" else 0))

    def set(self, item: dict, **kw) -> None:
        item.update(kw)
        self.save()
