"""Agent methods that make the check pipeline faster and sharper: targeted tests first, flaky retry, a result cache,
changed-line coverage and a light mutation check. The pure helpers at the top need no agent and are unit tested."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from core.cfutil import clip, sh, sh_live, sha, tail, write_json

FEATURE_DEFAULTS = {"targeted_tests": True, "flaky_retry": True, "verify_cache": True, "coverage_gate": "off", "mutation_check": False}
TEST_FILE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|[^/]+\.(test|spec)\.[jt]sx?)$")
MUTATIONS = [(" == ", " != "), (" != ", " == "), (" >= ", " > "), (" <= ", " < "), (" > ", " >= "), (" < ", " <= "),
             (" and ", " or "), (" or ", " and "), (" + ", " - "), ("True", "False"), ("return True", "return False"), ("return False", "return True")]


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE.search(path))


def related_tests(repo: Path, changed: list[str], limit: int = 8) -> list[str]:
    """Test files that belong to a change: changed test files, tests named after a changed module and tests that import it."""
    found: list[str] = []
    tests = [str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file() and is_test_file(str(p.relative_to(repo)))
             and not any(x in p.parts for x in (".git", ".venv", "node_modules", "__pycache__"))]
    for f in changed:
        if is_test_file(f) and (repo / f).exists() and f not in found:
            found.append(f)
    for f in changed:
        if is_test_file(f):
            continue
        stem = Path(f).stem
        for t in tests:
            if t in found:
                continue
            name = Path(t).name
            if stem in name:
                found.append(t)
                continue
            try:
                if re.search(rf"\b(?:import|from)\s+[\w.]*\b{re.escape(stem)}\b|require\([^)]*{re.escape(stem)}", (repo / t).read_text("utf-8", "replace")):
                    found.append(t)
            except OSError:
                pass
    return found[:limit]


def targeted_cmd(test_cmd: str, files: list[str]) -> str | None:
    """The test command limited to the given test files. None if the runner is not supported (the full suite runs then)."""
    py = [f for f in files if f.endswith(".py")]
    if not test_cmd or not py:
        return None
    if "pytest" in test_cmd:
        return re.sub(r"\s+(?:tests?/?|\.)\s*$", "", test_cmd) + " " + " ".join(py)
    m = re.search(r"-m unittest\b", test_cmd)
    if m:
        mods = " ".join(re.sub(r"\.py$", "", f).replace("/", ".") for f in py)
        return test_cmd[:m.end()] + f" -q {mods}"
    return None


def added_lines(diff: str) -> dict[str, list[int]]:
    """Line numbers (new file) of the added lines per file of a unified diff."""
    out: dict[str, list[int]] = {}
    cur, ln = "", 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            cur = line[4:].removeprefix("b/") if line != "+++ /dev/null" else ""
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif cur and line.startswith("+"):
            out.setdefault(cur, []).append(ln)
            ln += 1
        elif cur and not line.startswith("-") and not line.startswith("\\"):
            ln += 1
    return out


def make_mutants(lines: list[str], numbers: list[int], limit: int = 5) -> list[tuple[int, str]]:
    """Up to `limit` (line number, mutated line) pairs: one operator or constant flipped in an added code line."""
    out: list[tuple[int, str]] = []
    for n in numbers:
        if not 1 <= n <= len(lines):
            continue
        text = lines[n - 1]
        s = text.strip()
        if not s or s.startswith(("#", '"', "'", "import ", "from ", "def ", "class ", "@")) or "assert" in s:
            continue
        for a, b in MUTATIONS:
            if a in text:
                out.append((n, text.replace(a, b, 1)))
                break
        if len(out) >= limit:
            break
    return out


def covered_share(cov: dict, added: dict[str, list[int]]) -> tuple[float, dict[str, list[int]]]:
    """Share of the added executable lines that ran, plus the missing lines per file (from `coverage json`)."""
    files = cov.get("files", {})
    total = hit = 0
    missing: dict[str, list[int]] = {}
    for path, nums in added.items():
        info = files.get(path)
        if not info or is_test_file(path):
            continue
        executable = set(info.get("executed_lines", [])) | set(info.get("missing_lines", []))
        for n in nums:
            if n in executable:
                total += 1
                if n in info.get("executed_lines", []):
                    hit += 1
                else:
                    missing.setdefault(path, []).append(n)
    return (hit / total if total else 1.0), missing


class VerifyMixin:
    def vfeat(self, name: str):
        return self.cfg["features"].get(name, FEATURE_DEFAULTS[name])

    # ---- cache and flaky retry around every build/test/lint command
    def tree_key(self, name: str, cmd: str) -> str:
        self.git.run("add", "-A")
        return sha(f"{name}\0{cmd}\0{self.git.out('write-tree')}")

    def run_cmd(self, name: str, cmd: str, timeout: int) -> tuple[int, str]:
        """Runs one verify command. An identical tree with an identical command that already passed is not run again; a failing
        test run is repeated once, and a pass on the second run is recorded as flaky instead of starting a debug round."""
        cache_f = self.dir / "verify_cache.json"
        key = self.tree_key(name, cmd) if self.vfeat("verify_cache") else ""
        cache: list[str] = []
        if key:
            try:
                cache = json.loads(cache_f.read_text("utf-8"))
            except (OSError, ValueError):
                cache = []
            if key in cache:
                self.log(f"▶ {name}: unchanged since the last green run, skipped")
                self.metric(kind="verify_cache_hit", cmd=name)
                return 0, "cached ok"
        write_json(self.dir / "cmd_now.json", {"name": name, "cmd": cmd, "since": time.time()})
        rc, out = sh_live(cmd, self.repo, timeout, self.dir / "cmd_live.txt", shell=True)
        if rc and name == "test" and rc != 124 and self.vfeat("flaky_retry"):
            self.log("▶ test failed, running it once more to rule out a flaky test")
            rc2, out2 = sh_live(cmd, self.repo, timeout, self.dir / "cmd_live.txt", shell=True)
            if rc2 == 0:
                self.metric(kind="flaky_test", first_failure=clip(out, 300))
                self.log("A test passed only on the second run (flaky); recorded, no debug round started.")
                rc, out = 0, out2
        (self.dir / "cmd_now.json").unlink(missing_ok=True)
        if key and rc == 0:
            write_json(cache_f, (cache + [key])[-40:])
        return rc, out

    # ---- targeted tests: fast feedback for a debug round, the full suite still runs before anything is merged
    def verify_fast(self) -> tuple[bool, str] | None:
        """Runs only the tests that belong to the change. None = not applicable (unsupported runner, no related tests)."""
        if not self.vfeat("targeted_tests"):
            return None
        files = related_tests(self.repo, self.git.changed_files())
        cmd = targeted_cmd(self.cfg["commands"].get("test", ""), files)
        if not cmd:
            return None
        self.log(f"▶ targeted tests first: {len(files)} file(s)")
        rc, out = sh_live(cmd, self.repo, self.lim["cmd_timeout"], self.dir / "cmd_live.txt", shell=True)
        if rc:
            return False, f"[test] FAILED rc={rc}\n$ {cmd}\n{tail(out, 6000)}"
        return True, "targeted tests ok"

    def verify_stage(self) -> tuple[bool, str]:
        fast = self.verify_fast()
        if fast is not None and not fast[0]:
            return fast
        return self.verify()

    # ---- coverage of the lines this change added
    def coverage_check(self, item: dict) -> None:
        g = str(self.vfeat("coverage_gate"))
        goal = int(g) if g.isdigit() else 0
        cmd = self.cfg["commands"].get("test", "")
        m = re.search(r"-m (pytest|unittest)\b", cmd)
        if not goal or not m:
            return
        base = cmd[:m.start()] + "-m coverage run --branch " + cmd[m.start():]
        py = cmd[:m.start()].strip() or sys.executable
        if sh(f"{py} -m coverage --version", self.repo, 20, shell=True)[0]:
            self.log("Coverage gate skipped: the coverage package is not installed in the project environment.")
            return
        self.phase("Checking that the new lines are covered by tests", "build")
        out_f = self.dir / "coverage.json"
        sh(f"{base} && {py} -m coverage json -q -o {out_f}", self.repo, self.lim["cmd_timeout"], shell=True)
        try:
            cov = json.loads(out_f.read_text("utf-8"))
        except (OSError, ValueError):
            return
        share, missing = covered_share(cov, added_lines(self.git.diff()))
        self.metric(kind="coverage", item=item["id"], share=round(share, 3))
        if share * 100 >= goal or not missing:
            return
        self.log(f"Coverage of the new lines: {round(share * 100)}% (goal {goal}%), one test round")
        tests = related_tests(self.repo, self.git.changed_files()) or sorted(f for f in self.git.changed_files() if is_test_file(f))
        self.code("coverage_fix", (tests + sorted(missing))[:self.lim["max_files_per_task"] + 4], [str(self.run_dir / "context.md")],
                  f"The new lines have only {round(share * 100)}% test coverage. These lines are never executed by any test: {json.dumps(missing)}.\n"
                  f"Add tests that execute them (assert real behaviour). Change no production code.\n\n{self.RULES}")

    # ---- light mutation check: do the tests notice a flipped operator in the new code?
    def mutation_check(self, item: dict) -> bool:
        """True = fine (or not applicable). Flips a few operators in added lines and expects the targeted tests to fail each time."""
        if not self.vfeat("mutation_check"):
            return True
        files = related_tests(self.repo, self.git.changed_files())
        cmd = targeted_cmd(self.cfg["commands"].get("test", ""), files)
        if not cmd:
            return True
        self.phase("Mutation check: do the tests notice broken code?", "build")
        added = {f: n for f, n in added_lines(self.git.diff()).items() if f.endswith(".py") and not is_test_file(f) and (self.repo / f).exists()}
        survived: list[str] = []
        tried = 0
        for f, nums in added.items():
            p = self.repo / f
            original = p.read_text("utf-8")
            lines = original.splitlines(keepends=True)
            for n, new in make_mutants([x.rstrip("\n") for x in lines], nums, 3):
                mutated = lines[:n - 1] + [new + ("\n" if lines[n - 1].endswith("\n") else "")] + lines[n:]
                tried += 1
                try:
                    p.write_text("".join(mutated), "utf-8")
                    rc, _ = sh(cmd, self.repo, min(self.lim["cmd_timeout"], 120), shell=True)
                finally:
                    p.write_text(original, "utf-8")
                if rc == 0:
                    survived.append(f"{f}:{n}  `{lines[n - 1].strip()}` -> `{new.strip()}`")
        self.metric(kind="mutation", item=item["id"], tried=tried, survived=len(survived))
        if not tried or len(survived) * 2 < tried:
            return True
        self.log(f"Mutation check: {len(survived)} of {tried} broken variants were not noticed by any test, one test round")
        tests = related_tests(self.repo, self.git.changed_files()) or sorted(f for f in self.git.changed_files() if is_test_file(f))
        self.code("mutation_fix", tests[:self.lim["max_files_per_task"]], [str(self.run_dir / "context.md")],
                  "These deliberate bugs were NOT detected by any test (the tests still passed):\n" + "\n".join(survived) +
                  f"\nAdd or sharpen tests so each of them would fail. Change no production code.\n\n{self.RULES}")
        ok, _ = self.debug_loop(item)
        return ok
