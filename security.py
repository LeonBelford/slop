"""Security checks for generated projects (standard library only).

Three layers, all local:
  1. static rules over source files (secrets, injection, weak crypto, unsafe deserialization, ...)
  2. optional external scanners if installed (bandit, pip-audit, npm audit, gitleaks)
  3. a dynamic probe of the running app on localhost (headers, exposed files, cookies, CORS)

Every finding is {severity, rule, file, line, message, fix}. Severities: critical > high > medium > low.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

SEVERITIES = ("critical", "high", "medium", "low")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", "vendor", "e2e"}
SCAN_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".go", ".rs", ".java", ".rb", ".php", ".sh",
             ".yml", ".yaml", ".json", ".toml", ".env", ".cfg", ".ini", ".conf", ".sql"}
MAX_BYTES = 400_000

# (rule, severity, regex, message, fix, optional file-extension filter)
RULES: list[tuple[str, str, str, str, str, tuple[str, ...]]] = [
    ("secret-private-key", "critical", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
     "Private key in the repository", "Remove it, rotate the key, load it from the environment or a secret store", ()),
    ("secret-aws", "critical", r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id", "Remove, rotate, use environment variables", ()),
    ("secret-github", "critical", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", "GitHub token", "Remove, revoke, use environment variables", ()),
    ("secret-slack-stripe", "critical", r"\b(?:xox[baprs]-[A-Za-z0-9-]{10,}|sk_live_[A-Za-z0-9]{16,})\b", "Slack/Stripe live secret",
     "Remove, revoke, use environment variables", ()),
    ("secret-openai", "critical", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b", "API key (sk-...)", "Remove, revoke, use environment variables", ()),
    ("secret-assignment", "high",
     r"(?i)\b(?:api[_-]?key|secret|token|passwd|password|private[_-]?key)\w*\s*[:=]\s*[\"'][A-Za-z0-9_\-/+=.]{12,}[\"']",
     "Hard-coded credential", "Read it from an environment variable (os.environ / process.env) and document it in .env.example", ()),
    ("password-weak-hash", "high", r"(?i)\b(?:md5|sha1|sha256)\s*\(.*(?:pass|pwd)", "Password hashed with a fast hash",
     "Use argon2id, scrypt or bcrypt with a per-user salt (hashlib.scrypt, argon2-cffi, bcrypt)", ()),
    ("password-plaintext", "high", r"(?i)\b(?:password|passwd)\w*\s*(?:==|!=)\s*(?:req|request|body|data|input|user)\w*[\[.]",
     "Password compared in plain text", "Store only salted hashes and compare with a constant-time function (hmac.compare_digest)", ()),
    ("code-eval", "high", r"(?<![\w.])(?:eval|exec)\s*\(", "eval/exec on dynamic input is remote code execution",
     "Parse data with json/ast.literal_eval, dispatch through an allow-list", (".py", ".js", ".ts", ".jsx", ".tsx", ".php", ".rb")),
    ("code-new-function", "high", r"\bnew\s+Function\s*\(", "new Function() runs dynamic code", "Avoid dynamic code", (".js", ".ts", ".jsx", ".tsx")),
    ("cmd-shell-true", "high", r"subprocess\.\w+\([^)]*shell\s*=\s*True", "Shell command injection risk",
     "Pass an argument list and shell=False; never interpolate user input", (".py",)),
    ("cmd-os-system", "high", r"\bos\.(?:system|popen)\s*\(", "Shell command injection risk", "Use subprocess.run([...]) with an argument list", (".py",)),
    ("cmd-node-exec", "high", r"\bchild_process\b.*\bexec(?:Sync)?\s*\(|\bexec(?:Sync)?\s*\(\s*[`\"'].*\$\{", "Shell command injection risk",
     "Use execFile/spawn with an argument array", (".js", ".ts")),
    ("deserialize-pickle", "high", r"\b(?:pickle|cPickle|marshal|shelve)\.loads?\(", "Unsafe deserialization can execute code",
     "Use JSON or another data-only format", (".py",)),
    ("deserialize-yaml", "high", r"\byaml\.load\((?![^)]*Loader\s*=\s*(?:yaml\.)?(?:Safe|CSafe)Loader)", "yaml.load can execute code",
     "Use yaml.safe_load", (".py",)),
    ("sql-concat", "high", r"(?i)\b(?:execute|query|raw)\w*\(\s*(?:f[\"']|[\"'][^\"']*[\"']\s*(?:\+|%)|.*\.format\()[^)]*\b(?:select|insert|update|delete)\b",
     "SQL built from strings (injection)", "Use parameterized queries (placeholders) only", ()),
    ("sql-concat2", "high", r"(?i)[\"'](?:select|insert into|update|delete from)\b[^\"']*[\"']\s*\+\s*\w", "SQL built by concatenation",
     "Use parameterized queries", ()),
    ("xss-innerhtml", "medium", r"\.innerHTML\s*[+]?=|dangerouslySetInnerHTML|document\.write\(|insertAdjacentHTML\(",
     "HTML injection (XSS) if the value contains user data", "Use textContent or escape every dynamic value", (".js", ".ts", ".jsx", ".tsx", ".html")),
    ("tls-verify-off", "high", r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|NODE_TLS_REJECT_UNAUTHORIZED",
     "TLS verification disabled", "Keep certificate verification on", ()),
    ("debug-on", "medium", r"(?i)\bapp\.run\([^)]*debug\s*=\s*True|\bDEBUG\s*=\s*True\b", "Debug mode leaks internals and may allow code execution",
     "Read debug from the environment, default off", (".py",)),
    ("cors-wildcard", "medium", r"(?i)access-control-allow-origin[\"']?\s*[:,]\s*[\"']?\*|cors\(\s*\)|allow_origins\s*=\s*\[\s*[\"']\*",
     "CORS allows every origin", "Allow only the origins you need", ()),
    ("bind-all", "low", r"(?:host\s*=\s*|listen\(\s*\d+\s*,\s*)[\"']0\.0\.0\.0[\"']", "Server listens on all interfaces",
     "Bind to 127.0.0.1 unless it must be reachable, and then require authentication", ()),
    ("weak-random", "medium", r"(?i)(?:token|session|secret|nonce|otp|reset)\w*\s*=\s*.*\b(?:random\.(?:random|randint|choice)|Math\.random)\(",
     "Predictable random value used for a security token", "Use secrets.token_urlsafe() / crypto.randomBytes()", ()),
    ("jwt-none", "high", r"(?i)algorithms?\s*[:=]\s*\[?\s*[\"']none[\"']|verify_signature[\"']?\s*:\s*False", "JWT signature not verified",
     "Always verify signatures with a fixed algorithm list", ()),
    ("tar-extractall", "medium", r"\.extractall\(", "Archive extraction can write outside the target (path traversal)",
     "Validate member paths before extracting", (".py",)),
    ("path-join-user", "medium", r"open\(\s*(?:request|req|params|args|body)\w*[\[.]", "File opened from user input (path traversal)",
     "Resolve the path and check it stays inside an allowed base directory", (".py",)),
    ("http-url", "low", r"[\"']http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|www\.w3\.org|example\.)[\w.-]+\.\w{2,}", "Plain-HTTP URL",
     "Use https://", (".py", ".js", ".ts", ".jsx", ".tsx", ".go")),
]
COMPILED = [(r, s, re.compile(rx), m, f, e) for r, s, rx, m, f, e in RULES]
PLACEHOLDER = re.compile(r"(?i)(example|changeme|your[_-]|xxx+|<.*>|\$\{|process\.env|os\.environ|getenv|placeholder|dummy|test|fake|\*{3,})")


def finding(sev: str, rule: str, file: str, line: int, msg: str, fix: str) -> dict:
    return {"severity": sev, "rule": rule, "file": file, "line": line, "message": msg, "fix": fix}


def iter_files(repo: Path, only: list[str] | None = None):
    if only is not None:
        for rel in only:
            p = repo / rel
            if p.is_file():
                yield p
        return
    for p in repo.rglob("*"):
        if p.is_file() and not (set(p.relative_to(repo).parts) & SKIP_DIRS):
            yield p


def scan_text(rel: str, text: str) -> list[dict]:
    out: list[dict] = []
    ext = Path(rel).suffix.lower()
    is_test = bool(re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_|\.test\.|\.spec\.", rel))
    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 600 or "nosec" in line or "security: ignore" in line:
            continue
        for rule, sev, rx, msg, fix, exts in COMPILED:
            if exts and ext not in exts:
                continue
            m = rx.search(line)
            if not m:
                continue
            if rule.startswith("secret-") and (PLACEHOLDER.search(line) or is_test):
                continue
            if rule == "secret-assignment" and PLACEHOLDER.search(m.group(0)):
                continue
            out.append(finding("low" if is_test and sev != "critical" else sev, rule, rel, n, msg, fix))
    return out


def scan_repo(repo: Path, only: list[str] | None = None) -> list[dict]:
    """Static rules plus repository hygiene checks. `only` limits the scan to the given relative paths."""
    out: list[dict] = []
    for p in iter_files(repo, only):
        rel = str(p.relative_to(repo))
        name = p.name
        if name in (".env", ".env.local", ".env.production") or name.endswith((".pem", ".p12", ".pfx")) or name in ("id_rsa", "id_ed25519"):
            if not _gitignored(repo, rel):
                out.append(finding("critical", "secret-file", rel, 1, "Secret file is not ignored by git",
                                   "Add it to .gitignore, remove it from git (git rm --cached), rotate the contents"))
            continue
        if p.suffix.lower() not in SCAN_EXTS and name not in ("Dockerfile",):
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        out.extend(scan_text(rel, text))
    if only is None:
        out.extend(hygiene(repo))
    return dedupe(out)


def _gitignored(repo: Path, rel: str) -> bool:
    try:
        r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def hygiene(repo: Path) -> list[dict]:
    out: list[dict] = []
    gi = repo / ".gitignore"
    text = gi.read_text(errors="ignore") if gi.exists() else ""
    if not re.search(r"(?m)^\.env", text):
        out.append(finding("medium", "gitignore-env", ".gitignore", 1, ".gitignore does not exclude .env files",
                           "Add '.env' and '.env.*' (but keep .env.example) to .gitignore"))
    return out


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in items:
        k = (f["rule"], f["file"], f["line"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return sorted(out, key=lambda f: (SEVERITIES.index(f["severity"]), f["file"], f["line"]))


# ---- optional external scanners
def _run(cmd: list[str], cwd: Path, timeout: int = 180):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def external_scans(repo: Path) -> tuple[list[dict], list[str]]:
    """Runs bandit, pip-audit, npm audit and gitleaks when installed. Returns (findings, tools used)."""
    out: list[dict] = []
    used: list[str] = []
    sev_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low", "critical": "critical", "high": "high", "moderate": "medium", "low": "low"}
    if shutil.which("bandit") and any(repo.rglob("*.py")):
        r = _run(["bandit", "-r", ".", "-f", "json", "-q", "-x", "./tests,./.venv,./node_modules"], repo)
        if r and r.stdout.strip():
            try:
                for i in json.loads(r.stdout).get("results", []):
                    out.append(finding(sev_map.get(i["issue_severity"], "low"), "bandit-" + i["test_id"], i["filename"].lstrip("./"),
                                       i["line_number"], i["issue_text"], "See bandit " + i["test_id"]))
                used.append("bandit")
            except (ValueError, KeyError):
                pass
    if shutil.which("pip-audit") and (repo / "requirements.txt").exists():
        r = _run(["pip-audit", "-r", "requirements.txt", "-f", "json", "--progress-spinner", "off"], repo, 240)
        if r and r.stdout.strip():
            try:
                for d in json.loads(r.stdout).get("dependencies", []):
                    for v in d.get("vulns", []):
                        out.append(finding("high", "dep-" + v["id"], "requirements.txt", 1, f"{d['name']} {d['version']}: {v['id']}",
                                           "Upgrade to " + ", ".join(v.get("fix_versions", [])) if v.get("fix_versions") else "Replace the package"))
                used.append("pip-audit")
            except (ValueError, KeyError):
                pass
    if shutil.which("npm") and (repo / "package-lock.json").exists():
        r = _run(["npm", "audit", "--json", "--omit=dev"], repo, 240)
        if r and r.stdout.strip():
            try:
                for name, v in json.loads(r.stdout).get("vulnerabilities", {}).items():
                    out.append(finding(sev_map.get(v.get("severity"), "medium"), "dep-npm", "package.json", 1,
                                       f"{name}: {v.get('severity')} vulnerability", "npm audit fix or upgrade " + name))
                used.append("npm-audit")
            except (ValueError, KeyError):
                pass
    if shutil.which("gitleaks"):
        rep = repo / ".gitleaks-report.json"
        _run(["gitleaks", "detect", "--no-banner", "--report-format", "json", "--report-path", str(rep), "--source", "."], repo)
        if rep.exists():
            try:
                for i in json.loads(rep.read_text() or "[]"):
                    out.append(finding("critical", "gitleaks-" + i.get("RuleID", "secret"), i.get("File", ""), i.get("StartLine", 1),
                                       "Secret in the repository (incl. history)", "Remove, rotate and purge it from history"))
                used.append("gitleaks")
            except ValueError:
                pass
            rep.unlink(missing_ok=True)
    return out, used


# ---- dynamic probe (localhost only)
HEADERS = {
    "x-content-type-options": ("medium", "Add 'X-Content-Type-Options: nosniff'"),
    "content-security-policy": ("medium", "Add a Content-Security-Policy that blocks inline/third-party scripts"),
    "x-frame-options": ("low", "Add 'X-Frame-Options: DENY' (or CSP frame-ancestors) against clickjacking"),
    "referrer-policy": ("low", "Add 'Referrer-Policy: no-referrer'"),
}
SENSITIVE_PATHS = ["/.env", "/.git/HEAD", "/.git/config", "/config.json", "/backup.zip", "/db.sqlite3", "/.DS_Store", "/server-status", "/debug", "/admin"]


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


def _get(url: str, headers: dict | None = None, timeout: int = 5):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(20000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(20000).decode("utf-8", "ignore")
    except (OSError, ValueError):
        return 0, {}, ""


def probe(url: str) -> list[dict]:
    """Black-box checks against the running app. Refuses non-local URLs: never point this at third parties."""
    if not is_local(url):
        return []
    base = url.rstrip("/")
    code, hdr, body = _get(base + "/")
    if not code:
        return []
    out: list[dict] = []
    for h, (sev, fix) in HEADERS.items():
        if h not in hdr and not (h == "x-frame-options" and "frame-ancestors" in hdr.get("content-security-policy", "")):
            out.append(finding(sev, "http-header-" + h, "(runtime)", 0, f"Missing response header {h}", fix))
    if re.search(r"(?i)\d+\.\d+", hdr.get("server", "")) or "x-powered-by" in hdr:
        out.append(finding("low", "http-banner", "(runtime)", 0, "Server version banner leaks details", "Remove Server/X-Powered-By version info"))
    for p in SENSITIVE_PATHS:
        c, _, b = _get(base + p)
        if c == 200 and p != "/admin" and b.strip() and not re.search(r"(?i)<html", b[:300]):
            out.append(finding("critical" if p in ("/.env", "/.git/HEAD", "/.git/config") else "high", "exposed-path", p, 0,
                               f"Sensitive path {p} is served", "Never serve project files; serve only a dedicated public directory"))
    c, h2, _ = _get(base + "/", {"Origin": "https://evil.example"})
    if h2.get("access-control-allow-origin") in ("*", "https://evil.example"):
        out.append(finding("medium", "cors-open", "(runtime)", 0, "Arbitrary Origin is allowed by CORS", "Allow-list the origins"))
    for ck in re.findall(r"(?i)set-cookie:[^\n]*", "\n".join(f"{k}: {v}" for k, v in hdr.items())):
        if "httponly" not in ck.lower():
            out.append(finding("medium", "cookie-httponly", "(runtime)", 0, "Cookie without HttpOnly", "Set HttpOnly, Secure and SameSite=Lax/Strict"))
    c, _, b = _get(base + "/%00%27\"<x>")
    if re.search(r"Traceback \(most recent|at \w+\.\w+ \(.*:\d+:\d+\)|stack trace|SQLSTATE", b, re.I):
        out.append(finding("medium", "error-leak", "(runtime)", 0, "Error responses leak stack traces", "Return generic errors, log details server-side"))
    c, _, b = _get(base + "/..%2f..%2f..%2fetc/passwd")
    if "root:x:0:0" in b:
        out.append(finding("critical", "path-traversal", "(runtime)", 0, "Path traversal exposes /etc/passwd", "Normalize and confine file paths"))
    return dedupe(out)


# ---- reporting
def blocking(findings: list[dict], level: str = "high") -> list[dict]:
    cut = SEVERITIES.index(level)
    return [f for f in findings if SEVERITIES.index(f["severity"]) <= cut]


def summary(findings: list[dict]) -> dict:
    return {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}


def render(findings: list[dict], limit: int = 25) -> str:
    rows = [f"- [{f['severity']}] {f['file']}:{f['line']} {f['rule']}: {f['message']} -> {f['fix']}" for f in findings[:limit]]
    if len(findings) > limit:
        rows.append(f"- ... and {len(findings) - limit} more")
    return "\n".join(rows)
