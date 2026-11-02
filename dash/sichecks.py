"""Helpers of the self-improve run that only need git, the model client or the evidence files: git wrapper, change lists, backup, requirement checks, evidence gathering."""
from __future__ import annotations

import json
from pathlib import Path

import codefactory as sa
from dash import diagnostics, history, sievidence


def _git(repo: Path, *args: str) -> tuple[int, str]:
    return sa.sh(["git", *args], repo, 120)


def _changes(work: Path) -> list[str]:
    _git(work, "add", "-A")
    return [l for l in _git(work, "diff", "--cached", "--name-status")[1].splitlines() if l.strip()]


def _needs_tests(changes: list[str]) -> tuple[bool, bool]:
    """(code changed, a new test file is part of the change)."""
    paths = [(l.split("\t")[0][:1], l.split("\t")[-1]) for l in changes]
    code = any(not f.startswith(("tests/", "docs/")) and f.endswith((".py", ".html", ".sh")) for _, f in paths)
    new = any(st == "A" and f.startswith("tests/") and f.endswith(".py") for st, f in paths)
    return code, new


def _new_tests_red(work: Path, changes: list[str]) -> str:
    """Test-first check: new tests should FAIL on the code without the change. Returns a warning if they already pass there."""
    new = [l.split("\t")[-1] for l in changes if l[:1] == "A" and l.split("\t")[-1].startswith("tests/test_")]
    if not new:
        return ""
    base = work.parent / (work.name + "-base")
    try:
        if _git(work, "worktree", "add", "-q", "--detach", str(base), "HEAD")[0] != 0:
            return ""
        for t in new:
            (base / t).write_text((work / t).read_text("utf-8"), "utf-8")
        mods = [t[:-3].replace("/", ".") for t in new]
        rc, _ = sa.sh(["python3", "-m", "unittest", *mods], base, 120)
        return "" if rc != 0 else "the new tests already pass without the change (they may not test it)"
    except Exception:  # noqa: BLE001
        return ""
    finally:
        _git(work, "worktree", "remove", "--force", str(base))


def _fast_tests(work: Path, changes: list[str]) -> tuple[bool, str]:
    """Runs only the tests that belong to the change first: quick feedback before the full suite."""
    mods = [l.split("\t")[-1][:-3].replace("/", ".") for l in changes if l.split("\t")[-1].startswith("tests/test_") and l[:1] in "AM"]
    if not mods:
        return True, "ok"
    rc, out = sa.sh(["python3", "-m", "unittest", *mods], work, 300)
    return rc == 0, sa.tail(out, 1500)


def _restore(work: Path, paths: list[str]) -> None:
    """Puts protected files back to the last commit (new ones are removed)."""
    for path in paths:
        if _git(work, "cat-file", "-e", f"HEAD:{path}")[0] == 0:
            _git(work, "checkout", "HEAD", "--", path)
        else:
            (work / path).unlink(missing_ok=True)
            _git(work, "rm", "-q", "--cached", "--ignore-unmatch", path)


def _diagnose(llm, p: dict, stage: str, report: str, work: Path) -> str:
    """Reasoning step between attempts: why did it fail and what exactly should change."""
    if llm is None:
        return ""
    try:
        d = llm.json("tool_diagnose", "planner",
                     "A code change failed an automatic check. Find the root cause and say precisely what to change in the code. "
                     "Do not suggest removing or editing safeguards or checks to make them pass.",
                     f"Change: {p['title']}\n{p['change']}\nFailed stage: {stage}\nOutput:\n{sa.clip(report, 3000)}\n"
                     f"Current diff:\n{sa.clip(_git(work, 'diff', '--cached', 'HEAD')[1], 6000)}\nSchema: " '{"cause": "...", "fix": "..."}')
    except Exception:  # noqa: BLE001
        return ""
    return f"Cause: {d.get('cause', '')}\nFix: {d.get('fix', '')}" if isinstance(d, dict) and (d.get("cause") or d.get("fix")) else ""


def make_backup(repo: Path, stamp: str, out_dir: Path, log) -> str:
    """Safety net before any self-change: a tag on the current code plus a self-contained git bundle outside the work tree."""
    tag = f"backup/pre-self-improve-{stamp}"
    rc, out = _git(repo, "tag", tag, "HEAD")
    if rc != 0:
        raise RuntimeError(f"Backup failed, nothing changed: {out}")
    bdir = out_dir / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    bundle = bdir / f"codefactory-{stamp}.bundle"
    rc, out = _git(repo, "bundle", "create", str(bundle), "HEAD")
    if rc != 0:
        _git(repo, "tag", "-d", tag)
        raise RuntimeError(f"Backup failed, nothing changed: {out}")
    log(f"Backup created: tag {tag} and {bundle}. Restore: git reset --hard {tag}")
    return tag


def _verify_requirements(repo: Path, base: str, branch: str, ledger, llm, log) -> None:
    """Marks every requirement done / partial / missing by comparing it with the finished diff."""
    reqs = ledger.d["requirements"]
    if ledger.d["kind"] == "auto":  # one requirement per proposal, in the same order
        for r, pr in zip(reqs, ledger.d["proposals"], strict=False):
            ledger.mark_requirement(r["id"], "done" if pr["status"] == "kept" else "failed", pr["note"] or "kept after green tests")
        return
    diff = _git(repo, "diff", f"{base}..{branch}")[1]
    results = []
    if llm is not None and reqs:
        try:
            d = llm.json("tool_verify", "reviewer",
                         "You check whether each requirement of the user is implemented by the diff. Be strict: 'done' only if the diff clearly "
                         "does it, 'partial' if something is missing, 'missing' if it is not there.",
                         f"Requirements: {json.dumps([{'id': r['id'], 'text': r['text']} for r in reqs])}\nDiff:\n{sa.clip(diff, 14000)}\nSchema: "
                         '{"results": [{"id": 1, "status": "done|partial|missing", "evidence": "file or reason"}]}')
            results = d.get("results", []) if isinstance(d, dict) else []
        except Exception as e:  # noqa: BLE001
            log(f"  requirement check failed: {e}")
    seen = set()
    for r in results:
        if isinstance(r, dict) and isinstance(r.get("id"), int) and r.get("status") in ("done", "partial", "missing"):
            ledger.mark_requirement(r["id"], r["status"], r.get("evidence", ""))
            seen.add(r["id"])
    for r in reqs:
        if r["id"] not in seen:
            ledger.mark_requirement(r["id"], "unverified", "could not be checked automatically; review the diff")
    log("  requirements: " + ", ".join(f"{r['id']}={r['status']}" for r in ledger.d["requirements"]))


def _requirements(llm, instruction: str, log) -> list[str]:
    """The user's request as a checklist of separate, checkable requirements (falls back to the request itself)."""
    try:
        d = llm.json("tool_requirements", "planner",
                     "Split the user's request for a code change into separate, checkable requirements. Keep the user's words, do not invent "
                     "extra work. A short request is a single requirement.",
                     f"Request:\n{instruction}\nSchema: " '{"requirements": ["..."]}')
        reqs = [r.strip() for r in d.get("requirements", []) if isinstance(r, str) and r.strip()] if isinstance(d, dict) else []
    except Exception as e:  # noqa: BLE001
        log(f"Could not split the request: {e}")
        reqs = []
    return reqs[:10] or [instruction.strip()]


def gather_evidence(projects: Path, repo: Path | None = None) -> dict:
    evidence = {"projects": []}
    for ws in sa.list_projects(projects):
        a = ws / "agent"
        ideas = []
        p = a / "tool_ideas.jsonl"
        if p.exists():
            ideas = [json.loads(l)["idea"] for l in p.read_text("utf-8").splitlines()[-20:] if l.strip()]
        rows = []
        mp = a / "metrics.jsonl"
        if mp.exists():
            rows = [json.loads(l) for l in mp.read_text("utf-8").splitlines() if l.strip()]
        merged = sum(1 for r in rows if r.get("review_score") is not None)
        failed = sum(1 for r in rows if r.get("kind") == "attempt_failed")
        pb = a / "playbook.md"
        try:
            diag = diagnostics.project(ws, False, 0.0, with_repo=False)
            diag["quality"] = history.quality(a)
            hints = sievidence.suggestions(diag)
        except Exception:  # evidence is optional: a broken project must not stop the plan
            hints = []
        evidence["projects"].append({"suggestions": hints, "journals": sievidence.journal_evidence(a), "planning": sievidence.triage_evidence(a),

            "project": ws.name, "merged": merged, "failed_attempts": failed, "tool_ideas": ideas,
            "playbook": sa.clip(pb.read_text("utf-8"), 1500) if pb.exists() else "",
            "recent_failures": [r.get("why", "") for r in rows if r.get("kind") == "attempt_failed"][-8:]})
    if repo is not None:  # evidence that needs no failing project: the benchmark and the code health of the tool itself
        from bench import harness
        evidence["bench"] = sievidence.bench_weaknesses(harness.load_results())
        evidence["health"] = sievidence.health(repo)
    return evidence
