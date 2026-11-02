"""Quality graphs: regular refactor and performance graphs that keep the codebase clean and compact."""
from __future__ import annotations

from core import graph_ideas
from core.cfutil import now, read_json, write_json

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
            if kind == "perf" and self.bench_cmd():
                item["bench"] = True  # accepted only if the benchmark does not get slower
            out.append(item)
            prev = [item["id"]]
        if kind == "perf" and out and self.bench_cmd():
            self.bench_baseline()
        self.backlog.save()
        return out

    def schedule_quality(self, ranked: list[str], merged: int) -> None:
        """Refactor graph on the usual interval, performance graph on every PERF_FACTOR-th one; never while one is still open."""
        if not ranked or _open_quality(self.backlog.items):
            return
        n = merged // max(1, self.lim["refactor_every"])
        self.quality_graph("perf" if n % PERF_FACTOR == 0 and n else "refactor", ranked)

    @property
    def ideas_file(self):
        return self.dir / "graph_ideas.json"

    @property
    def processes_file(self):
        return self.dir / "processes.json"

    def store_ideas(self, raw, items: list[dict]) -> int:
        """Keeps the graph ideas of a re-evaluation (new features that fit the project) for you to add from the Overview."""
        cur = graph_ideas.load_ideas(self.ideas_file, read_json)
        known = {i["title"] for i in items} | {i["title"] for i in cur["ideas"]}
        fresh = graph_ideas.norm_ideas(raw, known, cur["dismissed"])
        if fresh:
            write_json(self.ideas_file, {"ideas": (cur["ideas"] + fresh)[-graph_ideas.MAX_IDEAS:], "dismissed": cur["dismissed"], "ts": now()})
            self.log(f"{len(fresh)} new graph idea(s) for the project: " + ", ".join(i["title"] for i in fresh))
        return len(fresh)

    def run_process(self, proc: dict) -> list[dict]:
        out = graph_ideas.add_graph(self.backlog, graph_ideas.process_steps(proc), f"Standard process: {proc['name']}", "process", f"Part of the standard process '{proc['name']}'")
        self.backlog.save()
        return out

    def run_due_processes(self, merged: int) -> None:
        """Standard processes with an interval (every N merged tasks) are added as a graph when due, never while an earlier run is still open."""
        procs, last = read_json(self.processes_file, []), self.state.setdefault("process_at", {})
        for p in procs:
            if not p.get("every") or merged - last.get(p["id"], 0) < p["every"]:
                continue
            last[p["id"]] = merged
            title = f"Standard process: {p['name']}"
            if any(i.get("rationale") == title and i["status"] in ("pending", "in_progress", "blocked") for i in self.backlog.items):
                continue
            self.log(f"Standard process due: {p['name']}")
            self.run_process(p)

    @property
    def custom_file(self):
        return self.dir / "custom_graphs.json"

    def add_custom_graph(self, name: str, goal: str, steps_text: str) -> list[dict]:
        """A graph you describe: your own steps (one per line) become tasks; the next re-evaluation builds the graph out from the goal.
        Without steps it starts as one open task that the re-evaluation may split."""
        name, goal = " ".join(name.split())[:80], goal.strip()[:1500]
        if not (name or goal):
            raise ValueError("Give the graph a name or describe what it should do")
        name = name or goal.split("\n")[0][:60]
        steps = graph_ideas.steps_from_text(steps_text, True, "feature")
        mine = bool(steps)
        steps = steps or [{"title": name, "description": goal or name, "type": "feature", "acceptance": [], "needs": [], "why": "Your own graph"}]
        made = graph_ideas.add_graph(self.backlog, steps, f"Custom graph: {name}", "user" if mine else "custom", f"Part of your graph '{name}'")
        graphs = read_json(self.custom_file, [])
        graphs.append({"title": name, "goal": goal, "ids": [m["id"] for m in made], "expanded": False})
        write_json(self.custom_file, graphs[-30:])
        self.backlog.save()
        return made

    def custom_pending(self) -> list[dict]:
        return [g for g in read_json(self.custom_file, []) if not g.get("expanded")]

    def custom_note(self, mark: bool = True) -> str:
        """For the re-evaluation prompt: your custom graphs that still need to be built out (then they count as handled)."""
        graphs = read_json(self.custom_file, [])
        todo = [g for g in graphs if not g.get("expanded")]
        if not todo:
            return ""
        by = {i["id"]: i for i in self.backlog.items}
        rows = [f"- {g['title']}: {g['goal'] or '(no description)'} | existing tasks: " + ", ".join(f"{k} {by[k]['title'][:60]}" for k in g["ids"] if k in by) for g in todo]
        if mark:
            write_json(self.custom_file, [{**g, "expanded": True} if g in todo else g for g in graphs])
        return ("\nCustom graphs the user wants built out. For each, add the missing steps as new_tasks (depends_on the existing tasks of that graph or earlier steps, each with a why); "
                "a single open task of such a graph may be split into 2 to 4 steps; keep the user's own steps as they are:\n" + "\n".join(rows))
