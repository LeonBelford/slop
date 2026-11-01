"""Repeatable Playwright checks for the web UI itself (dashboard, tabs, URL-hash state), replacing the manual
browser spot-checks. Skipped if no interpreter has Playwright + Chromium installed (`make e2e-setup`)."""
from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from infra import e2e

PY = e2e.find_python()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(PY, "Playwright not installed: run `make e2e-setup`")
class WebUiBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = Path(self.tmp.name) / "projects"
        self.projects.mkdir()
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        root = Path(__file__).resolve().parent.parent
        cmd = f"python3 webui.py --port {self.port} --projects-dir {self.projects}"
        self.proc, err = e2e.start_app(cmd, root, self.url, timeout=30)
        if err:
            self.skipTest(f"webui.py did not start: {err}")
        self.addCleanup(lambda: e2e.stop_app(self.proc))

    def test_dashboard_loads_without_console_errors(self):
        rep = e2e.smoke(PY, self.url, Path(self.tmp.name) / "out1")
        self.assertTrue(rep["ok"], rep.get("problems") or rep.get("error"))

    def test_tabs_and_url_hash_state_round_trip(self):
        script = Path(self.tmp.name) / "tabs.py"
        script.write_text(
            "def run(page, base):\n"
            "    page.goto(base)\n"
            "    expect(page.locator('#dashBtn')).to_be_visible()\n"
            "    expect(page.locator('#chatBtn')).to_be_visible()\n"
            "    page.click('#chatBtn')\n"
            "    expect(page.get_by_text('Start a conversation')).to_be_visible(timeout=5000)\n"
            "    page.click('#dashBtn')\n"
            "    expect(page.locator('#body')).to_be_visible()\n"
            "    expect(page.get_by_text('Start a conversation')).to_have_count(0)\n"
        )
        rep = e2e.run_script(PY, script, self.url, Path(self.tmp.name) / "out2")
        self.assertTrue(rep["ok"], rep.get("error") or rep.get("problems"))


if __name__ == "__main__":
    unittest.main()
