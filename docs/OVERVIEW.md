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
- **Reasoning tab:** every model call in full (prompt, the model's own reasoning when it has one, answer), streamed live while it is written, plus live Aider and build/test output. Stored in `agent/thoughts.jsonl`. The live boxes are updated incrementally (lightweight `/stream` endpoints, fast poll, text appended to one DOM node), never re-rendered, so selection and scroll position survive while text is generated.
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

## GPU scheduling
`gpusched.py`: one MAIN agent has priority, SIDE agents (config `scheduler.priority`, `min_idle`) use the model only while main is idle. State is one JSON file per main process in `~/.codefactory/gpu`; `LLM.chat` and `Agent.code` wrap every GPU use in `sched.gpu()`. A side agent aborts a streaming call or stops Aider (`sh_live(gate=...)`, rc 75) when main becomes busy and retries later; a file lock allows only one side agent at a time. The UI shows the side state in the Now box (`agent/sched.json`).

## Run mode
`run.mode` in the config: `default` (3 empty idea rounds = saturated), `rounds` (`run.rounds` idea rounds, counted in `state.rounds_run` per run) or `endless` (no iteration/hour limit, idle wait instead of stopping; STOP file and the consecutive-failure guard still apply).

## Phone link (LAN mode)
In `make ui-lan` the sidebar shows a small "Phone link" button that opens a QR code of the token URL (`/api/lan`, rendered server-side by the dependency-free `qr.py`, byte mode, level M, up to ~210 bytes). Without `--lan` the endpoint answers `lan:false` and the button stays hidden.

LAN login cookie is `SameSite=Lax` (a QR scanner opens the link cross-site and `Strict` would drop the cookie on the redirect); the QR link uses the host you reached the UI by. The task panel's "Side task" checkbox starts a one-off `do` job with `CODEFACTORY_PRIORITY=side`.

## Dashboard and phone layout
`GET /api/dashboard` feeds the Dashboard view (polled every 1.5 s only while it is open). `LLM._read` counts stream chunks (Ollama emits about one token per chunk) and publishes `tokens`, `chars` and `t0` in `agent/llm_stream.json`; tokens/s is `tokens / (updated - t0)` and drops to 0 after 3 s of silence. Finished calls add to `agent/metrics.json` (totals and per-day counters; Aider output is not counted). GPU numbers come from `nvidia-smi` and loaded models from Ollama `/api/ps`; both are optional and silently absent when unavailable.

The layout switches at 800 px: the sidebar becomes an off-canvas drawer (☰ top left, backdrop, closes on selection or Escape), tables scroll horizontally, grids stack, inputs use 16 px to prevent iOS zoom. On desktop the « button collapses the sidebar and the choice is stored in `localStorage`.

## Smart GPU queue (no marking needed)
All non-side agents share the model server through `gpusched.py`: at most `scheduler.slots` (default 2, Settings → "Parallel model calls") calls run at once, first come first served, and only calls with the same model run side by side (Aider and the API name models differently, they are normalised). A different model waits for the card so a 12 GB GPU never swaps models back and forth; while one project builds or tests, the next one gets the GPU. The UI shows "Queued" in the Now box. `side` projects still only run while everybody else is idle. For real parallel decoding start Ollama with `OLLAMA_NUM_PARALLEL=2`.

## Token numbers
Calls send `stream_options.include_usage`, so finished calls add exact `completion_tokens`/`prompt_tokens` to `agent/metrics.json`; live values count stream chunks. Aider runs are counted from its "Tokens: X sent, Y received" lines; its live rate is an estimate (`~`, output growth / 4 chars).

## Reasoning log archive
`thoughts.jsonl` keeps the newest 150 calls; older ones move (gzip, prompts cut to 1500 chars) to `agent/thoughts-archive/<first>-<last>.jsonl.gz`. The Reasoning tab always opens at the top.

## Trying a self-improve branch before merging
Tool → Changes tab → "Try it before you merge": `POST /api/tool/preview` checks the selected `self-improve/<ts>` branch out as a detached git worktree in `self_improve/preview/` and starts its `webui.py` on a free port with an empty projects folder and its own GPU-queue folder. Your running tool, projects and `main` are not touched. "Try + run tests" also runs the unit tests and selftest inside it and shows pass/fail. "Stop and remove" kills both and removes the worktree. Merge afterwards (PR from the branch) or delete the branch with `git branch -D`.
