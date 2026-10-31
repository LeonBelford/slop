"""Self-improvement of the agent tool itself (`python3 saas_agent.py improve`, `make improve`).

Evidence from all projects (tool ideas from reflection, playbooks, success/failure statistics) is turned into a few
concrete code improvements. They are implemented in a separate git worktree on a branch `self-improve/<timestamp>`:

  * the running agent code is never touched, nothing is merged automatically
  * every proposal is committed only if `selftest` and the unit tests stay green
  * safety-critical files (web fetch guard, UI guard, this module, existing tests, CI) are off limits; a proposal that
    changes them is reverted
  * you review the branch (`git diff main...self-improve/<ts>`) and merge it yourself

Proposals are always written to self_improve/proposals-<ts>.md, even with --dry-run.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import saas_agent as sa

PROTECTED = ("webaccess.py", "webui.py", "selfimprove.py", ".github/", ".env", "scripts/quick-setup-popos.sh")
MAX_PROPOSALS = 3


def protected_violations(changes: list[str]) -> list[str]:
    """changes: lines of `git diff --name-status`. Existing tests may not change; new test files are welcome."""
    bad = []
    for line in changes:
        parts = line.split("\t")
        status, paths = parts[0][:1], parts[1:]
        for path in paths:
            if path.startswith(PROTECTED) or (path.startswith("tests/") and status != "A"):
                bad.append(path)
    return bad


def gather_evidence(projects: Path) -> dict:
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
        evidence["projects"].append({
            "project": ws.name, "merged": merged, "failed_attempts": failed, "tool_ideas": ideas,
            "playbook": sa.clip(pb.read_text("utf-8"), 1500) if pb.exists() else "",
            "recent_failures": [r.get("why", "") for r in rows if r.get("kind") == "attempt_failed"][-8:]})
    return evidence


def _git(repo: Path, *args: str) -> tuple[int, str]:
    return sa.sh(["git", *args], repo, 120)


def _tests_green(work: Path, timeout: int) -> tuple[bool, str]:
    for cmd in (["python3", "saas_agent.py", "selftest"], ["python3", "-m", "unittest", "discover", "-s", "tests"]):
        rc, out = sa.sh(cmd, work, timeout)
        if rc != 0:
            return False, sa.tail(out, 1500)
    return True, "ok"


def _aider(model: str, timeout: int):
    def run(work: Path, files: list[str], prompt: str) -> str:
        pf = work / ".improve_prompt.md"
        pf.write_text(prompt, "utf-8")
        cmd = ["aider", "--yes-always", "--no-auto-commits", "--no-pretty", "--no-stream", "--no-check-update",
               "--model", model, "--message-file", str(pf), *files]
        rc, out = sa.sh(cmd, work, timeout, env={"OLLAMA_API_BASE": sa.os.getenv("OLLAMA_API_BASE", "http://localhost:11434")})
        pf.unlink(missing_ok=True)
        return out
    return run


def _docs_pass(work: Path, proposal: dict, coder, log) -> None:
    """After a kept change, brings README.md and docs/ in line with it and logs it in docs/SELF_IMPROVEMENTS.md."""
    diff = _git(work, "show", "--stat", "--patch", "HEAD")[1]
    doc_files = [f for f in ("README.md", "docs/GUIDE.md", "docs/ARCHITECTURE.md", "docs/SELF_IMPROVEMENTS.md")
                 if (work / f).is_file()]
    if not (work / "docs" / "SELF_IMPROVEMENTS.md").exists():
        doc_files.append("docs/SELF_IMPROVEMENTS.md")
        sa.write_text(work / "docs" / "SELF_IMPROVEMENTS.md", "# Self-improvements\n\nChanges the agent made to itself, newest last.\n")
    coder(work, doc_files,
          f"Documentation update for this change: {proposal['title']}\n\n{proposal.get('rationale', '')}\n\n"
          f"Diff:\n{sa.clip(diff, 6000)}\n\nUpdate README.md, docs/GUIDE.md and docs/ARCHITECTURE.md only where the change affects "
          "what they say (usage, commands, config, behavior). Keep the existing structure and language (English). Append one "
          "entry to docs/SELF_IMPROVEMENTS.md: what changed, why, which files. Change documentation files only.")
    _git(work, "add", "-A")
    changes = [l for l in _git(work, "diff", "--cached", "--name-status")[1].splitlines() if l.strip()]
    only_docs = all(l.split("\t")[-1] in ("README.md",) or l.split("\t")[-1].startswith("docs/") for l in changes)
    if changes and only_docs:
        _git(work, "-c", "user.name=saas-agent", "-c", "user.email=agent@localhost", "commit", "-q", "-m",
             f"self-improve: docs for {proposal['title']}")
        log("  docs updated")
    else:
        _git(work, "reset", "-q", "--hard", "HEAD")
        _git(work, "clean", "-qfd")
        log("  docs pass produced no valid change (reverted)" if changes else "  docs pass: nothing to change")


def main(projects: Path, dry_run: bool = False, repo: Path | None = None, llm=None, coder=None, log=print) -> dict:
    repo = (repo or Path(__file__).resolve().parent).resolve()
    cfg = json.loads(json.dumps(sa.DEFAULT_CONFIG))
    llm = llm or sa.LLM(cfg, log)
    coder = coder or _aider(cfg["coder_model"], cfg["limits"]["coder_timeout"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = repo / "self_improve"
    out_dir.mkdir(exist_ok=True)

    evidence = gather_evidence(projects)
    if not any(p["tool_ideas"] or p["failed_attempts"] for p in evidence["projects"]):
        log("No evidence yet: let at least one project run and reflect first (it records tool ideas every few merges).")
        return {"branch": None, "proposals": []}
    code_files = sorted(p.name for p in repo.glob("*.py") if p.name not in PROTECTED) + ["scripts/run.sh", "MASTER.md"]
    d = llm.json("improve_plan", "planner",
                 "You improve an autonomous coding agent (this repository). Propose small, safe, high-value changes to the "
                 "agent's own code: better prompts, smarter heuristics, more robust error handling, better context selection. "
                 "Prefer fixing observed failures over adding features. Never propose changes to safety guards.",
                 f"Evidence from running projects:\n{sa.clip(json.dumps(evidence, ensure_ascii=False), 9000)}\n"
                 f"Editable files: {code_files}\nPropose at most {MAX_PROPOSALS}. Schema: "
                 '{"proposals": [{"title": "...", "rationale": "which evidence", "files": ["..."], "change": "precise instructions", '
                 '"risk": "low|medium|high"}]}')
    proposals = [p for p in d.get("proposals", []) if isinstance(p, dict) and p.get("title") and p.get("change")][:MAX_PROPOSALS]
    md = [f"# Self-improvement proposals {stamp}\n"] + [
        f"## {i}. {p['title']} (risk: {p.get('risk', '?')})\n{p.get('rationale', '')}\n\nFiles: {p.get('files', [])}\n\n{p['change']}\n"
        for i, p in enumerate(proposals, 1)]
    sa.write_text(out_dir / f"proposals-{stamp}.md", "\n".join(md))
    log(f"{len(proposals)} proposals written to self_improve/proposals-{stamp}.md")
    if dry_run or not proposals:
        return {"branch": None, "proposals": proposals}

    branch, work = f"self-improve/{stamp}", out_dir / "work" / stamp
    rc, out = _git(repo, "worktree", "add", "-b", branch, str(work), "HEAD")
    if rc != 0:
        raise RuntimeError(f"git worktree failed: {out}")
    base = _git(work, "rev-parse", "HEAD")[1].strip()
    kept = []
    try:
        for p in proposals:
            files = [f for f in p.get("files", []) if isinstance(f, str) and (work / f).is_file()
                     and not f.startswith(PROTECTED)]
            log(f"Implementing: {p['title']}")
            coder(work, files, f"{p['change']}\n\nRationale: {p.get('rationale', '')}\n"
                  "Keep the change small and focused. Do not modify existing tests; add new tests if sensible. Standard library only. "
                  "Documentation is updated in a separate step afterwards.")
            _git(work, "add", "-A")
            changes = [l for l in _git(work, "diff", "--cached", "--name-status")[1].splitlines() if l.strip()]
            bad = protected_violations(changes)
            ok, report = (False, f"touches protected files: {bad}") if bad else (_tests_green(work, 900) if changes else (False, "no changes"))
            if ok:
                _git(work, "-c", "user.name=saas-agent", "-c", "user.email=agent@localhost", "commit", "-q", "-m",
                     f"self-improve: {p['title']}\n\n{p.get('rationale', '')}")
                kept.append(p["title"])
                log("  kept (tests green)")
                _docs_pass(work, p, coder, log)
            else:
                _git(work, "reset", "-q", "--hard", "HEAD")
                _git(work, "clean", "-qfd")
                log(f"  reverted: {report}")
    finally:
        _git(repo, "worktree", "remove", "--force", str(work))
        shutil.rmtree(work, ignore_errors=True)
    if not kept:
        _git(repo, "branch", "-D", branch)
        log("No proposal survived the checks. Nothing to review.")
        return {"branch": None, "proposals": proposals}
    log(f"Branch {branch} has {len(kept)} commit(s). Review: git diff main...{branch}   Merge: git merge {branch}")
    return {"branch": branch, "proposals": proposals, "kept": kept, "base": base}


if __name__ == "__main__":
    main(Path("projects"))
