# CodeFactory overview

A living summary of everything CodeFactory does. **Rule: every new feature adds a line here in the same change** (a test checks that this file exists and covers the main areas).

## What it is
A fully local, autonomous coding agent. You give it one idea; it plans, builds, tests, debugs, documents and keeps improving the project, using Ollama (models) and Aider (code edits). Best quality and performance, open source, no monetization.

## The agent loop
1. **Understand:** turns the idea into `GOAL.md`, architecture and a prioritized backlog.
2. **Plan every task (impact analysis):** which files change, which callers break, invariants, risks, security impact, plus known consequences from earlier decisions and lessons.
3. **Implement** with Aider in a task branch, **build and test**, debug in rounds.
4. **Browser test** (web projects): Playwright smoke test plus a generated user-flow test.
5. **Security gate:** static rules, attacker-minded review and a runtime probe; critical/high findings block the merge.
6. **Independent review**, optional cleanup pass, docs, decision record, changelog.
7. **Merge** into `main` only when the build is green; otherwise roll back and learn.
8. **Self-direction:** ideates new features, audits the product, refactors hotspots, adds missing tests, runs regular security audits.
Limits (hours, iterations, failures) and a force-stop kill switch keep it under control.

## Memory and knowledge (per project)
Codemap, features, decisions, lessons (with expiry and weighting), web source cache. Hybrid search (SQLite FTS5 + optional embeddings) is shared by the agent and the chat. `knowledge.db` is a rebuildable view; the files are the source of truth.

## Models
Ollama models for planning, coding, review, summaries. Optional routing: light/standard/heavy coder by task class plus a final reviewer, used only when those models are installed. Everything is configurable per project.

## Languages and stacks
Python/FastAPI, Node, .NET, Go, Rust and more via presets; documentation language is configurable (default English).

## Web access
Optional research with guards: http/https only, no private addresses, blocked domains, content treated as untrusted data.

## Web UI (`make ui`, `make ui-lan`)
- **Reasoning tab:** live animated "thinking" indicator plus readable cards of recent plans, security reviews and decisions.
- Live "Now" panel with the cycle stepper (Plan, Implement, Build, Browser test, Security, Review, ...); the Live log is colour-coded (steps, success, security, warnings, errors) and logs every step.
- Live "Now" panel, AI activity log, file changes, backlog, goal, ideas.
- Directed tasks ("Do a task"), project create/rename/delete/settings, kill switch.
- **Chat:** streaming, Markdown rendering, Enter sends, thinking animation, link projects for context, "Add to ideas"/"Run as task".
- **Settings → Models:** install and remove Ollama models with live progress (recommended list included); all model fields are dropdowns of installed models.
- **Reasoning tab:** every model call in full (prompt, the model's own reasoning when it has one, answer), streamed live while it is written, plus live Aider and build/test output. Stored in `agent/thoughts.jsonl`.
- **File changes tab:** collapsible folder tree of the whole codebase; pick a file to read it with line numbers, new/changed lines highlighted, comparable against uncommitted work or the last N commits.
- **Update:** `make update` or the sidebar button pulls the latest version (fast-forward only, backup tag, dependency refresh, import check with automatic rollback). Blocked while agents run.
- **Chat toggle:** the chat AI can be switched off in the sidebar so it never competes with the agent for the model server.
- LAN mode with access token; "Change the tool itself" (self-improve on a branch).

## Self-improvement
Self-changes ("Change the tool itself") get the same transparency and safeguards as projects: a stage stepper (Plan, Backup, Impact, Implement, Security, Test, Review, Docs), a Reasoning tab with streamed thoughts and Aider/test output, a Changes tab (file tree with highlighted new lines per result branch) and a Live log. Before and after every change the agent runs impact analysis + plan critique, the security gate (static rules + attacker-minded review, one fix round), the full test suite and a final code review; `security.py` is protected from self-changes.
The agent can change the tool itself on a branch, never merging on its own; a backup tag and bundle is made before every self-change. Security-critical files are protected.

## Security
Secure-by-default coding rules, per-change security gate, runtime probe, periodic whole-project audit that becomes backlog tasks, optional bandit/pip-audit/npm audit/gitleaks. See [SECURITY.md](SECURITY.md).

## Quality of the tool
Stdlib-only core, unit tests (`make test`), end-to-end selftest with a stub model (`make selftest`), CI.

## Changelog of capabilities
- Core loop, memory, backlog, kill switch
- Multi-language stacks, web access
- Transparent web UI, directed tasks, project management, LAN mode
- Model routing, Playwright e2e, chat, self-improve backups
- Knowledge base with hybrid search, lessons with expiry
- Model manager and modern chat
- Reasoning tab with live thinking animation
- Security gate, runtime probe, periodic audits
- Full reasoning stream, code tree with highlighted changes, self-update, chat toggle
- Self-changes with impact analysis, security gate, review and the same UI tabs; reasoning view scroll anchoring and click-to-enlarge fields
