"""Task tiers: how much ceremony a task gets depends on its real size. A one-file change does not need a review, a mutation check and a
second model call per gate; a big or risky change does. The tier is decided after the coder has written the change (so from the real diff),
and every tier is a plain list of gate names that a project can override in config.json ("tiers": {"small": ["e2e", "review"]}).

Also here, because they are the same idea (spend model time only where it pays off): the light plan for small tasks with known files
(no planner call, the graph context pack is the plan), the single full verify run before the commit, the graph split of a too big task along
its file groups, and the re-plan of a task whose dependency was cancelled."""
from __future__ import annotations

import json

from core import graphctx
from core.cfa_verify import is_test_file
from core.cfllm import LLMError
from core.cfutil import clip, write_json, write_text

TIER_GATES = {
    "tiny": ("e2e", "security"),
    "small": ("e2e", "security"),
    "full": ("coverage", "mutation", "e2e", "security", "review", "acceptance"),
}
TINY_LINES = 40  # at most one source file and this many added lines = tiny
FULL_LINES = 150  # this many added lines (or 3+ source files, or a risky task) = full
FULL_FILES = 3  # on a 12 GB GPU every extra gate is a model swap of a minute or more: below three source files the gates cost more than they find
DOC_EXT = (".md", ".rst", ".txt")
REPLAN_SCHEMA = '{"still_needed": true, "description": "adjusted task description", "acceptance": ["adjusted criteria"], "why": "one sentence"}'
NO_REVIEW = {"score": None, "verdict": "approve", "issues": []}


class TierMixin:
    # ---- tier decision and the stage list
    def tier_of(self, item: dict, base_sha: str) -> str:
        """tiny | small | full, from the real diff. A risky task, a retry after a failed gate and a big change always get everything."""
        mode = str(self.cfg["features"].get("tier", "auto"))
        if mode in TIER_GATES:
            return mode
        if not self.feat("tiers"):
            return "full"
        src = [f for f in self.git.changed_files(base_sha) if not is_test_file(f) and not f.endswith(DOC_EXT)]
        added = sum(1 for ln in self.git.diff(base_sha).splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        if item.get("risk") == "high" or int(item.get("attempts", 0)) > 1 or len(src) >= FULL_FILES or added >= FULL_LINES:
            return "full"
        return "tiny" if len(src) <= 1 and added <= TINY_LINES else "small"

    def tier_gates(self, tier: str) -> list[str]:
        custom = (self.cfg.get("tiers") or {}).get(tier)
        names = custom if isinstance(custom, list) else TIER_GATES[tier]
        return [n for n in names if n in ("coverage", "mutation", "e2e", "security", "review", "acceptance")]

    def run_gates(self, item: dict, plan: dict, base_sha: str) -> tuple[bool, str, dict]:
        """Runs the gates of the task's tier. Returns (ok, why it failed, review). Skipped gates cost nothing; the review dict then is a stub."""
        tier = self.cur_tier = self.tier_of(item, base_sha)
        gates = self.tier_gates(tier)
        self.backlog.set(item, tier=tier)
        self.log(f"Task tier: {tier} (gates: {', '.join(gates) or 'none'})")
        review = dict(NO_REVIEW, summary=item["title"])
        for name in gates:
            if name == "coverage":
                self.coverage_check(item)
            elif name == "mutation":
                if not self.mutation_check(item):
                    return False, "Red after the mutation fix round", review
            elif name == "e2e":
                ok, report = self.e2e_check(item)
                if not ok:
                    return False, "UI test failed:\n" + report, review
            elif name == "security":
                ok, report = self.security_gate(item, plan)
                if not ok:
                    return False, "Security gate failed:\n" + report, review
            elif name == "review":
                ok, why, review = self.review_gate(item, plan)
                if not ok:
                    return False, why, review
            elif name == "acceptance" and not self.acceptance_check(item, plan):
                return False, "Red after the acceptance fix round", review
        return True, "", review

    def review_gate(self, item: dict, plan: dict) -> tuple[bool, str, dict]:
        review = self.review(item, plan)
        self.note("review", f"score {review.get('score')} {review.get('verdict')}: {clip(json.dumps(review.get('issues', [])), 300)}")
        if review.get("verdict") == "revise" or review.get("score", 10) < self.lim["min_review_score"]:
            self.log(f"Review requests revision (score {review.get('score')})")
            self.handoff("review fix", "review", goal=item["title"],
                         decided=[f"review score {review.get('score')}: {review.get('summary', '')}"],
                         do=[f"{i.get('file')}: {i.get('problem')} -> {i.get('fix')}" for i in review.get("issues", []) if isinstance(i, dict)],
                         do_not=["change behaviour the review did not criticise", "delete tests"], done_when=["every listed issue is fixed and tests stay green"])
            self.code("review_fix", sorted(set(self.git.changed_files()))[:self.lim["max_files_per_task"] + 4],
                      [str(self.run_dir / "context.md")],
                      f"Fix these review points:\n{json.dumps(review.get('issues', []), indent=1)}\n\n{self.RULES}")
            ok, report = self.debug_loop(item)
            if not ok:
                return False, "Red after review fix:\n" + report, review
            review = self.review(item, plan)
        return True, "", review

    # ---- verification: skip runs that cannot change the result
    ok_tree = ""

    def tree_now(self) -> str:
        if not self.feat("fast_verify"):
            return ""
        self.git.run("add", "-A")
        return self.git.out("write-tree").strip()

    def unchanged_since_green(self, tree: str) -> bool:
        if not self.ok_tree:
            return False
        if tree == self.ok_tree:
            return True
        names = self.git.out("diff", "--name-only", self.ok_tree, tree).split()
        return bool(names) and all(n.endswith(DOC_EXT) and "requirements" not in n.lower() for n in names)

    # ---- verification: targeted tests while working, one full run before the commit
    def full_gate(self, item: dict) -> tuple[bool, str]:
        """The one full build/test/lint run before the commit (a tree that already passed is not run again). Red: debug rounds with full runs."""
        ok, report = self.verify()
        if ok:
            return True, report
        self.log("Full run before the commit is red: back to debugging with full runs")
        return self.debug_loop(item, full=True)

    # ---- light plan: no planner call for a small task whose files the graph already knows
    def light_plan(self, item: dict, previous: list[str]) -> dict | None:
        if not self.feat("light_plan") or previous or int(item.get("attempts", 0)) > 1 or item.get("risk") == "high" or item["type"] in ("docs",):
            return None
        pack = graphctx.context_pack(self.backlog.items, item)
        if item.get("files_guess"):
            return None
        files = [f for f in dict.fromkeys(graphctx.files_of(item)) if (self.repo / f).exists() and not is_test_file(f)]
        size = int(item.get("size_files", 0) or 0)
        if not files or len(files) > 2 or size > 2 or int(item.get("size_lines", 0) or 0) > 80:
            return None
        self.phase("Light plan: small task with known files, no planner call", "plan")
        cands = graphctx.prefer_files(pack["files"], files, lambda f: (self.repo / f).exists())
        ctx = self.mem.context_pack(item, cands)
        codemap = self.mem.codemap
        plan = {"understanding": item.get("why") or item["title"], "files_to_change": files, "new_files": [],
                "read_only_context": [f for f in pack["files"] if f not in files and (self.repo / f).exists()][:4],
                "affected_dependents": sorted({d for f in files for d in codemap.get(f, {}).get("imported_by", [])})[:8],
                "invariants": [], "risks": [], "steps": [str(c) for c in (item.get("acceptance") or [])] or [item["title"]],
                "tests_to_add": [], "docs_to_update": [], "decisions": [], "do_not": ["touch files outside the plan"],
                "done_when": [str(c) for c in (item.get("acceptance") or [])], "test_files": [], "security_impact": "",
                "known_consequences": self.mem.consequences_for(files, item["title"]), "light": True}
        write_json(self.run_dir / "plan.json", plan)
        write_text(self.run_dir / "context.md", ctx + ("\n" + pack["text"] if pack["text"] else ""))
        self.note("plan", f"light plan from the graph: change {files}, read {plan['read_only_context']}")
        return plan

    # ---- a cancelled dependency: the successor is re-planned once instead of silently running on
    def replan_after_cancel(self, item: dict) -> bool:
        """True if the task was cancelled because nothing it was planned around exists any more."""
        if not self.feat("dep_replan"):
            return False
        gone = [d for d in self.backlog.dead_deps(item) if d not in item.get("replanned_for", [])]
        if not gone:
            return False
        self.backlog.set(item, replanned_for=item.get("replanned_for", []) + gone)
        why = "; ".join(f"{d} {(self.backlog.by_id(d) or {}).get('title', '')}: {(self.backlog.by_id(d) or {}).get('reason', 'cancelled')}" for d in gone)
        try:
            d = self.llm.json("dep_replan", "strategist",
                              "A task was planned to build on work that was cancelled. Adjust the task so it works without it, or say it is no longer needed.",
                              f"Task {item['id']}: {item['title']}\n{item['description']}\nAcceptance: {item.get('acceptance')}\n"
                              f"Cancelled dependency: {clip(why, 600)}\n"
                              f"Goal:\n{clip(self.goal, 800)}\nSchema: {REPLAN_SCHEMA}")
        except LLMError:
            self.backlog.set(item, replanned_for=[x for x in item["replanned_for"] if x not in gone])
            raise
        except Exception as e:  # noqa: BLE001
            self.log(f"Re-plan after a cancelled dependency skipped: {clip(str(e), 120)}")
            return False
        if d.get("still_needed") is False and item.get("source") != "user":
            self.backlog.set(item, status="cancelled", reason=f"its dependency was cancelled and the task is no longer needed: {clip(str(d.get('why', '')), 200)}")
            self.log(f"{item['id']} cancelled: its dependency {', '.join(gone)} is gone and it is no longer needed")
            return True
        upd = {"notes": item.get("notes", []) + [f"re-planned because {', '.join(gone)} was cancelled: {clip(str(d.get('why', '')), 200)}"]}
        if str(d.get("description", "")).strip():
            upd["description"] = str(d["description"])[:800]
        if isinstance(d.get("acceptance"), list) and d["acceptance"]:
            upd["acceptance"] = [str(a)[:200] for a in d["acceptance"]][:6]
        self.backlog.set(item, **upd)
        self.log(f"{item['id']} re-planned: its dependency {', '.join(gone)} was cancelled")
        return False

    # ---- graph split: a too big task is cut along the file groups of the graph (no model call)
    def graph_split(self, item: dict, reason: str) -> bool:
        """2-3 slices from the node's own files, grouped by module. False = not applicable (few files, one block), the caller splits with the model."""
        if not self.feat("graph_split") or int(item.get("split_depth", 0)) >= 2:
            return False
        files = [] if item.get("files_guess") else graphctx.files_of(item)
        groups = graphctx.group_files(files, 3, int(self.cfg["features"].get("graph_split_files", 4)))
        if not groups:
            return False
        subs = []
        for n, g in enumerate(groups, 1):
            last = n == len(groups)
            rest = [f for o in groups if o is not g for f in o]
            subs.append({"title": f"{item['title']} (part {n}/{len(groups)})",
                         "description": f"{item['description']}\nThis is part {n} of {len(groups)}: change only {', '.join(g)}. "
                                        f"The other parts handle {', '.join(rest)}; keep the build green after this part.",
                         "acceptance": item.get("acceptance") if last else [f"the part for {', '.join(g)} works and its tests pass"],
                         "type": item["type"], "files": g})
        self.log(f"{item['id']}: split along its file groups in the graph, no model call")
        return self.apply_split(item, subs, reason, ["Pre-check: " + reason])
