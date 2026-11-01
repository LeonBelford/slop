"""Self-improvement of the agent tool itself (`python3 codefactory.py improve`, `make self-improve`).

Evidence from all projects (tool ideas from reflection, playbooks, success/failure statistics) is turned into a few
concrete code improvements. They are implemented in a separate git worktree on a branch `self-improve/<timestamp>`:

  * the running agent code is never touched, nothing is merged automatically
  * every proposal is committed only if `selftest` and the unit tests stay green
  * safety-critical files (web fetch guard, UI guard, this module, existing tests, CI) are off limits; a proposal that
    changes them is reverted
  * every run keeps a ledger (self_improve/runs/<ts>.json, runledger.py): the requirements as a checklist, the plan, the stage of
    every proposal and the verification result; the UI shows it in the "Plan" tab. A change without new tests gets one extra
    round to add them
  * PROJECT_MAP.md (complete project description) is mandatory: it is part of every planning prompt and passed to Aider
    with --read; without it the run aborts
  * you review the branch (`git diff main...self-improve/<ts>`) and merge it yourself

Proposals are always written to self_improve/proposals-<ts>.md, even with --dry-run.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

import codefactory as sa
from core import progress
from dash import estimate, toolmodels
from infra import models
from infra import perf
from dash import runledger
from infra import security

PROTECTED = ("infra/webaccess.py", "infra/security.py", "webui.py", "selfimprove.py", ".github/", ".env", "scripts/quick-setup-popos.sh")
# Directed tasks (user-written instruction) may also change the web UI server; the guards for fetching and this module stay locked.
PROTECTED_DIRECTED = ("infra/webaccess.py", "infra/security.py", "selfimprove.py", ".github/", ".env", "scripts/quick-setup-popos.sh")
MAX_PROPOSALS = 3
MAP_FILE = "PROJECT_MAP.md"  # complete description of this repository; read before every plan and every change


def _is_tool(repo: Path) -> bool:
    """True for this very repository; copies used by embedders and tests may lack the map."""
    return Path(repo).resolve() == Path(__file__).resolve().parent


def project_map(repo: Path, required: bool = True, limit: int = 14000) -> str:
    """The project map. Self-improve refuses to plan without it."""
    f = Path(repo) / MAP_FILE
    text = f.read_text("utf-8", "replace").strip() if f.is_file() else ""
    if not text and required:
        raise RuntimeError(f"{MAP_FILE} is missing. It describes the whole project and must be read before every change; restore it from git.")
    return sa.clip(text, limit)


def protected_violations(changes: list[str], protected: tuple = PROTECTED, edit_tests: bool = False) -> list[str]:
    """changes: lines of `git diff --name-status`. Existing tests may not change (edit_tests=True, for requests you wrote yourself,
    allows adapting them but never deleting or renaming); new test files are welcome."""
    bad = []
    for line in changes:
        parts = line.split("\t")
        status, paths = parts[0][:1], parts[1:]
        if status in "DR" and MAP_FILE in paths[:1]:
            bad.append(MAP_FILE)
        for path in paths:
            if path.startswith(protected) or (path.startswith("tests/") and status != "A" and not (edit_tests and status == "M")):
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


_TOOL_PROGRESS = progress.Tracker()


def _phase(step: str, detail: str = "") -> None:
    """Publishes the current stage (plan / implement / test / docs) for the UI; an empty step clears it."""
    f = STATE / "phase.json"
    if not step:
        f.unlink(missing_ok=True)
        return
    STATE.mkdir(exist_ok=True)
    pct, steps, durations = _TOOL_PROGRESS.update(progress.TOOL_PIPELINE, "run", step, step, detail)
    sa.write_json(f, {"step": step, "detail": detail, "since": time.time(), "pct": pct, "steps": steps, "durations": durations})


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
        mod = re.escape(Path(f).stem)
        pat = re.compile(rf"^\s*(?:import\s+(?:\w+\.)*{mod}\b|from\s+(?:\w+\.)*{mod}\b|from\s+[\w.]+\s+import\s+[^\n]*\b{mod}\b)", re.M)
        out[f] = sorted(str(p.relative_to(work)) for p in work.rglob("*.py") if str(p.relative_to(work)) != f and ".venv" not in p.parts
                        and pat.search(p.read_text("utf-8", "replace")))
    return out


def _impact(work: Path, p: dict, files: list[str], llm, log, protected: tuple) -> tuple[list[str], str]:
    """Think before changing: consequences, dependents and invariants (plan + critique), like the project agent does."""
    if llm is None:
        return files, ""
    _phase("impact", "Analysing what this change can break")
    deps = _dependents(work, files)
    pmap = project_map(work, required=False, limit=5000)
    src = "\n\n".join(f"### {f}\n{sa.clip((work / f).read_text('utf-8', 'replace'), 3500)}" for f in files[:4])
    try:
        plan = llm.json("tool_impact", "planner",
                        "You are a tech lead for an autonomous coding agent. Before every change you analyse relationships: who imports the files, "
                        "which invariants apply, what can break, and what the change exposes security-wise. Plan minimally. "
                        "Base your analysis on the project map.",
                        f"Project map (whole repository):\n{pmap}\n\nChange: {p['title']}\n{p['change']}\nFiles: {files}\nImported by: {json.dumps(deps)}\nSource:\n{src}\nSchema: "
                        '{"risks": ["..."], "affected_dependents": ["paths"], "invariants": ["..."], "tests_to_add": ["..."], "security_impact": ""}')
        # the critique runs on the planner model (no model swap) and only for changes that touch several files or have dependents
        crit = (llm.json("tool_plan_critique", "planner", "You strictly critique plans for overlooked dependents and broken invariants.",
                         f"Plan: {json.dumps(plan)}\nImported by: {json.dumps(deps)}\nSchema: "
                         '{"ok": true, "missing_files": ["..."], "concerns": ["..."]}')
                if len(files) > 1 or any(deps.values()) else {"ok": True, "missing_files": [], "concerns": []})
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
               "--model", model, "--message-file", str(pf)]
        if (work / MAP_FILE).is_file():
            cmd += ["--read", MAP_FILE]  # the coder sees the whole-project map for every edit
        cmd += perf.aider_args(model, STATE)
        cmd += ["--edit-format", sa.os.getenv("AIDER_EDIT_FORMAT", "diff"), "--map-tokens", "1024"]  # diff: the model writes only the changed parts
        cmd += files if prompt.startswith("Documentation update") else _fit(work, files)
        STATE.mkdir(exist_ok=True)
        sa.write_json(STATE / "coder_now.json", {"tag": "self-change", "files": files[:12], "model": model, "since": time.time()})
        t0 = time.time()
        rc, out = sa.sh_live(cmd, work, timeout, STATE / "coder_live.txt",
                             env={"OLLAMA_API_BASE": sa.os.getenv("OLLAMA_API_BASE", "http://localhost:11434"), "PYTHONUNBUFFERED": "1"},
                             idle=True)  # the timeout only counts silence: Aider is never cut off while it is still writing
        for f in ("coder_now.json", "coder_live.txt"):
            (STATE / f).unlink(missing_ok=True)
        pf.unlink(missing_ok=True)
        rec = sa.LLM(sa.DEFAULT_CONFIG, lambda *_: None)
        rec.thoughts = STATE / "thoughts.jsonl"
        rec.ctx = lambda: work.name
        rec.record("self-change", "coder", model, prompt, out, "", time.time() - t0)
        return out
    cache: dict[str, object] = {}
    run.use = lambda name: cache.setdefault(name, _aider(models.coder_string(name), timeout))  # coder on another installed model
    run.model = model
    run.routes = None
    return run


def _docs_pass(work: Path, proposal: dict, coder, log) -> None:
    """After a kept change, brings README.md and docs/ in line with it and logs it in docs/SELF_IMPROVEMENTS.md."""
    diff = _git(work, "show", "--stat", "--patch", "HEAD")[1]
    doc_files = [f for f in (MAP_FILE, "README.md", "docs/GUIDE.md", "docs/ARCHITECTURE.md", "docs/SELF_IMPROVEMENTS.md")
                 if (work / f).is_file()]
    if not (work / "docs" / "SELF_IMPROVEMENTS.md").exists():
        doc_files.append("docs/SELF_IMPROVEMENTS.md")
        sa.write_text(work / "docs" / "SELF_IMPROVEMENTS.md", "# Self-improvements\n\nChanges the agent made to itself, newest last.\n")
    coder(work, doc_files,
          f"Documentation update for this change: {proposal['title']}\n\n{proposal.get('rationale', '')}\n\n"
          f"Diff:\n{sa.clip(diff, 6000)}\n\nUpdate PROJECT_MAP.md (layout, routes, config keys, commands, invariants; it must stay a "
          "complete and accurate map of the whole repository), README.md, docs/GUIDE.md and docs/ARCHITECTURE.md only where the change affects "
          "what they say (usage, commands, config, behavior). Keep the existing structure and language (English). Append one "
          "entry to docs/SELF_IMPROVEMENTS.md: what changed, why, which files. Change documentation files only.")
    _git(work, "add", "-A")
    changes = [l for l in _git(work, "diff", "--cached", "--name-status")[1].splitlines() if l.strip()]
    only_docs = all(l.split("\t")[-1] in ("README.md", MAP_FILE) or l.split("\t")[-1].startswith("docs/") for l in changes)
    if changes and only_docs:
        _git(work, "-c", "user.name=codefactory", "-c", "user.email=agent@localhost", "commit", "-q", "-m",
             f"self-improve: docs for {proposal['title']}")
        log("  docs updated")
    else:
        _git(work, "reset", "-q", "--hard", "HEAD")
        _git(work, "clean", "-qfd")
        log("  docs pass produced no valid change (reverted)" if changes else "  docs pass: nothing to change")


def main(projects: Path, dry_run: bool = False, repo: Path | None = None, llm=None, coder=None, log=print, model: str = "") -> dict:
    repo = (repo or Path(__file__).resolve().parent).resolve()
    cfg = toolmodels.apply(json.loads(json.dumps(sa.DEFAULT_CONFIG)), repo / "self_improve", model)
    llm = llm or sa.LLM(cfg, log)
    coder = coder or _aider(cfg["coder_model"], cfg["limits"]["coder_timeout"])
    routes = _route(llm)
    if not sa.LLM.mock and isinstance(llm, sa.LLM):
        for f in perf.diagnose():
            if f["level"] != "ok":
                log(f"⚠ speed: {f['text']}")
    if getattr(coder, "use", None) and getattr(coder, "routes", None) is None:
        coder.routes = routes
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = repo / "self_improve"
    out_dir.mkdir(exist_ok=True)
    global STATE
    STATE = out_dir
    _wire(llm, stamp)
    _phase("plan", "Reading evidence from the projects and proposing improvements")
    ledger = runledger.Ledger(out_dir, stamp, "auto", "Autonomous improvement from the evidence of all projects")

    evidence = gather_evidence(projects)
    if not any(p["tool_ideas"] or p["failed_attempts"] for p in evidence["projects"]):
        log("No evidence yet: let at least one project run and reflect first (it records tool ideas every few merges).")
        ledger.status("failed", "No evidence yet")
        return {"branch": None, "proposals": []}
    pmap = project_map(repo, required=_is_tool(repo))  # mandatory for the real tool: without the map there is no plan
    code_files = sorted(str(p.relative_to(repo)) for p in [*repo.glob("*.py"), *repo.glob("core/*.py"), *repo.glob("infra/*.py"), *repo.glob("dash/*.py"), *repo.glob("webapi/*.py")]
                        if not str(p.relative_to(repo)).startswith(PROTECTED)) + ["scripts/run.sh"]
    d = llm.json("improve_plan", "planner",
                 "You improve an autonomous coding agent (this repository). Propose small, safe, high-value changes to the "
                 "agent's own code: better prompts, smarter heuristics, more robust error handling, better context selection. "
                 "Prefer fixing observed failures over adding features. Never propose changes to safety guards. "
                 "First study the project map: it describes the whole repository, its data flow and its invariants.",
                 f"Project map (whole repository):\n{pmap}\n\nEvidence from running projects:\n{sa.clip(json.dumps(evidence, ensure_ascii=False), 9000)}\n"
                 f"Editable files: {code_files}\nPropose at most {MAX_PROPOSALS}. Schema: "
                 '{"proposals": [{"title": "...", "rationale": "which evidence", "files": ["..."], "change": "precise instructions", '
                 '"risk": "low|medium|high"}]}')
    proposals = [p for p in d.get("proposals", []) if isinstance(p, dict) and p.get("title") and p.get("change")][:MAX_PROPOSALS]
    md = [f"# Self-improvement proposals {stamp}\n"] + [
        f"## {i}. {p['title']} (risk: {p.get('risk', '?')})\n{p.get('rationale', '')}\n\nFiles: {p.get('files', [])}\n\n{p['change']}\n"
        for i, p in enumerate(proposals, 1)]
    sa.write_text(out_dir / f"proposals-{stamp}.md", "\n".join(md))
    log(f"{len(proposals)} proposals written to self_improve/proposals-{stamp}.md")
    ledger.set_requirements([f"{p['title']}: {p.get('rationale', '')}" for p in proposals])
    if dry_run or not proposals:
        ledger.status("done", "Proposals only" if proposals else "No proposals")
        return {"branch": None, "proposals": proposals}

    try:
        return _implement(repo, proposals, coder, stamp, out_dir, log, PROTECTED, llm, ledger)
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


def _changes(work: Path) -> list[str]:
    _git(work, "add", "-A")
    return [l for l in _git(work, "diff", "--cached", "--name-status")[1].splitlines() if l.strip()]


def _needs_tests(changes: list[str]) -> tuple[bool, bool]:
    """(code changed, a new test file is part of the change)."""
    paths = [(l.split("\t")[0][:1], l.split("\t")[-1]) for l in changes]
    code = any(not f.startswith(("tests/", "docs/")) and f.endswith((".py", ".html", ".sh")) for _, f in paths)
    new = any(st == "A" and f.startswith("tests/") and f.endswith(".py") for st, f in paths)
    return code, new


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


FIX_ROUNDS = 2  # repair rounds after the first attempt: the failure is diagnosed and handed back to the coder


def _route(llm) -> dict:
    """Which installed model does which job: light coder for docs, standard for normal steps, heavy for hard steps and the last
    repair round, strongest reviewer for review/verification/diagnosis. Missing models fall back to the defaults, nothing is required."""
    installed = getattr(llm, "installed", None) or set()
    if isinstance(llm, sa.LLM) and not sa.LLM.mock and not installed:
        installed = llm.installed = models.installed_from_ollama(sa.os.getenv("OLLAMA_HOST_URL", "http://localhost:11434")) or set()
    cfg = getattr(llm, "cfg", None) or sa.DEFAULT_CONFIG  # the saved model choice (self_improve/models.json) lives in the LLM's config
    r = models.resolve(installed, cfg["routing"], cfg["coder_model"])
    big = sa.os.getenv("CODER_MODEL_BIG")
    if big and not r["heavy"]:
        r["heavy"] = big
    if r["final_reviewer"] and isinstance(llm, sa.LLM):
        llm.overrides["reviewer"] = r["final_reviewer"]  # review, plan critique and requirement checks use the strongest reviewer
    return r


CONTEXT_TOKENS = 7000  # budget for the files Aider may edit; the pinned model window (perf.NUM_CTX, 16k) also holds map, prompt and answer


def _fit(work: Path, files: list[str], budget: int = CONTEXT_TOKENS) -> list[str]:
    """Only files that need edits, as many as fit the context budget. Docs and the project map are read-only (and updated in the docs pass)."""
    out, used = [], 0
    for f in dict.fromkeys(files):
        if f == MAP_FILE or f.startswith("docs/") or f.endswith(".md") and f != "README.md" or not (work / f).is_file():
            continue
        tokens = (work / f).stat().st_size // 3
        if out and used + tokens > budget:
            continue  # the first file is always kept (edited with the compact diff format), later big ones wait for their own step
        out.append(f)
        used += tokens
    return out


def _plain(name: str) -> str:
    return str(name or "default").split("/", 1)[-1] if str(name or "").startswith("ollama") else str(name or "default")


def _learned_cls(coder, cls: str, rnd: int) -> tuple[str, str]:
    """Adjusts the job class by what the track record says (needs a few attempts per model before it speaks up):
    a default coder that keeps failing gets replaced by the heavy one, a heavy model that is not better is not worth the model swap."""
    routes = getattr(coder, "routes", None) or {}
    heavy, default = routes.get("heavy"), _plain(getattr(coder, "model", ""))
    if not heavy or rnd >= 2:
        return cls, ""
    s = runledger.model_stats(STATE)
    d, h = s.get(default, {"tries": 0, "ok": 0}), s.get(_plain(heavy), {"tries": 0, "ok": 0})
    rd, rh = d["ok"] / d["tries"] if d["tries"] else 1, h["ok"] / h["tries"] if h["tries"] else 0
    if cls == "standard" and d["tries"] >= 3 and h["tries"] >= 2 and rd < 0.4 and rh - rd >= 0.25:
        return "heavy", f"{default} succeeded {d['ok']}/{d['tries']}, {heavy} {h['ok']}/{h['tries']}"
    if cls == "heavy" and h["tries"] >= 4 and rh <= rd and rh < 0.5:
        return "standard", f"{heavy} is not better ({h['ok']}/{h['tries']} vs {d['ok']}/{d['tries']}), saving the model swap"
    return cls, ""


def _coder_for(coder, cls: str):
    """The coder function for a job class (light | standard | heavy); the default coder when no better model is installed."""
    routes, use = getattr(coder, "routes", None), getattr(coder, "use", None)
    name = (routes or {}).get({"light": "light", "heavy": "heavy"}.get(cls, ""))
    return use(name) if name and use else coder


def _lessons() -> str:
    f = STATE / "lessons.md"
    return f.read_text("utf-8", "replace")[-1500:] if f.is_file() else ""


def _remember(title: str, stage: str, report: str, diag: str, fixed: bool) -> None:
    """Failure notebook that later prompts read: what went wrong, and what (if anything) fixed it."""
    try:
        STATE.mkdir(exist_ok=True)
        with open(STATE / "lessons.md", "a", encoding="utf-8") as f:
            f.write(f"- [{'fixed' if fixed else 'failed'}] {title}: stage {stage}: {sa.clip(report, 200)!r} {sa.clip(diag, 250)!r}\n")
    except OSError:
        pass


def _keyword_files(repo: Path, text: str, tracked: list[str], have: list[str], limit: int = 3) -> list[str]:
    """Content-based file candidates: files that mention the words of the request most often (cheap code search, no LLM)."""
    words = {w for w in re.findall(r"[a-zA-Z_]{5,}", text.lower()) if w not in {"should", "would", "please", "which", "there", "their", "about"}}
    score = {}
    for f in tracked:
        if f in have or not f.endswith((".py", ".html", ".md", ".sh")):
            continue
        try:
            body = (repo / f).read_text("utf-8", "replace").lower()
        except OSError:
            continue
        n = sum(min(body.count(w), 5) for w in words)
        if n:
            score[f] = n
    return sorted(score, key=score.get, reverse=True)[:limit]


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


def _clean_changes(work: Path, protected: tuple, directed: bool, log, notes: list) -> tuple[list[str], list[str]]:
    """Current changes. Directed runs get protected files restored automatically (and may adapt existing tests);
    autonomous runs treat any protected change as a reason to reject the whole proposal."""
    changes = _changes(work)
    bad = protected_violations(changes, protected, edit_tests=directed)
    if bad and directed:
        _restore(work, bad)
        notes.append(f"restored protected file(s): {bad}")
        log(f"  restored protected file(s): {bad}")
        changes = _changes(work)
        bad = protected_violations(changes, protected, edit_tests=directed)
    return changes, bad


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


def _gates(work: Path, p: dict, files: list[str], llm, coder, log, ledger, pi: int, protected: tuple, directed: bool, state: dict) -> tuple[bool, str, str]:
    """One pass over all gates for the current state of the worktree. Returns (ok, report, failed stage)."""
    changes, bad = _clean_changes(work, protected, directed, log, state["notes"])
    if bad:
        return False, f"touches protected files: {bad}", "protected"
    if not changes:
        out = sa.tail(str(state.get("coder_out") or ""), 700).strip()
        return False, "the coder made no usable edit" + (f". Last Aider output: {out}" if out else ""), "implement"
    code, has_tests = _needs_tests(changes)
    if code and not has_tests and not state["tests_round"]:
        state["tests_round"] = True
        _phase("implement", "Adding tests for the change")
        ledger.stage(pi, "tests_added", "running")
        coder(work, [f for f in files if (work / f).is_file()][:4],
              f"Add tests for this change: {p['title']}.\nCreate a NEW unittest file in tests/ (standard library only) that "
              "checks the new behavior and would fail without it. Do not modify existing tests and do not change non-test code.")
        changes, bad = _clean_changes(work, protected, directed, log, state["notes"])
        if bad:
            return False, f"touches protected files: {bad}", "protected"
        has_tests = _needs_tests(changes)[1]
    ledger.stage(pi, "implement", "ok")
    ledger.stage(pi, "tests_added", "ok" if has_tests else ("warn" if code else "skipped"), "" if has_tests else "no new test file was written")
    edited = [l.split("\t")[-1] for l in changes if l[:1] == "M" and l.split("\t")[-1].startswith("tests/")]
    if edited:
        ledger.d["proposals"][pi]["tests_modified"] = edited
        ledger.save()
    ledger.stage(pi, "security", "running")
    ok, report = _security_gate(work, p, llm, coder, log)
    ledger.stage(pi, "security", "ok" if ok else "failed", report)
    if not ok:
        return False, report, "security"
    _phase("test", "Selftest and unit tests on the changed copy")
    ledger.stage(pi, "test", "running")
    red = _new_tests_red(work, changes)
    if red:
        ledger.stage(pi, "tests_added", "ok", red)
    ok, report = _fast_tests(work, changes)  # quick feedback first, the full suite only follows when this is green
    review: dict = {}
    if ok:
        # the review is a model call (GPU) and the full suite is CPU work: run them side by side instead of one after the other
        _git(work, "add", "-A")
        ledger.stage(pi, "review", "running")
        t = threading.Thread(target=lambda: review.update(res=_review(work, p, llm, log)), daemon=True)
        t.start()
        ok, report = _tests_green(work, 900)
        t.join()
    ledger.stage(pi, "test", "ok" if ok else "failed", report)
    if not ok:
        return False, report, "test"
    ok, report = review.get("res", (True, ""))
    ledger.stage(pi, "review", "ok" if ok else "failed", report)
    return ok, report, "" if ok else "review"


def _work_on(work: Path, p: dict, files: list[str], impact: str, llm, coder, log, ledger, pi: int, protected: tuple,
             out_dir: Path, stamp: str) -> tuple[bool, str]:
    """Implement, check, and when a check fails: diagnose, hand the problem back to the coder and try again (FIX_ROUNDS times)."""
    directed = ledger.d["kind"] == "directed"
    state = {"tests_round": False, "notes": []}
    test_rule = ("Existing tests may only be adapted where the new behavior requires it; never delete a test or loosen an assertion. "
                 if directed else "Do not modify existing tests; add new tests if sensible. ")
    prompt = (f"{p['change']}\n\nRationale: {p.get('rationale', '')}\n{impact}\n"
              "Write secure code: validate input, no shell injection, no secrets, never disable or loosen a safeguard. "
              f"Keep the change focused. {test_rule}{('Lessons from earlier failed attempts (avoid repeating them):\\n' + _lessons() + chr(10)) if _lessons() else ''}Standard library only. Documentation is updated in a separate step afterwards.")
    ok, report, stage, last = False, "", "", None
    for rnd in range(FIX_ROUNDS + 1):
        _phase("implement", p["title"] if rnd == 0 else f"{p['title']} (repair round {rnd}: {stage})")
        ledger.stage(pi, "implement", "running")
        cls = models.classify("feature", max(2, len(files)), rnd, complex_files=sa.DEFAULT_CONFIG["routing"]["complex_files"])
        cls = "standard" if cls == "light" else cls  # real changes never go to the light model
        cls, why = _learned_cls(coder, cls, rnd)
        if why:
            log(f"  routing by track record: {cls} ({why})")
        run = _coder_for(coder, cls)
        used = _plain(coder.routes["heavy"]) if run is not coder else _plain(getattr(coder, "model", ""))
        if run is not coder:
            log(f"  model: {coder.routes['heavy']} (hard step / late repair round)")
            ledger.d["proposals"][pi].setdefault("models", []).append(f"round {rnd + 1}: {coder.routes['heavy']}")
            ledger.save()
        state["coder_out"] = run(work, [f for f in dict.fromkeys(files + [l.split("\t")[-1] for l in _changes(work) if not l.split("\t")[-1].startswith("tests/")]) if (work / f).is_file()][:8], prompt)
        ok, report, stage = _gates(work, p, files, llm, coder, log, ledger, pi, protected, directed, state)
        ledger.d["proposals"][pi].setdefault("attempts", []).append({"round": rnd + 1, "model": used, "class": cls, "ok": ok, "stage": stage})
        ledger.save()
        if ok or stage == "protected" and not directed:
            break
        if stage == "implement":
            ledger.stage(pi, "implement", "failed", report)
        ledger.d["proposals"][pi]["rounds"] = rnd + 1
        ledger.proposal(pi, note=f"round {rnd + 1} failed at {stage}: {report[:300]}")
        if rnd == FIX_ROUNDS:
            break
        log(f"  round {rnd + 1} failed at {stage}; diagnosing and repairing ({rnd + 1}/{FIX_ROUNDS})")
        _phase("implement", f"Diagnosing the failure ({stage})")
        diag = _diagnose(llm, p, stage, report, work)
        ledger.d["proposals"][pi].setdefault("round_log", []).append({"round": rnd + 1, "stage": stage, "problem": sa.clip(report, 400), "diagnosis": diag})
        ledger.save()
        last = (p["title"], stage, report, diag)
        prompt = (f"Repair round {rnd + 1}. Your previous change failed at the '{stage}' stage.\nProblem:\n{sa.clip(report, 2500)}\n"
                  f"{diag}\nFix the code so every check passes. The original task is still:\n{p['change']}\n{test_rule}"
                  "Never remove or loosen a safeguard to make a check pass. Standard library only."
                  + (" Your last answer changed no file: reply only with edit blocks in the requested format, no explanations, "
                     "and copy the SEARCH lines exactly from the file." if stage == "implement" else ""))
    if last and ok:
        _remember(*last, fixed=True)
    elif last:
        _remember(*last, fixed=False)
    if not ok:
        patch = _git(work, "diff", "--cached", "HEAD")[1]
        if patch.strip():
            sa.write_text(out_dir / f"failed-{stamp}-{pi + 1}.patch", patch)  # kept for inspection, not applied anywhere
            report += f" (the last attempt is saved in self_improve/failed-{stamp}-{pi + 1}.patch)"
    elif state["notes"]:
        ledger.proposal(pi, note="; ".join(state["notes"]))
    return ok, report


def _implement(repo: Path, proposals: list[dict], coder, stamp: str, out_dir: Path, log, protected: tuple, llm=None, ledger=None) -> dict:
    ledger = ledger or runledger.Ledger(out_dir, stamp, "auto")
    if not ledger.d["requirements"]:
        ledger.set_requirements([p["title"] for p in proposals])
    try:
        return _implement_run(repo, proposals, coder, stamp, out_dir, log, protected, llm, ledger)
    except Exception as e:  # noqa: BLE001
        ledger.status("failed", f"{type(e).__name__}: {e}")
        raise


def _implement_run(repo: Path, proposals: list[dict], coder, stamp: str, out_dir: Path, log, protected: tuple, llm, ledger) -> dict:
    """Implements proposals one by one in a worktree on a new branch; keeps only green, non-protected changes."""
    ledger.status("working")
    branch, work = f"self-improve/{stamp}", out_dir / "work" / stamp
    problems = estimate.write_problems([out_dir, out_dir / "work", out_dir / "backups", repo / ".git"])
    if problems:
        raise RuntimeError("self-improve needs write access: " + "; ".join(problems))
    _phase("backup", "Saving a backup of the current version")
    make_backup(repo, stamp, out_dir, log)  # always, before the new version can be merged or pushed
    rc, out = _git(repo, "worktree", "add", "-b", branch, str(work), "HEAD")
    if rc != 0:
        raise RuntimeError(f"git worktree failed: {out}")
    base = _git(work, "rev-parse", "HEAD")[1].strip()
    kept = []
    try:
        shared = None
        for p in proposals:
            pi = ledger.add_proposal(p["title"])
            ledger.proposal(pi, "running")
            t0 = time.time()
            est = estimate.estimate(estimate.load(out_dir / "durations.jsonl"), p["title"], "feature", p.get("rationale", ""))
            if est["seconds"]:
                log(f"  estimate: ~{round(est['seconds'] / 60)} min ({est['basis']}, {est['n']} comparable steps)")
            files = [f for f in p.get("files", []) if isinstance(f, str) and (work / f).is_file()
                     and not f.startswith(protected)]
            log(f"▶ [implement] {p['title']}")
            _phase("implement", p["title"])
            ledger.stage(pi, "impact", "running")
            if pi > 0 and p.get("risk") == "user" and shared:
                files, impact = shared  # steps of one request share the analysis: no repeated planning before the code is written
                log("  impact: reused from the first step")
            else:
                files, impact = _impact(work, p, files, llm, log, protected)
                shared = (list(files), impact) if p.get("risk") == "user" else None
            ledger.stage(pi, "impact", "ok" if impact else "skipped")
            ledger.proposal(pi, plan=f"Files: {files}\n{sa.clip(impact, 1500)}")
            ok, report = _work_on(work, p, files, impact, llm, coder, log, ledger, pi, protected, out_dir, stamp)
            if ok:
                _git(work, "-c", "user.name=codefactory", "-c", "user.email=agent@localhost", "commit", "-q", "-m",
                     f"self-improve: {p['title']}\n\n{p.get('rationale', '')}")
                kept.append(p["title"])
                ledger.proposal(pi, "kept")
                estimate.record(out_dir / "durations.jsonl", p["title"], time.time() - t0, "feature", 1, True, "self", p.get("rationale", ""))
                log("  kept (tests green)")
                _phase("docs", "Updating README and docs")
                ledger.stage(pi, "docs", "running")
                _docs_pass(work, p, _coder_for(coder, "light"), log)
                ledger.stage(pi, "docs", "ok")
            else:
                _git(work, "reset", "-q", "--hard", "HEAD")
                _git(work, "clean", "-qfd")
                ledger.proposal(pi, "reverted", note=report[:500])
                log(f"  reverted: {report}")
    finally:
        _git(repo, "worktree", "remove", "--force", str(work))
        shutil.rmtree(work, ignore_errors=True)
    if not kept:
        _git(repo, "branch", "-D", branch)
        _verify_requirements(repo, base, branch, ledger, llm, log)
        ledger.finish("failed", summary="No proposal survived the checks. Nothing to review.", base=base)
        log("No proposal survived the checks. Nothing to review.")
        return {"branch": None, "proposals": proposals}
    _verify_requirements(repo, base, branch, ledger, llm, log)
    ledger.finish("done", branch=branch, base=base, summary=f"{len(kept)} of {len(proposals)} proposal(s) kept",
                  **runledger.describe_branch(repo, base, branch))
    log(f"Branch {branch} has {len(kept)} commit(s). Review: git diff main...{branch}   Merge: git merge {branch}")
    return {"branch": branch, "proposals": proposals, "kept": kept, "base": base}


if __name__ == "__main__":
    main(Path("projects"))


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


def directed(instruction: str, repo: Path | None = None, llm=None, coder=None, log=print, model: str = "") -> dict:
    """Does exactly what the user asked with the tool itself (Makefile, scripts, UI, loop ...), on a review branch.
    No autonomy: one instruction in, one branch out, nothing merged."""
    repo = (repo or Path(__file__).resolve().parent).resolve()
    cfg = toolmodels.apply(json.loads(json.dumps(sa.DEFAULT_CONFIG)), repo / "self_improve", model)
    llm = llm or sa.LLM(cfg, log)
    coder = coder or _aider(cfg["coder_model"], cfg["limits"]["coder_timeout"])
    routes = _route(llm)
    if not sa.LLM.mock and isinstance(llm, sa.LLM):
        for f in perf.diagnose():
            if f["level"] != "ok":
                log(f"⚠ speed: {f['text']}")
    if getattr(coder, "use", None) and getattr(coder, "routes", None) is None:
        coder.routes = routes
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = repo / "self_improve"
    out_dir.mkdir(exist_ok=True)
    global STATE
    STATE = out_dir
    _wire(llm, stamp)
    _phase("plan", "Choosing which files must change")
    ledger = runledger.Ledger(out_dir, stamp, "directed", instruction.strip())
    requirements = _requirements(llm, instruction, log)
    pmap = project_map(repo, required=_is_tool(repo))  # mandatory for the real tool: without the map there is no plan
    tracked = sorted(l for l in _git(repo, "ls-files")[1].splitlines()
                     if not l.startswith(PROTECTED_DIRECTED) and not l.startswith("tests/") and l.count("/") <= 2)
    try:
        files = llm.json("tool_files", "planner", "You pick which files of a code repository must change for a task. Pick as few as possible. "
                         "Use the project map to see how the parts depend on each other.",
                         f"Project map (whole repository):\n{pmap}\n\nTask from the user:\n{instruction}\n\nFiles:\n" + "\n".join(tracked) +
                         '\nSchema: {"files": ["path", "..."]} (max 6)').get("files", [])
    except Exception as e:  # noqa: BLE001
        log(f"Could not pick files automatically: {e}")
        files = []
    files = [f for f in files if isinstance(f, str) and f in tracked][:6]
    files += _keyword_files(repo, instruction, tracked, files, max(0, 6 - len(files)) or 0)
    steps = requirements[:5] if len(requirements) > 1 else []
    base_title = sa.clip(instruction.strip().splitlines()[0], 60)
    if steps:
        # one small step per requirement: each is checked and committed on its own, passing steps are kept even if a later one fails
        proposals = [{"title": f"{base_title} [{i}/{len(steps)}]", "rationale": "Requested directly by the user.", "files": files,
                      "change": f"Overall request from the user (context):\n{instruction.strip()}\n\nImplement ONLY this step now "
                                f"(step {i} of {len(steps)}; earlier steps are already done):\n{r}\nWrite a failing-first test for it where sensible.",
                      "risk": "user"} for i, r in enumerate(steps, 1)]
    else:
        proposals = [{"title": sa.clip(instruction.strip().splitlines()[0], 80), "rationale": "Requested directly by the user.",
                      "files": files, "change": instruction.strip(), "risk": "user"}]
    sa.write_text(out_dir / f"task-{stamp}.md", f"# Directed task {stamp}\n\n{instruction}\n\nFiles: {files}\n")
    ledger.set_requirements(requirements)
    try:
        return _implement(repo, proposals, coder, stamp, out_dir, log, PROTECTED_DIRECTED, llm, ledger)
    finally:
        _phase("")
