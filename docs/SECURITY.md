# Security plan

Goal: every project CodeFactory builds is as hard to attack as we can make it, and the agent proves it on every change instead of hoping. No tool can promise "perfect"; this plan makes the baseline high, automatic and auditable, and says honestly where human review is still needed.

## 1. Threat model

| Threat | Typical cause in generated code | Defence in the loop |
|---|---|---|
| Leaked secrets / API keys | key pasted into code, `.env` committed | secret rules, `.env` must be git-ignored, gitleaks if installed, rule in the coder prompt |
| Account takeover | plain or fast-hash passwords, predictable tokens | weak-hash and insecure-random rules, prompt demands argon2id/scrypt/bcrypt with salt, `secrets` for tokens |
| Remote code execution | `eval`/`exec`, `shell=True`, `os.system`, pickle, `yaml.load`, `new Function` | static rules (high = blocks the merge), attacker-minded LLM review |
| Injection | SQL by string concatenation, command strings, templates | SQL rules, parameterized queries mandated |
| XSS / CSRF / clickjacking | `innerHTML`, missing CSP/headers | XSS rule, runtime header probe |
| Path traversal / file exposure | `open(user_input)`, serving the project root, `tar.extractall` | rules plus runtime probe for `/.env`, `/.git/HEAD`, `../../etc/passwd` |
| Insecure transport | `verify=False`, plain `http://` | rules |
| Information leaks | debug mode, stack traces, server banners | rules plus runtime probe |
| Vulnerable dependencies | outdated packages | `pip-audit`, `npm audit` if installed; periodic audit tasks |
| Supply chain / the tool itself | agent edits its own security code | `webaccess.py`, `selfimprove.py`, `.github/`, `.env` are protected from self-change; backups before self-change |

## 2. Where it runs in the agent loop

1. **Plan (impact analysis):** the plan has a `security_impact` field: new inputs, endpoints, files, commands, secrets, dependencies and how each is protected. The coder prompt carries it, together with the rules "security by default".
2. **Implement:** the shared `RULES` forbid secrets in code, unsalted/fast password hashes, string-built SQL, eval/exec/shell=True, unconfined paths, verbose errors.
3. **Security gate (every code change, before review):**
   - static rules over the changed files,
   - an LLM review that thinks like a penetration tester about the diff (only concrete, confidence >= 6),
   - critical/high findings trigger up to 2 `security_fix` rounds (build must stay green), then the attempt fails and the lesson "X must not appear in new code" is stored in the knowledge base so the next attempt and the next tasks know.
4. **Runtime probe (web projects):** while the app runs for the browser test, a black-box probe checks response headers, exposed files, CORS, cookie flags, error leakage and path traversal. It only talks to `localhost`. Exposed secrets/files block the change; hardening gaps are tracked.
5. **Periodic audit (every `security_every` merged tasks, default 3):** a full-repository scan plus every installed external scanner. Findings become backlog tasks (`Security: fix N finding(s) in <file>`, priority 9.5 for critical/high), so they are fixed by the normal loop, with tests and review.
6. **Visible results:** `agent/security.json` holds the last change scan, the runtime probe and the last audit (counts per severity, findings, tools used).

## 3. Rules the built-in scanner enforces

Secrets (private keys, AWS, GitHub, Slack/Stripe, `sk-` keys, hard-coded password/token assignments, committed `.env`/`.pem`), weak password hashing, plaintext password comparison, `eval`/`exec`/`new Function`, `shell=True`/`os.system`/`child_process.exec`, pickle/marshal/shelve, `yaml.load`, SQL concatenation, `innerHTML`/`document.write`, disabled TLS verification, debug mode, CORS `*`, predictable randomness for tokens, JWT `none`, `tar.extractall`, path from user input, plain HTTP URLs, listening on `0.0.0.0`.
Findings in test files are downgraded to low; `# nosec` on a line suppresses a reviewed false positive.

## 4. Optional stronger tools (used automatically when installed)

`bandit` (Python SAST), `pip-audit` (Python dependencies), `npm audit` (Node dependencies), `gitleaks` (secrets incl. git history). Install: `pip install bandit pip-audit`, `sudo apt install gitleaks`. Without them the built-in rules still run.

## 5. Configuration

`agent/config.json`: `"features": {"security": "on"}` (set `"off"` to disable), `"limits": {"security_every": 3}`.

## 6. Honest limits

- Pattern rules and an LLM review find common mistakes, not every logic flaw (broken authorization, business-logic abuse). Treat the result as a strong baseline, not a certificate.
- The probe is black-box and basic; it is not a replacement for OWASP ZAP or a human pentest before real production use. Never point it at systems you do not own.
- A secret that was ever committed must be rotated, not just deleted.
- Before exposing a project to the internet: HTTPS via a reverse proxy, a dedicated non-root user, dependency updates, backups.

## 7. CodeFactory itself

The Web UI only listens locally unless LAN mode is on, then it requires a token, an `X-UI` header and an Origin check. Model names are validated, file paths are confined to the project folders, and no UI action runs shell strings.
