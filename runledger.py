"""Run ledger and preview verification for self-changes (standard library only).

Ledger: every self-improve run writes `self_improve/runs/<stamp>.json` with the user's requirements as a checklist, the plan,
the stage of every proposal (impact, implement, tests added, security, test, review, docs) and the result. The UI shows it
in the "Plan" tab, so you always see what was asked, what is done and what is still open.

verify_checkout: proves that the preview really runs the branch you want to merge (right commit, clean tree, changed files
identical to the branch, code compiles, the running server reports that commit and serves that UI, new routes answer).
"""
from __future__ import annotations

import hashlib
import json
import py_compile
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STAGES = ("impact", "implement", "tests_added", "security", "test", "review", "docs")
REQ_STATES = ("open", "done", "partial", "missing", "unverified", "failed")


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


class Ledger:
    """One run. Saving never raises: bookkeeping must not break a self-change."""

    def __init__(self, out_dir: Path, stamp: str, kind: str, instruction: str = ""):
        self.path = Path(out_dir) / "runs" / f"{stamp}.json"
        self.d = {"id": stamp, "kind": kind, "instruction": instruction, "status": "planning", "created": time.time(),
                  "updated": time.time(), "requirements": [], "proposals": [], "branch": None, "base": None, "head": None,
                  "files": [], "tests_added": [], "summary": ""}
        self.save()

    def save(self) -> None:
        self.d["updated"] = time.time()
        try:
            _write(self.path, self.d)
        except OSError:
            pass

    def status(self, status: str, summary: str = "") -> None:
        self.d["status"] = status
        if summary:
            self.d["summary"] = summary
        self.save()

    def set_requirements(self, texts: list[str]) -> None:
        self.d["requirements"] = [{"id": i + 1, "text": str(t)[:400], "status": "open", "evidence": ""}
                                  for i, t in enumerate(texts[:12]) if str(t).strip()]
        self.save()

    def mark_requirement(self, rid: int, status: str, evidence: str = "") -> None:
        for r in self.d["requirements"]:
            if r["id"] == rid and status in REQ_STATES:
                r["status"], r["evidence"] = status, str(evidence)[:300]
        self.save()

    def add_proposal(self, title: str) -> int:
        self.d["proposals"].append({"title": title, "status": "pending", "note": "", "plan": "",
                                    "stages": {s: "pending" for s in STAGES}})
        self.save()
        return len(self.d["proposals"]) - 1

    def proposal(self, i: int, status: str = "", note: str = "", plan: str = "") -> None:
        p = self.d["proposals"][i]
        p["status"] = status or p["status"]
        p["note"] = note or p["note"]
        p["plan"] = plan or p["plan"]
        self.save()

    def stage(self, i: int, stage: str, state: str, note: str = "") -> None:
        """state: running / ok / failed / skipped. A failed stage also explains why in the proposal note."""
        p = self.d["proposals"][i]
        p["stages"][stage] = state
        if note and state == "failed":
            p["note"] = note[:500]
        self.save()

    def finish(self, status: str, **info) -> None:
        self.d.update({k: v for k, v in info.items() if v is not None})
        self.status(status)


def active_run(out_dir: Path) -> dict | None:
    """The newest run that is still planning or working (None if nothing is in flight)."""
    for f in sorted((Path(out_dir) / "runs").glob("*.json"), reverse=True)[:3]:
        try:
            d = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("status") in ("planning", "working"):
            return d
    return None


def mark_run(out_dir: Path, run_id: str, status: str, summary: str = "") -> None:
    d = load_run(out_dir, run_id)
    if d:
        d["status"], d["updated"] = status, time.time()
        if summary:
            d["summary"] = summary
        _write(Path(out_dir) / "runs" / f"{run_id}.json", d)


def list_runs(out_dir: Path, limit: int = 15) -> list[dict]:
    rows = []
    for f in sorted((Path(out_dir) / "runs").glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        reqs = d.get("requirements", [])
        rows.append({"id": d.get("id", f.stem), "kind": d.get("kind"), "status": d.get("status"), "branch": d.get("branch"),
                     "title": (d.get("instruction") or "").strip().splitlines()[0][:90] if d.get("instruction") else d.get("kind", ""),
                     "done": sum(1 for r in reqs if r.get("status") == "done"), "total": len(reqs), "updated": d.get("updated")})
    return rows


def load_run(out_dir: Path, run_id: str) -> dict | None:
    if not re.fullmatch(r"[\w.-]{1,40}", run_id or ""):
        return None
    f = Path(out_dir) / "runs" / f"{run_id}.json"
    try:
        return json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def run_for_branch(out_dir: Path, branch: str) -> dict | None:
    for f in sorted((Path(out_dir) / "runs").glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("branch") == branch:
            return d
    return None


def _git(cwd: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout.strip()


def describe_branch(repo: Path, base: str, branch: str) -> dict:
    """Changed files and the new test files of a branch relative to its base (for the ledger and the preview checks)."""
    rc, out = _git(repo, "diff", "--name-status", f"{base}..{branch}")
    files, tests = [], []
    for line in out.splitlines() if rc == 0 else []:
        parts = line.split("\t")
        path = parts[-1]
        files.append(path)
        if parts[0][:1] == "A" and path.startswith("tests/") and path.endswith(".py"):
            tests.append(path)
    return {"files": files, "tests_added": tests, "head": _git(repo, "rev-parse", branch)[1]}


def _get(port: int, path: str, timeout: float = 5.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (OSError, ValueError):
        return 0, b""


def verify_checkout(repo: Path, tree: Path, branch: str, port: int, wait: float = 20.0, alive=lambda: True) -> list[dict]:
    """Checks that the preview at `tree`/`port` is really the branch. Returns [{name, ok, detail}]."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    tip = _git(repo, "rev-parse", branch)[1]
    head = _git(tree, "rev-parse", "HEAD")[1]
    add("checkout is the branch tip", bool(tip) and head == tip, f"{head[:8]} vs {tip[:8]}")
    dirty = _git(tree, "status", "--porcelain", "--untracked-files=no")[1]
    add("no stray local changes", not dirty, dirty[:200])
    rc, names = _git(repo, "diff", "--name-only", f"HEAD...{branch}")
    changed = [n for n in names.splitlines() if n.strip()] if rc == 0 else []
    bad = []
    for n in changed:
        f = tree / n
        want = _git(repo, "rev-parse", f"{branch}:{n}")[1]
        if f.is_file() and want and _git(tree, "hash-object", n)[1] != want:
            bad.append(n)
        if not f.exists() and want:
            bad.append(n)
    add(f"all {len(changed)} changed file(s) present and identical", not bad, ", ".join(bad[:6]))
    broken = []
    for n in changed:
        if n.endswith(".py") and (tree / n).is_file():
            try:
                py_compile.compile(str(tree / n), cfile=str(tree / n) + "c.check", doraise=True)
            except py_compile.PyCompileError as e:
                broken.append(f"{n}: {str(e)[:100]}")
            finally:
                Path(str(tree / n) + "c.check").unlink(missing_ok=True)
    add("changed Python files compile", not broken, "; ".join(broken))

    deadline, status, body = time.time() + wait, 0, b""
    while time.time() < deadline:
        status, body = _get(port, "/api/build")
        if status or not alive():
            break
        time.sleep(0.5)
    legacy = status == 404  # a branch cut before /api/build existed: the server is up, but cannot name its commit
    if legacy:
        status = _get(port, "/api/projects")[0]
    if not add("preview server answers", status == 200, "" if status == 200 else "no answer; see the preview log"):
        return checks
    if legacy:
        add("server runs exactly this commit", True, "not verifiable: this branch is older than the /api/build check")
    else:
        try:
            info = json.loads(body.decode("utf-8"))
        except ValueError:
            info = {}
        add("server runs exactly this commit", bool(tip) and info.get("sha") == tip, f"server reports {str(info.get('sha'))[:8]}")
    ui = tree / "web" / "index.html"
    code, page = _get(port, "/")
    add("served UI is the branch's UI", ui.is_file() and code == 200 and hashlib.sha1(page).hexdigest() == hashlib.sha1(ui.read_bytes()).hexdigest(),
        f"HTTP {code}")
    smoke = {p: _get(port, p)[0] for p in ("/api/tool", "/api/projects", "/api/dashboard")}
    add("core API routes answer", all(s == 200 for s in smoke.values()), ", ".join(f"{p} {s}" for p, s in smoke.items() if s != 200))
    rc, patch = _git(repo, "diff", "-U0", f"HEAD...{branch}", "--", "*.py")
    routes = sorted(set(re.findall(r'^\+.*?"(/api/[A-Za-z0-9_/.-]+)"', patch if rc == 0 else "", re.M)))[:8]
    if routes:  # informational: POST-only routes legitimately answer a GET with 404/405
        add("new routes in this branch", True, ", ".join(f"{r} → {_get(port, r.rstrip('/'))[0]}" for r in routes))
    return checks


if __name__ == "__main__":
    print(json.dumps(verify_checkout(Path.cwd(), Path.cwd(), sys.argv[1], int(sys.argv[2])), indent=1))
