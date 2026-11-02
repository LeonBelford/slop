"""Planning-quality gates: size estimate before the start, spec before code, protected contract tests."""
from __future__ import annotations

from core.cfllm import LLMError, scaled
from core.cfutil import clip

MAX_LINES_ESTIMATE = 250
SIZE_SCHEMA = '{"files": 2, "lines": 120, "reason": "one sentence"}'
SPEC_SCHEMA = '{"files": [{"path": "", "purpose": ""}], "signatures": ["function/type signatures"], "edge_cases": ["..."]}'
REVIEW_SCHEMA = '{"ok": true, "problems": ["where the design breaks a contract or the task"]}'
CONTRACT_DIR = "tests/contracts/"


def removed_contract_lines(diff: str) -> list[str]:
    """Lines a diff removes from tests/contracts/ (contract tests may grow, but never shrink or loosen)."""
    out, in_contract = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            in_contract = f" b/{CONTRACT_DIR}" in line
        elif in_contract and line.startswith("-") and not line.startswith("---") and line[1:].strip():
            out.append(line[1:].strip())
    return out


class QualityMixin:
    def size_gate(self, item: dict) -> bool:
        """Estimates files and lines before the first attempt. Too big: split at once instead of failing first. True if split."""
        if not self.feat("size_gate") or item.get("size_checked") or int(item.get("attempts", 0)) > 0 or item["type"] in ("docs", "test"):
            return False
        self.backlog.set(item, size_checked=True)
        known = [f for f in (item.get("files") or []) if isinstance(f, str)]
        if known and len(known) <= 2 and not item.get("why", "").startswith("Slice"):
            self.backlog.set(item, size_files=len(known))  # the graph already knows the files: no model call to count them
            return False
        try:
            d = self.llm.json("size_estimate", "planner",
                              "Estimate how many files must change and how many lines must be written for this task. Be realistic.",
                              f"Task: {item['title']}\n{item['description']}\nAcceptance: {item.get('acceptance')}\n"
                              f"Code map:\n{clip(self.mem.render_codemap(), scaled(self.llm, 2500))}\nSchema: {SIZE_SCHEMA}")
            files, lines = int(d.get("files", 0)), int(d.get("lines", 0))
        except LLMError:
            self.backlog.set(item, size_checked=False)
            raise  # the model server is down, not a verdict: let the loop back off
        except Exception as e:  # noqa: BLE001
            self.log(f"Size estimate skipped: {clip(str(e), 120)}")
            return False
        self.backlog.set(item, size_files=files, size_lines=lines)
        if files <= self.lim["max_files_per_task"] and lines <= MAX_LINES_ESTIMATE:
            return False
        self.log(f"{item['id']} estimated at {files} files / {lines} lines: too big for one attempt, splitting before the start")
        reason = f"estimated {files} files and {lines} lines, too large for one attempt. {d.get('reason', '')}"
        if self.graph_split(item, reason):
            return True
        return self.split_task(item, [f"Pre-check: estimated {files} files and {lines} lines, too large for one attempt. {d.get('reason', '')}"])

    def make_spec(self, item: dict, plan: dict) -> None:
        """Planner writes a short design (files, signatures, edge cases), the reviewer checks it against the contracts, then it is pinned."""
        if not self.feat("spec_first") or item["type"] in ("docs",):
            return
        self.phase("Writing the design before the code", "plan", item["title"])
        bp = self.blueprint() or {}
        contracts = (bp.get("architecture") or {}).get("contracts", [])
        try:
            spec = self.llm.json("spec", "planner", "Write a short design for the task: files, signatures, edge cases. No code bodies.",
                                 f"Task: {item['title']}\n{item['description']}\nAcceptance: {item.get('acceptance')}\n"
                                 f"Plan steps: {plan.get('steps')}\nContracts: {contracts}\nSchema: {SPEC_SCHEMA}")
            review = self.llm.json("spec_review", "reviewer", "Check the design against the contracts and the task. Report only real conflicts.",
                                   f"Design: {spec}\nContracts: {contracts}\nTask: {item['title']}\nAcceptance: {item.get('acceptance')}\n"
                                   f"Schema: {REVIEW_SCHEMA}")
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            self.log(f"Design step skipped: {clip(str(e), 120)}")
            return
        problems = [str(p) for p in (review.get("problems") or [])][:4] if review.get("ok") is False else []
        sigs = [str(s) for s in spec.get("signatures", [])][:10]
        edge = [str(s) for s in spec.get("edge_cases", [])][:8]
        files, sigs, edge = self.spec_gate(item, list(spec.get("files", [])), sigs, edge)
        spec["files"] = files
        plan["spec"] = {"files": spec.get("files", []), "signatures": sigs, "edge_cases": edge, "problems": problems}
        self.handoff("implementation", "design", goal=f"{item['id']} {item['title']}",
                     decided=[f"files: {plan['spec']['files']}"] + [f"signature: {s}" for s in sigs],
                     do=[f"handle edge case: {e}" for e in edge] + list(plan.get("steps") or []),
                     do_not=[f"design conflict to avoid: {p}" for p in problems] + list(plan.get("do_not") or []),
                     done_when=list(plan.get("done_when") or []) + [str(item.get("acceptance") or "")])
        self.note("spec", f"signatures: {sigs} | edge cases: {edge} | review problems: {problems}")

    def contract_violation(self, item: dict) -> str:
        """Policy: contract tests are protected. Empty string if fine, else the message for the debug round."""
        if not self.feat("contracts_code") or item.get("key") == "contracts":
            return ""
        gone = removed_contract_lines(self.git.diff())
        if not gone:
            return ""
        return ("[policy] The change removes or loosens contract tests in tests/contracts/ (never allowed). Restore them and adapt the "
                f"code to the contract instead: {'; '.join(clip(g, 80) for g in gone[:6])}")
