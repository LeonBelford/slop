"""Agent methods: security gate, probe, audit and end-to-end check."""
from __future__ import annotations

import e2e
import security
from cfutil import PRESETS, now, read_json, write_json, write_text


class SecurityMixin:
    def security_on(self) -> bool:
        return self.cfg["features"].get("security", "on") not in (False, "off")

    def save_security(self, kind: str, findings: list[dict], tools: list[str] | None = None) -> None:
        data = read_json(self.dir / "security.json", {})
        data[kind] = {"time": now(), "summary": security.summary(findings), "tools": tools or [], "findings": findings[:100]}
        write_json(self.dir / "security.json", data)

    def security_review(self, item: dict) -> list[dict]:
        """LLM threat model of the diff: only concrete, exploitable problems introduced by this change."""
        return security.llm_review(self.llm, item["title"], self.git.diff(), self.log)

    def security_gate(self, item: dict, plan: dict) -> tuple[bool, str]:
        """Blocks a change with critical/high findings: static rules on the changed files plus an attacker-minded review."""
        if not self.security_on() or item["type"] == "docs":
            return True, ""
        found: list[dict] = []
        for rnd in range(1, 4):
            self.phase(f"Security check, round {rnd}", "security", "static rules + threat review")
            changed = [f for f in self.git.changed_files() if (self.repo / f).exists()]
            found = security.dedupe(security.scan_repo(self.repo, changed) + (self.security_review(item) if rnd == 1 else []))
            bad = security.blocking(found, "high")
            self.save_security("last_change", found)
            if not bad:
                if found:
                    self.log(f"Security: {security.summary(found)} (non-blocking, tracked)")
                return True, ""
            self.log(f"Security: {len(bad)} blocking finding(s), round {rnd}")
            if rnd == 3:
                break
            self.code("security_fix", sorted(set(changed))[:self.lim["max_files_per_task"] + 4], [str(self.run_dir / "context.md")],
                      f"Fix these security problems without changing behavior otherwise. Never hide them with comments:\n"
                      f"{security.render(bad)}\n\n{self.RULES}")
            ok, report = self.verify()
            if not ok:
                return False, "Build red after security fix:\n" + report
        self.mem.add_lesson("Security gate: " + "; ".join(sorted({f["rule"] for f in found})[:4]) + " must not appear in new code",
                            sorted({f["file"] for f in found})[:4], item["id"])
        return False, security.render(security.blocking(found, "high"))

    def security_probe(self, url: str) -> tuple[bool, str]:
        """Black-box probe of the running app (localhost only). Exposed secrets/files block, hardening gaps are tracked."""
        if not self.security_on():
            return True, ""
        self.phase("Security probe of the running app", "security", url)
        found = security.probe(url)
        self.save_security("runtime", found)
        bad = security.blocking(found, "high")
        if bad:
            return False, "[security probe]\n" + security.render(bad)
        if found:
            self.log(f"Security probe: {security.summary(found)} (tracked)")
        return True, ""

    def security_audit(self) -> int:
        """Periodic full audit of the whole repository (static + installed scanners) that turns findings into backlog tasks."""
        if not self.security_on():
            return 0
        self.phase("Security audit of the whole project", "security")
        found = security.scan_repo(self.repo)
        ext, tools = security.external_scans(self.repo)
        found = security.dedupe(found + ext)
        self.save_security("audit", found, tools)
        self.metric(kind="security_audit", summary=security.summary(found), tools=tools)
        by_file: dict[str, list[dict]] = {}
        for f in found:
            if f["severity"] != "low":
                by_file.setdefault(f["file"], []).append(f)
        added = 0
        for file, fs in sorted(by_file.items(), key=lambda kv: security.SEVERITIES.index(kv[1][0]["severity"]))[:4]:
            top = fs[0]["severity"]
            if self.backlog.add(f"Security: fix {len(fs)} finding(s) in {file}", security.render(fs, 12), "bug",
                                9.5 if top in ("critical", "high") else 6.0,
                                ["No finding of these rules remains in the file", "Build and tests green"], "Security audit", "security"):
                added += 1
        self.log(f"Security audit: {security.summary(found)}, {added} new tasks, tools: {', '.join(tools) or 'built-in rules'}")
        return added

    def e2e_check(self, item: dict) -> tuple[bool, str]:
        """Real-browser check of web products: smoke test plus a generated Playwright script. Skipped when unavailable."""
        feat = self.cfg["features"].get("e2e", "auto")
        url = e2e.extract_url(self.detect_run_plan().get("url", ""))
        if feat in (False, "off") or not url or item["type"] == "docs":
            return True, ""
        py = e2e.find_python()
        if not py:
            if not getattr(self, "e2e_hinted", False):
                self.e2e_hinted = True
                self.log("Browser tests skipped. " + e2e.INSTALL_HELP)
            return True, ""
        if e2e.port_busy(url):
            self.log(f"Browser tests skipped: {url} is already in use.")
            return True, ""
        self.phase("Starting the app for the browser test", "e2e", url)
        proc, err = e2e.start_app(PRESETS[self.cfg["preset"]]["run"], self.repo, url)
        if not proc:
            return False, "[e2e] " + err
        out = self.run_dir / "e2e"
        script = self.repo / "e2e" / f"e2e_{item['id'].lower()}.py"
        try:
            for rnd in range(1, 3):
                self.phase(f"Browser test, round {rnd}", "e2e", "smoke + user flow")
                rep = e2e.smoke(py, url, out)
                if rep.get("error") or not rep.get("ok"):
                    problems = rep.get("error") or "\n".join(rep.get("problems", []))
                    fail = f"[e2e smoke] {problems}"
                else:
                    if not script.exists():
                        code = e2e.extract_script(self.llm.chat(
                            "e2e_test", "planner",
                            "You write one Playwright (sync, Python) UI test. Define `def run(page, base):` that opens base, "
                            "performs the user flow with real clicks/typing and asserts the visible result with expect(). "
                            "Use only role/text/label locators. Output only a python code block.",
                            f"Task: {item['title']}\n{item['description']}\nAcceptance: {item.get('acceptance')}\n\n"
                            f"Page (aria snapshot):\n{rep.get('snapshot', '')}"))
                        if not code:
                            return True, ""
                        script.parent.mkdir(exist_ok=True)
                        write_text(script, code)
                    res = e2e.run_script(py, script, url, out)
                    if res.get("ok"):
                        self.log(f"Browser test passed ({script.name}).")
                        return self.security_probe(url)
                    fail = f"[e2e script] {res.get('error') or ''}\n" + "\n".join(res.get("problems", [])) \
                        + f"\nPage now:\n{res.get('snapshot', '')[:2000]}"
                self.log("Browser test failed: " + fail.splitlines()[0][:150])
                if rnd == 2:
                    return False, fail
                files = sorted(set(self.git.changed_files()) | ({f"e2e/{script.name}"} if script.exists() else set()))
                e2e.stop_app(proc)
                self.code("e2e_fix", files[:self.lim["max_files_per_task"] + 4], [str(self.run_dir / "context.md")],
                          f"A real-browser test failed. The product code or the test script e2e/{script.name} is wrong; "
                          f"fix whichever is at fault (never just weaken the assertion).\nTask: {item['title']}\n\n{fail}\n\n{self.RULES}")
                vok, vrep = self.verify()
                if not vok:
                    return False, "Build red after e2e fix:\n" + vrep
                proc, err = e2e.start_app(PRESETS[self.cfg["preset"]]["run"], self.repo, url)
                if not proc:
                    return False, "[e2e] " + err
            return True, ""
        finally:
            e2e.stop_app(proc)
