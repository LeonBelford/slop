"""Browser runner for CodeFactory's real-UI tests. Needs Playwright (`make e2e-setup`); started by e2e.py as a subprocess.

  python e2e_runner.py smoke  <url> <outdir>            open the page, collect errors, check links, dump an accessibility snapshot
  python e2e_runner.py script <file> <url> <outdir>     run `def run(page, base)` from a generated script (clicks, typing, asserts)

The browser may only reach localhost: every other request is aborted. The last stdout line is a JSON report.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright  # noqa: F401  (expect is available to generated scripts)

LOCAL = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_LINKS = 15


def local_only(route) -> None:
    u = urllib.parse.urlsplit(route.request.url)
    if u.scheme in ("data", "blob", "about") or (u.hostname or "") in LOCAL:
        route.continue_()
    else:
        route.abort()


class Watch:
    """Collects what a user would never see but a tester must: console errors, JS exceptions, failed requests."""

    def __init__(self, page, main_url: str):
        self.console, self.exceptions, self.failed = [], [], []
        self.main = main_url
        page.on("console", lambda m: self.console.append(m.text[:300]) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.exceptions.append(str(e)[:300]))
        page.on("requestfailed", lambda r: self.failed.append(f"{r.method} {r.url} ({r.failure})")
                if urllib.parse.urlsplit(r.url).hostname in LOCAL else None)
        page.on("response", self.on_response)

    def on_response(self, r) -> None:
        main_doc = r.request.resource_type == "document" and r.url.rstrip("/") == self.main.rstrip("/")
        if r.status >= 500 or (r.status >= 400 and not main_doc and r.request.resource_type != "fetch"):
            self.failed.append(f"{r.status} {r.request.method} {r.url}")

    def problems(self) -> list[str]:
        out = [f"JS exception: {x}" for x in self.exceptions] + [f"Failed request: {x}" for x in self.failed]
        out += [f"Console error: {x}" for x in self.console]
        return out[:25]


def snapshot(page) -> str:
    try:
        return page.locator("body").aria_snapshot()[:6000]
    except Exception as e:  # noqa: BLE001
        return f"(no snapshot: {e})"


def same_origin_links(page, base: str) -> list[str]:
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    origin = urllib.parse.urlsplit(base)
    seen, out = set(), []
    for h in hrefs:
        u = urllib.parse.urlsplit(h)
        key = (u.path, u.query)
        if u.netloc == origin.netloc and u.scheme in ("http", "https") and key not in seen and not h.endswith(("#",)):
            seen.add(key)
            out.append(h.split("#")[0])
    return out[:MAX_LINKS]


def smoke(p, url: str, out: Path) -> dict:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.route("**/*", local_only)
    page.set_default_timeout(10000)
    w = Watch(page, url)
    resp = page.goto(url, wait_until="load")
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    broken = []
    for link in same_origin_links(page, url):
        try:
            r = ctx.request.get(link, timeout=8000)
            if r.status >= 400:
                broken.append(f"{r.status} {link}")
        except Exception as e:  # noqa: BLE001
            broken.append(f"unreachable {link}: {str(e)[:80]}")
    out.mkdir(parents=True, exist_ok=True)
    shot = out / "smoke.png"
    page.screenshot(path=str(shot))
    probs = w.problems() + [f"Broken link: {b}" for b in broken]
    if resp is not None and resp.status >= 500:
        probs.insert(0, f"Start page answered {resp.status}")
    rep = {"ok": not probs, "status": resp.status if resp else None, "title": page.title(), "problems": probs,
           "snapshot": snapshot(page), "screenshot": str(shot)}
    browser.close()
    return rep


def run_script(p, file: str, url: str, out: Path) -> dict:
    src = Path(file).read_text("utf-8")
    ns: dict = {"expect": expect}
    exec(compile(src, file, "exec"), ns)  # noqa: S102  (generated test, runs locally against localhost only)
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.route("**/*", local_only)
    page.set_default_timeout(8000)
    w = Watch(page, url)
    err = ""
    try:
        ns["run"](page, url.rstrip("/"))
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:800]}"
    out.mkdir(parents=True, exist_ok=True)
    shot = out / (Path(file).stem + ".png")
    try:
        page.screenshot(path=str(shot))
    except Exception:  # noqa: BLE001
        pass
    probs = [x for x in w.problems() if not x.startswith("Console error")]
    rep = {"ok": not err and not probs, "error": err, "problems": probs, "snapshot": snapshot(page), "screenshot": str(shot)}
    browser.close()
    return rep


def main(argv: list[str]) -> int:
    try:
        with sync_playwright() as p:
            if argv[0] == "smoke":
                rep = smoke(p, argv[1], Path(argv[2]))
            else:
                rep = run_script(p, argv[1], argv[2], Path(argv[3]))
    except Exception as e:  # noqa: BLE001
        rep = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:800]}", "problems": [], "snapshot": ""}
    print(json.dumps(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
