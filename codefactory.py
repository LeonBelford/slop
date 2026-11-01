#!/usr/bin/env python3
"""CodeFactory: local autonomous agent team that builds, tests, debugs, cleans up and documents open-source software.

Only the start idea comes from you. After that an outer loop runs and decides on its own
(the model does NOT decide when to finish):

  health -> backlog empty? -> product review / ideation (+critic) -> pick task
  -> impact analysis with memory (codemap, dependency graph, ADRs, lessons)
  -> plan critique -> implementation (Aider) -> build+tests -> debug rounds
  -> review -> cleanup pass (only with a green build) -> update memory+docs
  -> commit -> merge into main -> build on main (if red: rollback)

Layout (one folder per idea under projects/<name>/):
  repo/     Git repo of the product (incl. docs/)
  agent/    State of this project OUTSIDE the repo so that `git reset` never destroys it:
            config.json state.json backlog.json GOAL.md memory/ runs/ agent.log metrics.jsonl STOP

Usage:
  python codefactory.py init   --idea "Invoicing tool for freelancers"   # creates projects/<name>/
  python codefactory.py run    --project <name>
  python codefactory.py status --project <name>
  python codefactory.py list
  python codefactory.py stop   --project <name>     # clean stop after the current task
  python codefactory.py selftest                    # mechanics test with a stub LLM, no Ollama needed

Standard library only. Requires: git, aider (pip install aider-chat), an OpenAI-compatible
LLM endpoint (Ollama by default). Details: README.md and docs/.
"""

from __future__ import annotations

from pathlib import Path
from webaccess import Web
from webaccess import WebError
import argparse
import ast
import difflib
import e2e
import gpusched
import gzip
import hashlib
import json
import knowledge
import models
import os
import perf
import re
import security
import shlex
import shutil
import signal
import stacks
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from cfutil import (  # noqa: F401
    DEFAULT_CONFIG,
    IDEAS_TEMPLATE,
    LENSES,
    MASTER_TEMPLATE,
    PRESETS,
    QUALITY_CHECKLIST,
    SOURCES_TEMPLATE,
    TUNABLE,
    _children,
    _open_think,
    _strip_think,
    _zombie,
    aider_tokens,
    clip,
    compact_thoughts,
    force_stop_pid,
    last_seq,
    now,
    parse_json,
    process_tree,
    read_ideas,
    read_json,
    read_master,
    read_sources,
    sh,
    sh_live,
    sha,
    tail,
    words,
    write_json,
    write_text,
)
from cfllm import (  # noqa: F401
    LLM,
)
from cfgit import (  # noqa: F401
    Backlog,
    Git,
)
from cfmemory import (  # noqa: F401
    Memory,
)
from cfa_build import BuildMixin
from cfa_security import SecurityMixin
from cfa_research import ResearchMixin
from cfa_ops import OpsMixin


class Agent(BuildMixin, SecurityMixin, ResearchMixin, OpsMixin):
    def __init__(self, ws: Path, cfg: dict | None = None):
        self.ws = ws.resolve()
        self.repo, self.dir = self.ws / "repo", self.ws / "agent"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or self.load_config()
        self.lim = self.cfg["limits"]
        for k, v in read_json(self.dir / "tuning.json", {}).items():  # self-tuned values, bounded
            if k in TUNABLE:
                self.lim[k] = max(TUNABLE[k][0], min(TUNABLE[k][1], int(v)))
        self.llm = LLM(self.cfg, self.log)
        self.llm.trace = self.dir / "llm.log"
        self.llm.live = self.dir / "llm_now.json"
        self.llm.thoughts = self.dir / "thoughts.jsonl"
        self.llm.stream = self.dir / "llm_stream.json"
        self.llm.ctx = lambda: self.cur_task
        self.llm.sched = gpusched.make(self.cfg, self.ws.name, self.log, self.dir / "sched.json")
        self.cur_task, self.cur_attempt = "", 0
        self.git = Git(self.repo)
        self.backlog = Backlog(self.dir / "backlog.json")
        self.mem = Memory(self.dir, self.repo, self.cfg, self.llm, self.log)
        self.state = read_json(self.dir / "state.json", {
            "iteration": 0, "elapsed": 0.0, "merged": 0, "consecutive_failures": 0, "empty_refills": 0,
            "req_hash": "", "ideation_min_score": self.lim["ideation_min_score"], "last_review_at": 0,
            "last_refactor_at": 0, "last_coverage_at": 0, "last_arch_at": 0, "last_security_at": 0})
        self.run_dir = self.dir
        self.ideas_path = self.ws / "IDEAS.md"
        self.sources_path = self.ws / "SOURCES.md"
        self.web = Web(self.cfg["web"])
        self.web_dir = self.dir / "web"
        self.master_path = self.ws.parent.parent / "MASTER.md"
        self.coder_mock = None

    # ---- Configuration/logging
    def load_config(self) -> dict:
        path = self.dir / "config.json"
        user = read_json(path, {})
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        preset = PRESETS.get(cfg["preset"], PRESETS["fastapi"])
        cfg["commands"] = {**preset["commands"], **cfg.get("commands", {})}
        cfg["stack"] = user.get("stack", preset["stack"])
        cfg["source_exts"] = user.get("source_exts", preset["source_exts"])
        cfg["src_dirs"] = user.get("src_dirs", preset["src_dirs"])
        return cfg

    def log(self, msg: str) -> None:
        line = f"[{now()}] {msg}"
        print(line, flush=True)
        with open(self.dir / "agent.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def phase(self, name: str, step: str | None = None, detail: str = "") -> None:
        """Publishes what the agent is doing right now (activity.json) and appends it to the event timeline (events.jsonl)."""
        write_json(self.dir / "activity.json", {"phase": name, "step": step, "detail": detail[:300], "since": time.time(),
                                                "task": self.cur_task, "attempt": self.cur_attempt, "pid": os.getpid()})
        self.log(f"▶ [{step or 'agent'}] {name}" + (f" ({detail[:120]})" if detail else ""))
        with open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": now(), "phase": name, "step": step, "detail": detail[:300], "task": self.cur_task,
                                "attempt": self.cur_attempt}, ensure_ascii=False) + "\n")

    def save_state(self) -> None:
        write_json(self.dir / "state.json", self.state)

    def metric(self, **kw) -> None:
        with open(self.dir / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": now(), **kw}, ensure_ascii=False) + "\n")

    @property
    def directive(self) -> str:
        """Master prompt of this session as a text block for prompts (empty if none is set)."""
        p = self.dir / "master_session.md"
        out = "\n\nMaster prompt (binding for all projects):\n" + clip(p.read_text("utf-8"), 2000) if p.exists() else ""
        return out + self.playbook

    @property
    def playbook(self) -> str:
        """Working rules the agent derived from its own experience on this project (see reflect())."""
        p = self.dir / "playbook.md"
        return "\n\nPlaybook (your own lessons from earlier work, follow them):\n" + clip(p.read_text("utf-8"), 2500) if p.exists() else ""

    @property
    def goal(self) -> str:
        p = self.dir / "GOAL.md"
        return p.read_text("utf-8") if p.exists() else ""

    # ---- Bootstrap
    def choose_stack(self, idea: str) -> None:
        """Picks the best language/framework among the installed toolchains and stores it in config.json."""
        avail = [k for k in stacks.public_stacks() if not stacks.missing_tools(k)]
        if not avail:
            sys.exit("No supported toolchain found.\n" + "\n".join(stacks.install_help(k) for k in stacks.public_stacks()[:3])
                     + "\nInstall at least one language toolchain (see README, section Languages).")
        pick = avail[0] if "fastapi" not in avail else "fastapi"
        if len(avail) > 1:
            try:
                d = self.llm.json("stack", "planner",
                                  "You choose the technology stack for a new open-source project. Prefer the stack that fits the idea best, performs well and "
                                  "is easy for contributors to maintain. Only choose from the given options.",
                                  f"Idea: {idea}\nOptions: {json.dumps({k: PRESETS[k]['stack'] for k in avail})}\n"
                                  '{"stack": "<one option key>", "reason": "<one sentence>"}')
                if d.get("stack") in avail:
                    pick = d["stack"]
                    self.log(f"Stack chosen: {pick} ({d.get('reason', '')})")
            except Exception as e:  # noqa: BLE001
                self.log(f"Stack selection failed, using {pick}: {e}")
        conf = read_json(self.dir / "config.json", {})
        conf["preset"] = pick
        write_json(self.dir / "config.json", conf)
        self.cfg.update(self.load_config())

    def init(self, idea: str) -> None:
        if (self.dir / "GOAL.md").exists():
            self.log("Workspace already initialized.")
            return
        self.llm.preflight()
        if not (self.dir / "config.json").exists():
            write_json(self.dir / "config.json", {k: self.cfg[k] for k in ("preset", "doc_language", "models", "coder_model")})
        if self.cfg["preset"] == "auto":
            self.choose_stack(idea)
        miss = stacks.install_help(self.cfg["preset"])
        if miss:
            sys.exit(miss)
        self.git.ensure()
        if not self.ideas_path.exists():
            write_text(self.ideas_path, IDEAS_TEMPLATE.format(idea=idea))
        if not self.sources_path.exists():
            write_text(self.sources_path, SOURCES_TEMPLATE)
        for rel, content in stacks.scaffold(self.cfg["preset"]).items():
            write_text(self.repo / rel, content)
        write_text(self.repo / ".gitignore", stacks.gitignore(self.cfg["preset"]))
        self.git.commit_all("scaffold")
        brief = self.llm.json(
            "brief", "planner", "You are a senior engineer and product designer building the best open-source project in its category: fast, cleanly written, great experience for everyone. Realistic, focused, start small. No monetization.",
            f"Idea: {idea}\nStack: {self.cfg['stack']}\nSchema: "
            '{"name": "", "vision": "", "target_users": ["..."], "problems": ["..."], "mvp_features": '
            '[{"title": "", "description": "", "acceptance": ["testable"], "value": 1-10, "effort": 1-10}], '
            '"non_goals": ["..."], "performance_goals": ["measurable"], "quality_bar": ["..."]}')
        goal = (f"# {brief.get('name', 'Product')}\n\nIdea: {idea}\n\n## Vision\n{brief.get('vision', '')}\n\n"
                f"## Target audience\n" + "".join(f"- {x}\n" for x in brief.get("target_users", [])) +
                "\n## Problems\n" + "".join(f"- {x}\n" for x in brief.get("problems", [])) +
                "\n## Performance goals\n" + "".join(f"- {x}\n" for x in brief.get("performance_goals", [])) + "\n## Non-goals\n" +
                "".join(f"- {x}\n" for x in brief.get("non_goals", [])) + "\n## Quality bar\n" +
                "".join(f"- {x}\n" for x in brief.get("quality_bar", [])) + f"\n## Stack\n{self.cfg['stack']}\n")
        write_text(self.dir / "GOAL.md", goal)
        write_text(self.repo / "docs" / "PRODUCT.md", goal)
        for f in brief.get("mvp_features", []):
            self.backlog.add(f["title"], f.get("description", ""), "feature",
                             float(f.get("value", 5)) * 2 - float(f.get("effort", 5)),
                             f.get("acceptance", []), "MVP", "bootstrap")
        self.mem.sync()
        self.mem.refresh_kb()
        self.mem.refresh_architecture()
        self.mem.refresh_readme(goal)
        self.git.commit_all("docs: initial product brief and architecture")
        self.save_state()
        self.log(f"Initialized: {len(self.backlog.items)} MVP tasks in the backlog.")

    RULES = ("Rules: Change only what is necessary for the task. Respect the stated invariants. Adapt all "
             "affected callers. Write/update tests (happy path and error cases). Small, typed "
             "functions with docstrings, no dead code leftovers, no new dependencies without an entry in the project's dependency manifest. "
             "Security by default: no secrets or API keys in code (environment variables, documented in .env.example), "
             "passwords only as salted argon2id/scrypt/bcrypt hashes, parameterized SQL, validate and bound all input, escape output, "
             "no eval/exec/shell=True/unsafe deserialization, confine file paths, constant-time secret comparison, generic error messages. "
             "The project must build and all tests must pass after your change.")

    def should_stop(self, started: float) -> str | None:
        if (self.dir / "STOP").exists():
            return "STOP file found"
        endless = self.cfg["run"]["mode"] == "endless"
        if not endless and self.state["iteration"] >= self.lim["max_iterations"]:
            return "max_iterations reached"
        if not endless and self.state["elapsed"] + time.time() - started > self.lim["max_hours"] * 3600:
            return "max_hours reached"
        if self.state["consecutive_failures"] >= self.lim["max_consecutive_failures"]:
            return "too many consecutive failures, please check the logs"
        return None

    def refill(self) -> bool:
        added = 0
        self.state["rounds_run"] = self.state.get("rounds_run", 0) + 1
        self.research()
        if self.state["merged"] - self.state["last_review_at"] >= self.lim["product_review_every"] or not self.mem.features:
            added += self.product_review()
        if not added:
            added += self.ideate()
        if added:
            self.state["empty_refills"] = 0
            return True
        self.state["empty_refills"] += 1
        self.state["ideation_min_score"] = max(3, self.state["ideation_min_score"] - 1)
        if self.cfg["run"]["mode"] == "endless":
            self.log(f"No new ideas, threshold {self.state['ideation_min_score']}. Endless mode: waiting 2 minutes for your ideas/tasks, then trying again.")
            for _ in range(24):
                if (self.dir / "STOP").exists():
                    break
                time.sleep(5)
            return True
        self.log(f"No new ideas ({self.state['empty_refills']}/3), lowering threshold to {self.state['ideation_min_score']}")
        return self.state["empty_refills"] < 3

    def run(self) -> None:
        if not (self.dir / "GOAL.md").exists():
            sys.exit("Workspace not initialized: run `init --idea ...` first.")
        miss = stacks.install_help(self.cfg["preset"])
        if miss:
            sys.exit(miss)
        self.llm.preflight()
        (self.dir / "STOP").unlink(missing_ok=True)
        master = read_master(self.master_path)
        if master:
            write_text(self.dir / "master_session.md", master)
            self.log(f"Master prompt from {self.master_path} adopted for this session.")
        else:
            (self.dir / "master_session.md").unlink(missing_ok=True)
        write_text(self.dir / "agent.pid", str(os.getpid()))
        try:
            self._run_loop()
        finally:
            (self.dir / "agent.pid").unlink(missing_ok=True)

    def _run_loop(self) -> None:
        for it in self.backlog.items:  # resume tasks of an aborted run
            if it["status"] == "in_progress":
                it["status"] = "pending"
        self.backlog.save()
        self.state["empty_refills"] = 0  # fresh start: try ideas again after `stop`/saturation
        self.state["rounds_run"] = 0
        started = time.time()
        self.git.hard_reset()
        self.health_check()
        reason = None
        while True:
            self.state["elapsed"] += 0  # placeholder, the real sum is added when saving
            reason = self.should_stop(started)
            if reason:
                break
            self.state["iteration"] += 1
            if self.state["iteration"] % self.lim["health_every"] == 0:
                self.health_check()
            self.sync_ideas()
            self.sync_sources()
            if self.reflect_due():
                self.reflect()
            self.schedule_hygiene()
            item = self.backlog.next()
            if not item:
                run = self.cfg["run"]
                if run["mode"] == "rounds" and self.state.get("rounds_run", 0) >= int(run["rounds"]):
                    reason = f"Finished {run['rounds']} idea round(s) and the backlog is empty"
                    break
                if not self.refill():
                    reason = "Product saturated: no sensible new ideas left"
                    break
                self.save_state()
                continue
            ok = self.process(item)
            if not ok:
                self.state["failed_since_reflect"] = self.state.get("failed_since_reflect", 0) + 1
            self.state["consecutive_failures"] = 0 if ok else self.state["consecutive_failures"] + 1
            self.save_state()
        self.state["elapsed"] += time.time() - started
        self.save_state()
        self.cur_task, self.cur_attempt = "", 0
        self.phase("Stopped", None, str(reason))
        self.log(f"End: {reason}")


# --------------------------------------------------------------------------- Selftest


def selftest() -> None:
    """Checks the loop mechanics with a stub LLM and stub coder (no Ollama/Aider needed)."""
    ws = Path(tempfile.mkdtemp(prefix="codefactory-selftest-"))
    counter = {"n": 0}

    def fake_llm(tag, role, system, user):
        if tag == "brief":
            return json.dumps({"name": "Demo", "vision": "v", "mvp_features": [
                {"title": "Greeting endpoint", "description": "greet", "acceptance": ["test"], "value": 8, "effort": 2}]})
        if tag == "summarize":
            return json.dumps({"purpose": "demo module", "public_api": ["f()"], "invariants": ["pure"]})
        if tag == "select_files":
            return json.dumps({"files": ["app/main.py"]})
        if tag == "impact":
            return json.dumps({"understanding": "u", "files_to_change": ["app/main.py"], "new_files": [], "steps": ["s"],
                               "invariants": ["health stays ok"], "tests_to_add": ["t"]})
        if tag == "reflect":
            return json.dumps({"playbook": "- keep tasks small\n- run tests first", "tuning": {"max_debug_rounds": 4, "evil_key": 99},
                               "tool_improvements": ["review prompt too lenient"]})
        if tag == "web_summary":
            return json.dumps({"summary": "- the doc says use feature X", "relevant": True})
        if tag == "web_queries":
            return json.dumps({"queries": ["fastapi best practices"]})
        if tag == "replan":
            return json.dumps({"summary": "new wishes", "new_items": [{"title": "Dark mode toggle", "description": "d",
                               "type": "feature", "acceptance": ["x"], "priority": 7}], "reprioritize": [], "cancel": []})
        if tag == "plan_critique":
            return json.dumps({"ok": True, "missing_files": [], "concerns": []})
        if tag == "security_review":
            return json.dumps({"findings": []})
        if tag == "review":
            return json.dumps({"score": 8, "verdict": "approve", "summary": "added things", "issues": []})
        if tag == "decision":
            return json.dumps({"title": "t", "context": "c", "decision": "d", "alternatives": "a", "consequences": "q"})
        if tag in ("ideate",):
            counter["n"] += 1
            return json.dumps({"ideas": [{"title": ["Export invoices as CSV", "Team workspace roles", "Usage based billing page",
                                          "Public REST API keys", "Weekly email digest"][counter["n"] % 5], "description": "d",
                                          "type": "feature", "value": 9, "effort": 2, "risk": 1, "acceptance": ["x"]}]})
        if tag == "ideate_critic":
            return json.dumps({"scores": []})
        if tag == "product_review":
            return json.dumps({"scores": {"ux": 5}, "items": []})
        if tag == "lesson":
            return json.dumps({"lesson": "avoid bad things"})
        return "# generated markdown"

    def fake_coder(repo, tag, edit, read, prompt):
        if tag == "implement":
            n = len(list((repo / "app").glob("feat_*.py"))) + 1
            (repo / "app" / f"feat_{n}.py").write_text(f"from app.main import health\n\n\ndef feat_{n}() -> str:\n    return health()\n")
            (repo / "tests" / f"test_feat_{n}.py").write_text(
                f"import unittest\nfrom app.feat_{n} import feat_{n}\n\n\nclass T(unittest.TestCase):\n"
                f"    def test(self):\n        self.assertEqual(feat_{n}(), 'ok')\n")
        return ""

    LLM.mock = fake_llm
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["preset"] = "minimal"
    cfg["commands"] = PRESETS["minimal"]["commands"]
    cfg["stack"] = PRESETS["minimal"]["stack"]
    cfg["src_dirs"] = PRESETS["minimal"]["src_dirs"]
    cfg["web"]["mode"] = "open"
    cfg["limits"]["max_iterations"] = 6
    cfg["limits"]["reflect_every"] = 2
    cfg["limits"]["architecture_refresh_every"] = 1
    a = Agent(ws, cfg)
    a.coder_mock = fake_coder
    a.web.mock_fetch = lambda url: ("Docs", f"content of {url}")
    a.web.mock_search = lambda q: [("https://example.com/found", "Found")]
    a.init("demo idea")
    with open(ws / "SOURCES.md", "a", encoding="utf-8") as f:
        f.write("\nhttps://example.com/docs   use this for payments\n")
    with open(ws / "IDEAS.md", "a", encoding="utf-8") as f:
        f.write("\n- Make dark mode switchable\n")
    a.run()
    assert any(i["source"] == "ideas" for i in a.backlog.items), "IDEAS.md must lead to backlog entries"
    assert "User directives (from IDEAS.md)" in a.goal
    assert "keep tasks small" in (a.dir / "playbook.md").read_text("utf-8"), "reflection must write a playbook"
    tuned = read_json(a.dir / "tuning.json", {})
    assert tuned == {"max_debug_rounds": 4}, f"only whitelisted limits may be tuned: {tuned}"
    assert (a.dir / "tool_ideas.jsonl").exists(), "reflection must record tool improvement ideas"
    ev = [json.loads(l) for l in (a.dir / "events.jsonl").read_text("utf-8").splitlines()]
    assert {"plan", "implement", "build", "review", "merge"} <= {e["step"] for e in ev}, "workflow steps must be published"
    t = a.add_user_task("Add a /about endpoint")
    assert a.backlog.next()["id"] == t["id"], "user tasks go first"
    assert a.run_user_tasks() == 1 and t["status"] == "done", "directed task must run alone"
    digest = (a.dir / "web" / "digest.md").read_text("utf-8")
    assert "example.com/docs" in digest and "use this for payments" in digest, "SOURCES.md must be fetched into the digest"
    assert "example.com/found" in digest, "open mode must store searched pages"
    print(a.status())
    assert a.state["merged"] >= 3, "several features should have been merged"
    assert a.git.out("status", "--porcelain") == "", "working directory must be clean"
    assert (ws / "repo" / "docs" / "ARCHITECTURE.md").exists() and (ws / "repo" / "CHANGELOG.md").exists()
    assert (ws / "repo" / "docs" / "modules").exists() and a.mem.codemap["app/feat_1.py"]["imports"] == ["app/main.py"]
    assert "app/feat_1.py" in a.mem.codemap["app/main.py"]["imported_by"], "reverse graph missing"
    print("SELFTEST OK ->", ws)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return "-".join(s.strip("-").split("-")[:5])[:40] or "project"


def unique_slug(root: Path, text: str) -> str:
    slug = slugify(text)
    n, cand = 2, slug
    while (root / cand).exists():
        cand, n = f"{slug}-{n}", n + 1
    return cand


def list_projects(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*") if (p / "agent" / "GOAL.md").exists())


def pick_project(root: Path, name: str | None) -> Path:
    if name:
        ws = root / name
        if not (ws / "agent").exists():
            sys.exit(f"Project '{name}' not found in {root}/. Available: {[p.name for p in list_projects(root)]}")
        return ws
    found = list_projects(root)
    if len(found) != 1:
        sys.exit(f"Please specify --project. Available: {[p.name for p in found]}")
    return found[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "run", "status", "stop", "force-stop"):
        p = sub.add_parser(name)
        p.add_argument("--projects-dir", default="projects", help="Folder containing all projects")
        if name == "init":
            p.add_argument("--idea", required=True)
            p.add_argument("--name", help="Project folder (default: derived from the idea)")
            p.add_argument("--preset", default="auto", choices=["auto"] + stacks.public_stacks() + ["minimal"],
                           help="auto picks the best installed language/framework for the idea")
            p.add_argument("--config", help="Path to a config.json (see config.example.json)")
        else:
            p.add_argument("--project", help="Project name (optional if there is only one project)")
    p_try = sub.add_parser("try", help="Pause the agent, build a test version and print start instructions")
    p_say = sub.add_parser("say", help="Add feedback/wish to IDEAS.md")
    for q in (p_try, p_say):
        q.add_argument("--projects-dir", default="projects")
        q.add_argument("--project")
    p_say.add_argument("--msg", required=True)
    p_do = sub.add_parser("do", help="Do exactly what you ask in this project (queues the task; runs it if the agent is idle)")
    p_do.add_argument("--projects-dir", default="projects")
    p_do.add_argument("--project")
    p_do.add_argument("--task", required=True)
    p_do.add_argument("--queue-only", action="store_true")
    p_name = sub.add_parser("name", help="Print the resolved project name")
    p_name.add_argument("--projects-dir", default="projects")
    p_name.add_argument("--project")
    p_list = sub.add_parser("list")
    p_list.add_argument("--projects-dir", default="projects")
    sub.add_parser("selftest")
    p_imp = sub.add_parser("improve", help="Let the agent improve its own code on a review branch (never auto-merged)")
    p_imp.add_argument("--projects-dir", default="projects")
    p_imp.add_argument("--dry-run", action="store_true", help="Only write proposals, change no code")
    p_imp.add_argument("--task", help="Not autonomous: do exactly this instruction with the tool itself, on a review branch")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    root = Path(a.projects_dir)
    if a.cmd == "improve":
        import selfimprove
        if a.task:
            selfimprove.directed(a.task)
            return
        return selfimprove.main(root, dry_run=a.dry_run)
    if a.cmd == "list":
        for p in list_projects(root):
            print(f"{p.name}: {Agent(p).status()}")
        return
    if a.cmd == "init":
        if a.preset != "auto" and stacks.install_help(a.preset):
            sys.exit(stacks.install_help(a.preset))
        ws = root / unique_slug(root, a.name or a.idea)
        (ws / "agent").mkdir(parents=True, exist_ok=True)
        base = read_json(Path(a.config), {}) if a.config else {}
        write_json(ws / "agent" / "config.json", {"preset": a.preset, **base})
        print(f"Project: {ws}")
        return Agent(ws).init(a.idea)
    ws = pick_project(root, a.project)
    if a.cmd == "do":
        ag = Agent(ws)
        item = ag.add_user_task(a.task)
        print(f"Queued {item['id']}: {item['title']}")
        if not a.queue_only and not ag.is_running():
            ag.run_user_tasks()
        return
    if a.cmd == "name":
        print(ws.name)
        return
    if a.cmd == "try":
        print(Agent(ws).try_version())
        return
    if a.cmd == "say":
        Agent(ws).add_feedback(a.msg)
        print("Added to IDEAS.md. The agent will re-plan on its next pass.")
        return
    if a.cmd == "force-stop":
        print(Agent(ws).force_stop())
        return
    if a.cmd == "stop":
        write_text(ws / "agent" / "STOP", "stop\n")
        print(f"STOP set for {ws.name}")
    elif a.cmd == "run":
        Agent(ws).run()
    else:
        print(Agent(ws).status())


if __name__ == "__main__":
    main()
