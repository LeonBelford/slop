"""Self-improvement of the agent tool itself (`python3 codefactory.py improve`, `make self-improve`).

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
import re
import shutil
import time
from pathlib import Path

import codefactory as sa
import security

PROTECTED = ("webaccess.py", "security.py", "webui.py", "selfimprove.py", ".github/", ".env", "scripts/quick-setup-popos.sh")
# Directed tasks (user-written instruction) may also change the web UI server; the guards for fetching and this module stay locked.
PROTECTED_DIRECTED = ("webaccess.py", "security.py", "selfimprove.py", ".github/", ".env", "scripts/quick-setup-popos.sh")
MAX_PROPOSALS = 3


def protected_violations(changes: list[str], protected: tuple = PROTECTED) -> list[str]:
    """changes: lines of `git diff --name-status`. Existing tests may not change; new test files are welcome."""
    bad = []
    for line in changes:
        parts = line.split("\t")
        status, paths = parts[0][:1], parts[1:]
        for path in paths:
            if path.startswith(protected) or (path.startswith("tests/") and status != "A"):
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


STATE = Path(__file__).resolve().parent / "self_improve"  # live state shown in the UI ("Change the tool itself")


def _phase(step: str, detail: str = "") -> None:
    """Publishes the current stage (plan / implement / test / docs) for the UI; an empty step clears it."""
    f = STATE / "phase.json"
    if not step:
        f.unlink(missing_ok=True)
        return
    STATE.mkdir(exist_ok=True)
    sa.write_json(f, {"step": step, "detail": detail, "since": time.time()})


def _wire(llm, stamp: str) -> None:
    """Makes the model client record every thought and stream into the same files the project view uses."""
    if isinstance(llm, sa.LLM):
        STATE.mkdir(exist_ok=True)
        llm.thoughts, llm.stream, llm.live = STATE / "thoughts.jsonl", STATE / "llm_stream.json", STATE / "llm_now.json"
        llm.ctx = lambda: stamp


def _dependents(work: Path, files: list[str]) -> dict[str, list[str]]:
    """Which other Python files import each file (the 'who breaks if I change this' map)."""
    out: dict[str, list[str]] = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        mod = Path(f).stem
        pat = re.compile(rf"^\s*(?:import|from)\s+{re.escape(mod)}\b", re.M)
        out[f] = sorted(p.name for p in work.rglob("*.py") if p.name != Path(f).name and ".venv" not in p.parts
                        and pat.search(p.read_text("utf-8", "replace")))
    return out


def _impact(work: Path, p: dict, files: list[str], llm, log, protected: tuple) -> tuple[list[str], str]:
    """Think before changing: consequences, dependents and invariants (plan + critique), like the project agent does."""
    if llm is None:
        return files, ""
    _phase("impact", "Analysing what this change can break")
    deps = _dependents(work, files)
    src = "\n\n".join(f"### {f}\n{sa.clip((work / f).read_text('utf-8', 'replace'), 3500)}" for f in files[:4])
    try:
        plan = llm.json("tool_impact", "planner",
                        "You are a tech lead for an autonomous coding agent. Before every change you analyse relationships: who imports the files, "
                        "which invariants apply, what can break, and what the change exposes security-wise. Plan minimally.",
                        f"Change: {p['title']}\n{p['change']}\nFiles: {files}\nImported by: {json.dumps(deps)}\nSource:\n{src}\nSchema: "
                        '{"risks": ["..."], "affected_dependents": ["paths"], "invariants": ["..."], "tests_to_add": ["..."], "security_impact": ""}')
        crit = llm.json("tool_plan_critique", "reviewer", "You strictly critique plans for overlooked dependents and broken invariants.",
                        f"Plan: {json.dumps(plan)}\nImported by: {json.dumps(deps)}\nSchema: "
                        '{"ok": true, "missing_files": ["..."], "concerns": ["..."]}')
    except Exception as e:  # noqa: BLE001
        log(f"  impact analysis failed: {e}")
        return files, ""
    if not isinstance(plan, dict) or not isinstance(crit, dict):
        return files, ""
    for f in crit.get("missing_files") or []:
        if isinstance(f, str) and (work / f).is_file() and f not in files and not f.startswith(protected):
            files.append(f)
    notes = ["Impact analysis (respect it):"]
    for key in ("risks", "affected_dependents", "invariants", "tests_to_add"):
        if plan.get(key):
            notes.append(f"- {key}: {plan[key]}")
    if plan.get("security_impact"):
        notes.append(f"- security_impact: {plan['security_impact']}")
    if crit.get("concerns"):
        notes.append(f"- reviewer concerns: {crit['concerns']}")
    log(f"  impact: {len(plan.get('risks') or [])} risk(s), {len(crit.get('concerns') or [])} concern(s)")
    return files[:8], "\n".join(notes)


def _security_gate(work: Path, p: dict, llm, coder, log) -> tuple[bool, str]:
    """Same gate as for projects: static rules on the changed files plus an attacker-minded review; one fix round."""
    for rnd in (1, 2):
        _phase("security", f"Static rules and threat review, round {rnd}")
        _git(work, "add", "-A")
        changed = [l.split("\t")[-1] for l in _git(work, "diff", "--cached", "--name-status")[1].splitlines() if l.strip()]
        found = security.scan_repo(work, [f for f in changed if (work / f).exists()])
        if llm is not None and rnd == 1:
            found += security.llm_review(llm, p["title"], _git(work, "diff", "--cached")[1], log)
        found = security.dedupe(found)
        bad = security.blocking(found, "high")
        if not bad:
            if found:
                log(f"  security: {security.summary(found)} (non-blocking)")
            return True, ""
        log(f"  security: {len(bad)} blocking finding(s), round {rnd}")
        if rnd == 2:
            return False, "security gate: " + security.render(bad, 6)
        coder(work, [f for f in changed if (work / f).is_file()][:8],
              "Fix these security problems without changing behavior otherwise. Never hide them with comments:\n" + security.render(bad))
    return False, "security gate failed"


def _review(work: Path, p: dict, llm, log) -> tuple[bool, str]:
    """Final code review of the whole diff before it is kept."""
    if llm is None:
        return True, ""
    _phase("review", "Reviewing the finished change")
    try:
        d = llm.json("tool_review", "reviewer", "You are a strict code reviewer. Reject only for real bugs, broken behavior, removed safeguards or missing tests.",
                     f"Task: {p['title']}\n{p['change']}\nDiff:\n{sa.clip(_git(work, 'diff', '--cached')[1], 12000)}\nSchema: "
                     '{"approve": true, "issues": ["..."]}')
    except Exception as e:  # noqa: BLE001
        log(f"  review failed: {e}")
        return True, ""
    if isinstance(d, dict) and d.get("approve") is False and d.get("issues"):
        return False, f"review rejected: {d['issues']}"
    return True, ""


def _git(repo: Path, *args: str) -> tuple[int, str]:
    return sa.sh(["git", *args], repo, 120)


def _tests_green(work: Path, timeout: int) -> tuple[bool, str]:
    for cmd in (["python3", "codefactory.py", "selftest"], ["python3", "-m", "unittest", "discover", "-s", "tests"]):
        STATE.mkdir(exist_ok=True)
        sa.write_json(STATE / "cmd_now.json", {"name": "test", "cmd": " ".join(cmd), "since": time.time()})
        rc, out = sa.sh_live(cmd, work, timeout, STATE / "cmd_live.txt")
        (STATE / "cmd_now.json").unlink(missing_ok=True)
        if rc != 0:
            return False, sa.tail(out, 1500)
    return True, "ok"


def _aider(model: str, timeout: int):
    def run(work: Path, files: list[str], prompt: str) -> str:
        pf = work / ".improve_prompt.md"
        pf.write_text(prompt, "utf-8")
        cmd = ["aider", "--yes-always", "--no-auto-commits", "--no-pretty", "--no-check-update",
               "--model", model, "--message-file", str(pf), *files]
        STATE.mkdir(exist_ok=True)
        sa.write_json(STATE / "coder_now.json", {"tag": "self-change", "files": files[:12], "model": model, "since": time.time()})
        t0 = time.time()
        rc, out = sa.sh_live(cmd, work, timeout, STATE / "coder_live.txt",
                             env={"OLLAMA_API_BASE": sa.os.getenv("OLLAMA_API_BASE", "http://localhost:11434"), "PYTHONUNBUFFERED": "1"})
        for f in ("coder_now.json", "coder_live.txt"):
            (STATE / f).unlink(missing_ok=True)
        pf.unlink(missing_ok=True)
        rec = sa.LLM(sa.DEFAULT_CONFIG, lambda *_: None)
        rec.thoughts = STATE / "thoughts.jsonl"
        rec.ctx = lambda: work.name
        rec.record("self-change", "coder", model, prompt, out, "", time.time() - t0)
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
        _git(work, "-c", "user.name=codefactory", "-c", "user.email=agent@localhost", "commit", "-q", "-m",
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
    global STATE
    STATE = out_dir
    _wire(llm, stamp)
    _phase("plan", "Reading evidence from the projects and proposing improvements")

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

    try:
        return _implement(repo, proposals, coder, stamp, out_dir, log, PROTECTED, llm)
    finally:
        _phase("")


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


def _implement(repo: Path, proposals: list[dict], coder, stamp: str, out_dir: Path, log, protected: tuple, llm=None) -> dict:
    """Implements proposals one by one in a worktree on a new branch; keeps only green, non-protected changes."""
    branch, work = f"self-improve/{stamp}", out_dir / "work" / stamp
    _phase("backup", "Saving a backup of the current version")
    make_backup(repo, stamp, out_dir, log)  # always, before the new version can be merged or pushed
    rc, out = _git(repo, "worktree", "add", "-b", branch, str(work), "HEAD")
    if rc != 0:
        raise RuntimeError(f"git worktree failed: {out}")
    base = _git(work, "rev-parse", "HEAD")[1].strip()
    kept = []
    try:
        for p in proposals:
            files = [f for f in p.get("files", []) if isinstance(f, str) and (work / f).is_file()
                     and not f.startswith(protected)]
            log(f"▶ [implement] {p['title']}")
            _phase("implement", p["title"])
            files, impact = _impact(work, p, files, llm, log, protected)
            _phase("implement", p["title"])
            coder(work, files, f"{p['change']}\n\nRationale: {p.get('rationale', '')}\n{impact}\n"
                  "Write secure code: validate input, no shell injection, no secrets, never disable or loosen a safeguard. Keep the change small and focused. Do not modify existing tests; add new tests if sensible. Standard library only. "
                  "Documentation is updated in a separate step afterwards.")
            _git(work, "add", "-A")
            changes = [l for l in _git(work, "diff", "--cached", "--name-status")[1].splitlines() if l.strip()]
            bad = protected_violations(changes, protected)
            if bad:
                ok, report = False, f"touches protected files: {bad}"
            elif not changes:
                ok, report = False, "no changes"
            else:
                ok, report = _security_gate(work, p, llm, coder, log)
                if ok:
                    _phase("test", "Selftest and unit tests on the changed copy")
                    ok, report = _tests_green(work, 900)
                if ok:
                    _git(work, "add", "-A")
                    ok, report = _review(work, p, llm, log)
            if ok:
                _git(work, "-c", "user.name=codefactory", "-c", "user.email=agent@localhost", "commit", "-q", "-m",
                     f"self-improve: {p['title']}\n\n{p.get('rationale', '')}")
                kept.append(p["title"])
                log("  kept (tests green)")
                _phase("docs", "Updating README and docs")
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


def directed(instruction: str, repo: Path | None = None, llm=None, coder=None, log=print) -> dict:
    """Does exactly what the user asked with the tool itself (Makefile, scripts, UI, loop ...), on a review branch.
    No autonomy: one instruction in, one branch out, nothing merged."""
    repo = (repo or Path(__file__).resolve().parent).resolve()
    cfg = json.loads(json.dumps(sa.DEFAULT_CONFIG))
    llm = llm or sa.LLM(cfg, log)
    coder = coder or _aider(cfg["coder_model"], cfg["limits"]["coder_timeout"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = repo / "self_improve"
    out_dir.mkdir(exist_ok=True)
    global STATE
    STATE = out_dir
    _wire(llm, stamp)
    _phase("plan", "Choosing which files must change")
    tracked = sorted(l for l in _git(repo, "ls-files")[1].splitlines()
                     if not l.startswith(PROTECTED_DIRECTED) and not l.startswith("tests/") and l.count("/") <= 2)
    try:
        files = llm.json("tool_files", "planner", "You pick which files of a code repository must change for a task. Pick as few as possible.",
                         f"Task from the user:\n{instruction}\n\nFiles:\n" + "\n".join(tracked) +
                         '\nSchema: {"files": ["path", "..."]} (max 6)').get("files", [])
    except Exception as e:  # noqa: BLE001
        log(f"Could not pick files automatically: {e}")
        files = []
    files = [f for f in files if isinstance(f, str) and f in tracked][:6]
    proposal = {"title": sa.clip(instruction.strip().splitlines()[0], 80), "rationale": "Requested directly by the user.",
                "files": files, "change": instruction.strip(), "risk": "user"}
    sa.write_text(out_dir / f"task-{stamp}.md", f"# Directed task {stamp}\n\n{instruction}\n\nFiles: {files}\n")
    try:
        return _implement(repo, [proposal], coder, stamp, out_dir, log, PROTECTED_DIRECTED, llm)
    finally:
        _phase("")
