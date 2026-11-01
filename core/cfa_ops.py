"""Agent methods: user tasks, stop/force-stop, test version, feedback and status."""
from __future__ import annotations

import os
import shlex
import sys
import time
from core.cfutil import PRESETS, clip, force_stop_pid, now, read_json, sh, write_text


class OpsMixin:
    """Agent mixin: user-submitted tasks, stop/force-stop, building a test version, feedback and status."""

    def reload_models(self) -> None:
        """Picks up model choices saved in the UI while the agent runs (called before every task).

        Updates the shared cfg dicts in place, because the LLM client and the router hold the same references.
        """
        conf = read_json(self.dir / "config.json", {})
        changed = []
        for role, name in (conf.get("models") or {}).items():
            if isinstance(name, str) and (name or role == "strategist") and self.cfg["models"].get(role) != name:
                changed.append(f"{role}: {self.cfg['models'].get(role)} -> {name}")
                self.cfg["models"][role] = name
        coder = conf.get("coder_model")
        if isinstance(coder, str) and coder and self.cfg["coder_model"] != coder:
            changed.append(f"coder: {self.cfg['coder_model']} -> {coder}")
            self.cfg["coder_model"] = coder
        if isinstance(conf.get("routing"), dict) and self.cfg["routing"] != {**self.cfg["routing"], **conf["routing"]}:
            self.cfg["routing"].update(conf["routing"])
            changed.append("routing settings")
        if changed:
            self.log("Model settings reloaded: " + "; ".join(changed))

    # ---- Directed work: exactly what the user asks, nothing else
    def add_user_task(self, text: str) -> dict:
        text = text.strip()
        title = clip(text.splitlines()[0], 90)
        return self.backlog.add(title, text, "feature", 100.0, ["Does exactly what was asked, nothing more",
                                                                 "Build and tests green"], "Requested directly by the user", "user",
                                force=True)  # an explicit request is never dropped as a duplicate

    def run_user_tasks(self) -> int:
        """Processes only the tasks the user queued (source "user") and then stops. No ideation, no autonomy."""
        if self.is_running():
            sys.exit("The agent is running already; your task is queued and will be picked up next.")
        self.llm.preflight()
        write_text(self.dir / "agent.pid", str(os.getpid()))
        done = 0
        try:
            self.git.hard_reset()
            while True:
                items = [i for i in self.backlog.pending() if i.get("source") == "user"]
                if not items:
                    break
                item = max(items, key=lambda i: i["priority"])
                with self.conducting(item):
                    done += bool(self.record_outcome(item, self.process(item)))
                self.save_state()
        finally:
            (self.dir / "agent.pid").unlink(missing_ok=True)
            self.cur_task = ""
            self.phase("Idle (directed tasks finished)", None)
        return done

    # ---- Interactive: build a testable version
    def is_running(self) -> bool:
        try:
            pid = int((self.dir / "agent.pid").read_text())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def force_stop(self) -> str:
        """Kill switch: ends the running agent immediately, mid-task. The next start resets the repo and re-queues the task."""
        write_text(self.dir / "STOP", "force\n")
        try:
            pid = int((self.dir / "agent.pid").read_text())
        except (OSError, ValueError):
            pid = 0
        killed = force_stop_pid(pid) if pid else []
        sh(["tmux", "kill-session", "-t", f"agent-{self.ws.name}"], self.dir)
        for f in ("agent.pid", "llm_now.json", "coder_now.json", "llm_stream.json", "coder_live.txt", "cmd_now.json", "sched.json"):
            (self.dir / f).unlink(missing_ok=True)
        self.cur_task, self.cur_attempt = "", 0
        self.phase("Force-stopped", None, "kill switch used; unfinished work is discarded on the next start")
        self.log(f"FORCE STOP: ended {len(killed)} process(es).")
        return f"Force-stopped {self.ws.name}: {len(killed)} process(es) ended" if killed else "No running agent found (STOP flag set)"

    def detect_run_plan(self) -> dict:
        """Build steps (shell commands) plus start command, URL and artifact for the user, from the stack definition."""
        st = PRESETS.get(self.cfg["preset"])
        if not st:
            return {"kind": "unknown", "build": [], "run": "see README.md in repo/", "binary": ""}
        c = st["commands"]
        steps = [x for x in (c.get("setup"), c.get("build")) if x] + [shlex.join(r) for r in st.get("release", [])]
        return {"kind": st["label"], "build": steps, "run": st["run"], "binary": st.get("artifact", ""), "url": st.get("url", "")}

    def try_version(self, wait_minutes: int = 60) -> str:
        """Pauses the agent after the current task, builds the current main state and writes TESTING.md."""
        if self.is_running():
            write_text(self.dir / "STOP", "try\n")
            print("Agent is still running, finishing the current task first ...", flush=True)
            deadline = time.time() + wait_minutes * 60
            while self.is_running() and time.time() < deadline:
                time.sleep(3)
            if self.is_running():
                return "Agent did not stop in time. Try again later."
        self.git.run("checkout", "-q", "main")
        self.git.hard_reset()
        plan = self.detect_run_plan()
        log = []
        for cmd in plan["build"]:
            rc, out = sh(cmd, self.repo, timeout=1200, shell=True)
            log.append(f"$ {cmd} -> {rc}")
            if rc != 0:
                log.append(clip(out, 1500))
                break
        failed = any(l.endswith("-> 127") or ("-> " in l and not l.endswith("-> 0")) for l in log if l.startswith("$"))
        sha_ = self.git.out("rev-parse", "--short", "main")
        lines = [f"# Test version of {self.ws.name}", "", f"State: commit {sha_}, type: {plan['kind']}",
                 f"Folder: {self.repo}", ""]
        if failed:
            lines += ["## Build failed", "```", *log, "```", "Install missing tools or report it to the agent via `make feedback`."]
        else:
            lines += ["## How to start it", "```", f"cd {self.repo}", plan["run"], "```"]
            if plan.get("url"):
                lines.append(f"Then open in your browser: {plan['url']}")
            if plan["binary"]:
                lines.append(f"Built binary: {plan['binary']}")
        lines += ["", "## Afterwards", "- Send feedback to the agent: `make feedback P=<name> MSG=\"The button does not work\"`",
                  "- Let it continue working: `make start-tmux P=<name>`", ""]
        report = "\n".join(lines)
        write_text(self.ws / "TESTING.md", report)
        return report

    def add_feedback(self, text: str) -> None:
        """Appends user feedback to IDEAS.md; the agent re-plans on the next loop pass."""
        sec = "## Feedback from testing"
        cur = self.ideas_path.read_text("utf-8") if self.ideas_path.exists() else ""
        if sec not in cur:
            cur = cur.rstrip() + f"\n\n{sec}\n"
        write_text(self.ideas_path, cur.rstrip() + f"\n- {now()[:16]}: {text}\n")

    def status(self) -> str:
        c: dict[str, int] = {}
        for i in self.backlog.items:
            c[i["status"]] = c.get(i["status"], 0) + 1
        return (f"Iteration {self.state['iteration']}, merged {self.state['merged']}, backlog {c}, "
                f"features {len(self.mem.features)}, files {len(self.mem.codemap)}, "
                f"consecutive failures {self.state['consecutive_failures']}, runtime {self.state['elapsed'] / 3600:.1f} h")
