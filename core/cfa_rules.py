"""Per-project self-improvement: the playbook is versioned and judged by its effect, so a rule set that makes things worse is rolled back."""
from __future__ import annotations

from core.cfutil import clip, now, read_json, write_json, write_text

WORSE_BY = 0.2
MIN_TASKS = 3


def fail_rate(rows: list[dict], since: str = "") -> tuple[float, int]:
    """(share of failed attempts among finished tasks and failed attempts, sample size) for metric rows newer than `since`."""
    rows = [r for r in rows if r.get("time", "") > since]
    failed = sum(1 for r in rows if r.get("kind") == "attempt_failed")
    done = sum(1 for r in rows if r.get("review_score") is not None)
    return (failed / (failed + done) if failed + done else 0.0), failed + done


class RulesMixin:
    def save_playbook(self, text: str, baseline: float) -> None:
        """Stores the new playbook, keeps the previous one as a numbered backup and remembers the failure rate it has to beat."""
        hist = self.dir / "playbook_history"
        hist.mkdir(exist_ok=True)
        cur = self.dir / "playbook.md"
        backup = ""
        if cur.exists():
            backup = f"{len(list(hist.glob('*.md'))) + 1:03d}.md"
            write_text(hist / backup, cur.read_text("utf-8"))
        write_text(cur, clip(text, 4000))
        write_json(self.dir / "playbook_eval.json", {"time": now(), "baseline": baseline, "backup": backup, "judged": False})

    def judge_playbook(self, rows: list[dict]) -> str:
        """Compares the failure rate since the last playbook change with the one before it; restores the old playbook if it got clearly worse."""
        ev = read_json(self.dir / "playbook_eval.json", None)
        if not ev or ev.get("judged"):
            return ""
        rate, n = fail_rate(rows, ev["time"])
        if n < MIN_TASKS:
            return ""
        ev["judged"], ev["rate_after"] = True, rate
        old = self.dir / "playbook_history" / ev["backup"] if ev.get("backup") else None
        if rate > ev["baseline"] + WORSE_BY and old and old.exists():
            write_text(self.dir / "playbook.md", old.read_text("utf-8"))
            ev["verdict"] = "reverted"
            self.log(f"Playbook rolled back: failures rose from {round(ev['baseline'] * 100)}% to {round(rate * 100)}% after the last rule change")
        else:
            ev["verdict"] = "kept"
        write_json(self.dir / "playbook_eval.json", ev)
        return ev["verdict"]

    def master_suggestions(self, rules: list) -> None:
        """Rules that are not specific to this project: collected for you to copy into MASTER.md (never applied automatically)."""
        rules = [str(r).strip() for r in rules if str(r).strip()][:3]
        if rules:
            with open(self.dir / "master_suggestions.md", "a", encoding="utf-8") as f:
                f.write("".join(f"- {r}\n" for r in rules))
