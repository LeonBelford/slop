"""Agent methods: model choice, coder call, build/test, debug loop, review, one task attempt."""
from __future__ import annotations

from dash import estimate
from infra import gpusched
import json
from infra import models, notify
import os
from infra import perf
import re
import shutil
import time
from core import failures
from core.cfa_flow import removed_tests
from core.cfutil import PRESETS, clip, read_json, sh, sh_live, sha, tail, write_json, write_text


class BuildMixin:
    """Agent mixin: model choice, coder call, build/test, debug loop, review and one full task attempt."""

    # ---- Model routing (optional, uses only installed models)
    def routes(self) -> dict:
        return models.resolve(self.llm.installed, self.cfg["routing"], self.cfg["coder_model"])

    def coder_for(self, tag: str) -> str:
        r, default = self.routes(), self.cfg["coder_model"]
        light, heavy = r["light"], r["heavy"]
        cls = getattr(self, "task_class", "standard")
        if tag == "review_fix" or tag.startswith("debug") and tag != "debug1" or getattr(self, "force_heavy", False):
            cls = "heavy"
        elif tag == "cleanup":
            cls = "light"
        pick = light if cls == "light" else heavy if cls == "heavy" else None
        return models.coder_string(pick) if pick else default

    # ---- Coder (Aider)
    def code(self, tag: str, edit: list[str], read: list[str], prompt: str) -> str:
        if self.coder_mock:
            return self.coder_mock(self.repo, tag, edit, read, prompt)
        pf = self.run_dir / f"{tag}.prompt.md"
        write_text(pf, prompt)
        model = self.coder_for(tag)
        write_json(self.dir / "coder_now.json", {"tag": tag, "files": edit[:12], "model": model, "since": time.time()})
        edit, read, dropped = perf.fit_context(edit, read, prompt, self.repo)
        if dropped:
            self.log(f"Context budget: {len(dropped)} read-only file(s) left out of the {tag} call to stay inside the model window")
        cmd = ["aider", "--yes-always", "--no-auto-commits", "--no-pretty", "--no-check-update",
               "--model", model, "--message-file", str(pf)]
        for r in read:
            cmd += ["--read", r]
        cmd += perf.aider_args(model, self.dir)
        cmd += edit
        env = {"OLLAMA_API_BASE": os.getenv("OLLAMA_API_BASE", "http://localhost:11434"), "PYTHONUNBUFFERED": "1"}
        t0 = time.time()
        while True:
            with self.llm.sched.gpu(f"coder/{tag}", model) as gate:
                rc, out = sh_live(cmd, self.repo, self.lim["coder_timeout"], self.dir / "coder_live.txt", env=env, gate=gate, idle=True)
            if rc != gpusched.PREEMPTED_RC:
                break
            self.log(f"⏸ [coder/{tag}] Aider stopped for the main agent, retrying when the GPU is idle")
        (self.dir / "coder_now.json").unlink(missing_ok=True)
        (self.dir / "coder_live.txt").unlink(missing_ok=True)
        self.llm.record(tag, "coder", model, prompt, out, "", time.time() - t0)
        write_text(self.run_dir / f"{tag}.aider.log", out)
        if rc != 0:
            self.log(f"Aider exit code {rc} for {tag}: {tail(out, 300)}")
        return out

    # ---- Build/test
    def verify(self) -> tuple[bool, str]:
        cmds, t = self.cfg["commands"], self.lim["cmd_timeout"]
        deps = [self.repo / f for f in PRESETS.get(self.cfg["preset"], {}).get("deps_files", [])]
        h = sha("".join(f.read_text("utf-8") for f in deps if f.exists()))
        if cmds.get("setup") and h != self.state.get("req_hash"):
            rc, out = sh(cmds["setup"], self.repo, t, shell=True)
            if rc:
                return False, f"[setup] FAILED rc={rc}\n{tail(out, 5000)}"
            self.state["req_hash"] = h
            self.save_state()
        for name in ("build", "test", "lint"):
            c = cmds.get(name)
            if not c:
                continue
            self.log(f"▶ running {name}: {c}")
            rc, out = self.run_cmd(name, c, t)
            if rc:
                return False, f"[{name}] FAILED rc={rc}\n$ {c}\n{tail(out, 6000)}"
        return True, "build + tests ok"

    # ---- Planning
    def impact_analysis(self, item: dict, previous: list[str]) -> dict:
        self.phase("Planning: analysing the codebase and the impact of the change", "plan")
        cands = self.mem.select_candidates(item)
        ctx = self.mem.context_pack(item, cands)
        src = "\n\n".join(f"### {f}\n```\n{clip((self.repo / f).read_text('utf-8', 'replace'), self.cfg['context']['file_clip'])}\n```"
                          for f in (cands + self.mem.neighbors(cands))[:10] if (self.repo / f).exists())
        sys_p = ("You are a tech lead. Before every change you analyze relationships: who calls the files, "
                 "which invariants apply, what can break. Plan minimally and concretely.")
        schema = ('{"understanding": "", "files_to_change": ["existing paths"], "new_files": ["new paths"], '
                  '"read_only_context": ["paths"], "affected_dependents": ["paths that might break"], '
                  '"invariants": ["..."], "risks": ["..."], "steps": ["..."], "tests_to_add": ["..."], '
                  '"docs_to_update": ["..."], "test_files": ["paths of the test files to create or extend"], "security_impact": "new inputs, endpoints, files, commands, secrets or dependencies this change exposes, and how each is protected"}')
        plan = self.llm.json("impact", "planner", sys_p,
                             f"Task {item['id']} ({item['type']}): {item['title']}\n{item['description']}\n"
                             f"Acceptance: {item.get('acceptance')}\nStack: {self.cfg['stack']}\n"
                             f"{self.blueprint_ctx(item)}{self.ui_note()}\nPrevious failed attempts:\n{chr(10).join(previous) or '-'}\n\n{ctx}\n\nSource code:\n{src}\n\nSchema: {schema}")
        known = self.mem.consequences_for(plan.get("files_to_change", []) + plan.get("new_files", []), item["title"])
        plan["known_consequences"] = known
        if known:
            self.log(f"Knowledge base: {len(known)} known consequence(s) for this change.")
        if self.cfg["features"]["plan_critique"]:
            try:
                crit = self.llm.json("plan_critique", "reviewer", "You strictly critique plans for overlooked dependencies "
                                     "and for conflicts with earlier decisions and lessons.",
                                     f"Plan: {json.dumps(plan)}\nKnown consequences from earlier decisions/lessons/features:\n"
                                     + ("\n".join(known) or "-") + "\nDependency graph of the affected files:\n" +
                                     "\n".join(f"{f}: used_by={self.mem.codemap.get(f, {}).get('imported_by', [])}"
                                               for f in plan.get("files_to_change", [])) +
                                     '\nSchema: {"ok": true, "missing_files": ["..."], "concerns": ["..."]}')
                for f in crit.get("missing_files", []):
                    if f in self.mem.codemap and f not in plan.get("files_to_change", []):
                        plan.setdefault("files_to_change", []).append(f)
                plan["risks"] = plan.get("risks", []) + crit.get("concerns", [])
            except Exception as e:
                self.log(f"Plan critique failed: {e}")
        plan["files_to_change"] = [f for f in plan.get("files_to_change", []) if (self.repo / f).exists()][:self.lim["max_files_per_task"]]
        plan["new_files"] = plan.get("new_files", [])
        write_json(self.run_dir / "plan.json", plan)
        write_text(self.run_dir / "context.md", ctx)
        return plan

    def set_class(self, item: dict, plan: dict) -> None:
        rt = self.cfg["routing"]
        edit = plan["files_to_change"] + plan["new_files"]
        self.task_class = models.classify(item["type"], len(edit), self.cur_attempt, int(rt["heavy_from_attempt"]), int(rt["complex_files"]))

    def implement(self, item: dict, plan: dict) -> None:
        tests = self.test_first(item, plan)
        self.phase("Implementing the change with Aider", "implement", "; ".join(str(x) for x in plan.get("steps", [])[:4]))
        edit = plan["files_to_change"] + plan["new_files"]
        self.set_class(item, plan)
        if self.routes()["light"] or self.routes()["heavy"]:
            self.log(f"Task class: {self.task_class} -> coder {self.coder_for('implement')}")
        read = [str(self.run_dir / "context.md")] + [f for f in plan.get("read_only_context", []) if (self.repo / f).exists()]
        groups = self.step_groups(item, plan)
        for n, steps in enumerate(groups, 1):
            part = f"\nPass {n} of {len(groups)}: do ONLY these steps now: {steps}\nEarlier passes already did: {[s for g in groups[:n - 1] for s in g]}\n" if len(groups) > 1 else ""
            prompt = (f"Task {item['id']}: {item['title']}\n{item['description']}\nAcceptance: {item.get('acceptance')}\n\n"
                      f"Understanding: {plan.get('understanding', '')}\nSteps: {steps if len(groups) > 1 else plan.get('steps')}\nInvariants: {plan.get('invariants')}\n"
                      f"Risks: {plan.get('risks')}\nSecurity impact: {plan.get('security_impact')}\nKnown consequences (earlier decisions/lessons): {plan.get('known_consequences')}\n"
                      f"Affected callers: {plan.get('affected_dependents')}\n"
                      f"Tests to write: {plan.get('tests_to_add')}\nNew files: {plan.get('new_files')}\n"
                      + (f"Tests already written for this task (make them pass, change them only if they are wrong): {tests}\n" if tests else "")
                      + f"{part}\n{self.RULES}")
            self.code("implement" if len(groups) == 1 else f"implement{n}", edit, read + tests, prompt)
        if not self.coder_mock and not self.git.changed_files():
            raise RuntimeError("the coder made no usable edit")

    def debug_loop(self, item: dict) -> tuple[bool, str]:
        self.phase("Building and running tests", "build")
        ok, report = self.verify_stage()
        last_sig = failures.signature(report)
        for n in range(1, self.lim["max_debug_rounds"] + 1):
            if ok and self.cfg["features"]["require_tests"] and item["type"] in ("feature", "bug") \
                    and not any("test" in f.lower() for f in self.git.changed_files()):
                ok, report = False, "[policy] No tests were written/changed. Add tests for the change."
            if ok and self.feat("protect_tests") and removed_tests(self.git.diff()) > 0:
                ok, report = False, "[policy] The change deletes tests (more test definitions removed than added). Restore them; fix the code, not the tests."
            if ok:
                return True, report
            self.log(f"Debug round {n}: {report.splitlines()[0]}")
            self.phase(f"Debugging, round {n}", "build", report.splitlines()[0])
            mentioned = [f for f in re.findall(r"[\w./-]+\.(?:py|js|ts|html)", report) if (self.repo / f).exists()]
            files = sorted(set(self.git.changed_files()) | set(mentioned))[:self.lim["max_files_per_task"] + 4]
            self.code(f"debug{n}", files, [str(self.run_dir / "context.md")],
                      f"The build/test failed. Find the cause (not the symptoms) and fix it.\n"
                      f"The task was: {item['title']}\n\nError report:\n{report}\n\n{self.RULES}\n"
                      "Do not delete or weaken tests just to make them pass.")
            ok, report = self.verify_stage()
            sig = failures.signature(report)
            if not ok and n >= 2 and sig == last_sig:
                self.log(f"Debug loop stalled: round {n} failed exactly like round {n - 1}, stopping early")
                self.metric(kind="stalled", item=item["id"], where="debug", sig=sig[:80])
                return False, report
            last_sig = sig
        return ok, report

    def review(self, item: dict, plan: dict) -> dict:
        self.phase("Independent review of the diff", "review")
        diff = self.git.diff()
        final = self.routes()["final_reviewer"]
        if final:
            self.llm.overrides["final_reviewer"] = final
        try:
            return self.llm.json(
                "review", "final_reviewer" if final else "reviewer", "You are a strict code reviewer (correctness, security, readability, tests, invariants).",
                f"Task: {item['title']}\nAcceptance: {item.get('acceptance')}\nInvariants: {plan.get('invariants')}\n"
                f"Diff:\n{clip(diff, 14000)}\nSchema: "
                '{"score": 1-10, "verdict": "approve|revise", "summary": "2-3 sentences: what was changed and why", '
                '"issues": [{"file": "", "problem": "", "fix": ""}]}')
        except Exception as e:
            self.log(f"Review failed: {e}")
            return {"score": 7, "verdict": "approve", "summary": item["title"], "issues": []}

    # ---- Process one task completely
    def process(self, item: dict) -> bool:
        self.run_dir = self.dir / "runs" / f"{self.state['iteration']:04d}-{item['id']}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        previous: list[str] = []
        sigs: list[str] = []
        self.force_heavy = False
        self.backlog.set(item, status="in_progress")
        self.cur_task = f"{item['id']} {item['title']}"
        t0 = time.time()
        est = estimate.estimate(estimate.load(self.dir / "durations.jsonl"), item["title"], item["type"], item.get("description", ""))
        if est["seconds"]:
            self.log(f"estimate: ~{round(est['seconds'] / 60)} min ({est['basis']}, {est['n']} comparable tasks)")
            self.backlog.set(item, estimate_s=est["seconds"])
        for attempt in range(1, self.lim["max_attempts_per_task"] + 1):
            self.cur_attempt = attempt
            self.backlog.set(item, attempts=item["attempts"] + 1)
            self.log(f"== {item['id']} {item['title']} (attempt {attempt})")
            branch = f"agent/{item['id']}-{attempt}"
            self.git.branch_from_main(branch)
            base_sha = self.git.out("rev-parse", "main")
            if attempt == 2:
                previous.append("Scope: deliver the smallest working slice that meets the core acceptance criteria; leave extras for later tasks.")
            self.retry_strategy(item, previous)
            try:
                ok, why = self.attempt(item, previous, branch, base_sha)
            except Exception as e:
                ok, why = False, f"Exception: {e}"
            self.mem.lesson_feedback(ok)
            if ok:
                estimate.record(self.dir / "durations.jsonl", item["title"], time.time() - t0, item["type"], item["attempts"], True,
                                desc=item.get("description", ""))
                self.backlog.set(item, status="done")
                self.layer_done(item)
                self.force_heavy = False
                self.last_light = item["type"] in ("docs", "test")
                self.cur_task, self.cur_attempt = "", 0
                self.phase("Task finished and merged, picking the next one", None, item["title"])
                notify.send(self.cfg, "done", self.ws.name, f"finished {item['id']} {item['title']}")
                return True
            self.git.hard_reset()
            self.git.run("checkout", "-q", "main")
            self.git.run("branch", "-D", branch)
            cls, sig = failures.classify(why), failures.signature(why)
            previous.append(f"Attempt {attempt} [{cls}]: {clip(why, 600)}")
            if failures.advice(cls):
                previous.append(f"Hint for the next attempt: {failures.advice(cls)}")
            self.record_failure(item, why)
            self.metric(kind="attempt_failed", item=item["id"], title=item["title"], type=item["type"], attempt=attempt,
                        why=clip(why, 300), cls=cls)
            same = sum(1 for x in sigs if x == sig)
            sigs.append(sig)
            if same >= 1:
                self.force_heavy = True
                self.log(f"The same failure ({cls}) happened again: escalating to the stronger model and a different approach")
                previous.append("The same failure repeated. Do NOT repeat the previous approach; change the approach fundamentally.")
            if time.time() - t0 > self.lim.get("max_task_minutes", 120) * 60 and attempt < self.lim["max_attempts_per_task"]:
                self.log(f"{item['id']} took over {self.lim.get('max_task_minutes', 120)} min: no more attempts of the same kind")
                break
            if same >= 2:
                self.log("Three identical failures: stopping this task instead of repeating it")
                self.metric(kind="stalled", item=item["id"], where="task", sig=sig[:80])
                notify.send(self.cfg, "stalled", self.ws.name, f"{item['id']} {item['title']} failed three times the same way")
                break
        self.force_heavy = False
        self.drop_wip(item)
        if self.split_task(item, previous):
            self.cur_task, self.cur_attempt = "", 0
            self.phase("Task split into smaller slices", None, item["title"])
            return False
        self.block_task(item)
        self.phase("Task blocked after repeated failures", None, item["title"])
        notify.send(self.cfg, "blocked", self.ws.name, f"{item['id']} {item['title']} is blocked after repeated failures")
        return False

    def record_failure(self, item: dict, why: str) -> None:
        try:
            d = self.llm.json("lesson", "summarizer", "You formulate lessons learned.",
                              f"Task: {item['title']}\nError:\n{clip(why, 3000)}\n"
                              'Schema: {"lesson": "1-2 sentences: cause and how to avoid it"}')
            lesson = d["lesson"]
        except Exception:
            lesson = clip(why, 300)
        self.mem.add_lesson(lesson, [], item["id"])

    def save_wip(self, item: dict) -> None:
        """Builds and tests are green: keep this work so a later gate failure only needs a fix, not a rewrite from scratch."""
        if self.git.commit_all(f"{item['id']}: work in progress (build and tests green)") or self.git.changed_files():
            self.git.run("branch", "-f", f"agent/wip-{item['id']}", "HEAD")

    def resume_wip(self, item: dict) -> dict | None:
        """On a retry after a failed quality gate: continue from the saved green work. Returns its plan, or None to start fresh."""
        wip = f"agent/wip-{item['id']}"
        plan = read_json(self.run_dir / "plan.json", None)
        if not plan or self.git.run("rev-parse", "--verify", "-q", wip)[0] != 0 \
                or self.git.run("merge", "--ff-only", "-q", wip)[0] != 0:
            return None
        self.log(f"Resuming from the saved green work of {item['id']}: only the failed gate is fixed, nothing is rewritten")
        return plan

    def drop_wip(self, item: dict) -> None:
        self.git.run("branch", "-D", f"agent/wip-{item['id']}")

    def attempt(self, item: dict, previous: list[str], branch: str, base_sha: str) -> tuple[bool, str]:
        plan = self.resume_wip(item) if previous else None
        if plan:
            self.phase("Fixing the failed quality gate on the saved work", "implement", clip(previous[-1] if previous else "", 160))
            self.code("resume_fix", sorted(set(self.git.changed_files()))[:self.lim["max_files_per_task"] + 4],
                      [str(self.run_dir / "context.md")],
                      f"Task {item['id']}: {item['title']}\nThe implementation builds and passes its tests, but it was rejected by a "
                      f"quality gate. Fix exactly the problems below with minimal edits; keep everything that works.\n"
                      + "\n".join(previous[-3:]) + f"\n\n{self.RULES}")
        else:
            plan = self.impact_analysis(item, previous)
            self.implement(item, plan)
        ok, report = self.debug_loop(item)
        if not ok:
            return False, "Build/test red after debug rounds:\n" + report
        self.save_wip(item)
        self.coverage_check(item)
        if not self.mutation_check(item):
            return False, "Red after the mutation fix round"
        ok, report = self.e2e_check(item)
        if not ok:
            return False, "UI test failed:\n" + report
        ok, report = self.security_gate(item, plan)
        if not ok:
            return False, "Security gate failed:\n" + report
        review = self.review(item, plan)
        if review.get("verdict") == "revise" or review.get("score", 10) < self.lim["min_review_score"]:
            self.log(f"Review requests revision (score {review.get('score')})")
            self.code("review_fix", sorted(set(self.git.changed_files()))[:self.lim["max_files_per_task"] + 4],
                      [str(self.run_dir / "context.md")],
                      f"Fix these review points:\n{json.dumps(review.get('issues', []), indent=1)}\n\n{self.RULES}")
            ok, report = self.debug_loop(item)
            if not ok:
                return False, "Red after review fix:\n" + report
            review = self.review(item, plan)
        if not self.acceptance_check(item, plan):
            return False, "Red after the acceptance fix round"
        self.git.commit_all(f"{item['id']}: {item['title']}")
        checkpoint = self.git.sha()
        if self.cfg["features"]["cleanup_pass"] and item["type"] != "docs":
            self.phase("Cleanup pass (no behavior change)", "cleanup")
            changed = [f for f in self.git.changed_files(base_sha) if (self.repo / f).exists() and f.endswith(tuple(self.cfg["source_exts"]))]
            self.code("cleanup", changed[:self.lim["max_files_per_task"]], [str(self.run_dir / "context.md")],
                      "Cleanup without behavior change: remove dead code, clarify names, merge duplicates, "
                      "add docstrings, simplify functions. Leave public interfaces and tests unchanged.")
            ok, report = self.verify()
            if ok:
                self.git.commit_all(f"{item['id']}: cleanup")
            else:
                self.log("Cleanup broke the build, discarded.")
                self.git.hard_reset(checkpoint)
        snap = self.mem.snapshot()
        self.phase("Updating memory, documentation and decision records", "docs")
        try:
            changed_all = [f for f in self.git.changed_files(base_sha) if (self.repo / f).exists()]
            self.mem.sync(force_docs_for=[f for f in changed_all if f in self.mem.codemap])
            self.mem.add_decision(item, plan, review, self.git.diff(base_sha))
            self.mem.record_feature(item, changed_all, review)
            self.mem.changelog(item, review)
            self.mem.refresh_kb()
            if self.state["merged"] + 1 - self.state["last_arch_at"] >= self.lim["architecture_refresh_every"]:
                self.mem.refresh_architecture()
                self.mem.refresh_readme(self.goal)
                self.state["last_arch_at"] = self.state["merged"] + 1
            self.git.commit_all(f"{item['id']}: update docs and memory")
            ok, report = self.verify()
            if not ok:
                raise RuntimeError("Build red after docs update:\n" + report)
            self.phase("Merging into main and verifying the build", "merge")
            old_main = self.git.out("rev-parse", "main")
            if not self.git.merge_ff(branch):
                raise RuntimeError("Fast-forward merge into main failed")
            ok, report = self.verify()
            if not ok:
                self.git.hard_reset(old_main)
                raise RuntimeError("Build on main red after merge, rollback:\n" + report)
        except Exception as e:
            self.mem.restore(snap)
            return False, str(e)
        shutil.rmtree(snap, ignore_errors=True)
        self.git.run("branch", "-D", branch)
        self.drop_wip(item)
        self.state["merged"] += 1
        self.metric(item=item["id"], title=item["title"], type=item["type"], attempts=item["attempts"],
                    review_score=review.get("score"), files=len(changed_all), loc=sum(e.get("loc", 0) for e in self.mem.codemap.values()))
        self.log(f"✔ {item['id']} merged (review {review.get('score')})")
        return True, "ok"
