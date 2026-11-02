"""Quality graphs: regular refactor and performance graphs that keep the codebase clean and compact."""
from __future__ import annotations

PERF_FACTOR = 2  # a performance graph every this many refactor intervals


def _open_quality(items: list[dict]) -> bool:
    return any(i.get("source") == "hygiene" and i["type"] == "refactor" and i["status"] in ("pending", "in_progress", "blocked") for i in items)


class HygieneGraphMixin:
    def quality_graph(self, kind: str, files: list[str]) -> list[dict]:
        """Adds a small connected graph (analyse -> change -> verify) for `kind` ('refactor' or 'perf') over `files`."""
        names = ", ".join(files)
        if kind == "refactor":
            steps = [
                (f"Refactor: find duplication and dead code in {names}", "List duplicated logic, dead code and over-long functions in these files and remove the dead code. No behavior change.",
                 "Dead code is removed; duplicates are named in the notes", "Hotspot files keep growing; dead code and duplicates make them costly to change"),
                (f"Refactor: simplify and split {names}", "Merge the duplicates found before into shared helpers, split long functions, improve names. No behavior change.",
                 "Duplicates are merged; no function is longer than needed", "Smaller, clearer functions keep later tasks cheap and safe"),
            ]
        else:
            steps = [
                (f"Performance: find the slowest paths in {names}", "Find needless repeated work (loops, repeated reads or parsing, N+1 calls) in these files and fix the cheapest wins.",
                 "Needless repeated work is removed; behavior is unchanged", "Hot paths are paid on every run, so cheap wins compound"),
                (f"Performance: verify {names}", "Add or extend a test that guards the optimized behavior and check that nothing regressed.",
                 "A test covers the optimized paths; build and tests green", "An optimization without a guard silently regresses"),
            ]
        out, prev = [], []
        for title, desc, accept, why in steps:
            item = self.backlog.add(title, desc, "refactor", 4.0, [accept, "Build and tests green"], "Hygiene", "hygiene",
                                    depends_on=prev, why=why)
            if not item:
                break
            out.append(item)
            prev = [item["id"]]
        return out

    def schedule_quality(self, ranked: list[str], merged: int) -> None:
        """Refactor graph on the usual interval, performance graph on every PERF_FACTOR-th one; never while one is still open."""
        if not ranked or _open_quality(self.backlog.items):
            return
        n = merged // max(1, self.lim["refactor_every"])
        self.quality_graph("perf" if n % PERF_FACTOR == 0 and n else "refactor", ranked)
