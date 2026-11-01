# CodeFactory project map

Complete overview of **this repository** (the tool itself). The self-improve agent MUST read this file before it plans or
changes anything (`selfimprove.py` injects it into every planning prompt and passes it to Aider with `--read`; without
the file it refuses to run). Keep it accurate: every structural change (new module, route, config key, command, file
layout) updates it in the same change set.

## 1. What it is
A fully local autonomous coding agent. From one idea it plans, builds, tests, debugs, reviews, documents and keeps
improving a project, using Ollama (OpenAI-compatible API) for reasoning and Aider for code edits. Everything is Python
standard library except Aider (external) and Playwright (optional, browser tests). One web UI (`webui.py` + `web/index.html`)
and one CLI (`codefactory.py`, wrapped by `scripts/run.sh` and the `Makefile`).

Two different things are called "agent memory", do not mix them up:
- the **tool** (this repo): code in the repo root, state in `projects/` and `self_improve/` (both gitignored);
- a **generated project**: `projects/<name>/repo/` (the product, own git repo) plus `projects/<name>/agent/` (agent state).

## 2. Repository layout
| Path | Role |
|---|---|
| `codefactory.py` | The agent: config defaults, `LLM`, `Git`, `Backlog`, `Memory`, `Agent`, CLI (`main`), `selftest` |
| `selfimprove.py` | Self-improvement of this repo on `self-improve/<ts>` branches (worktree, tests, security gate, review, docs) |
| `webui.py` | HTTP server + `Service` (JSON API, process spawning, dashboard, tool preview/merge) |
| `web/index.html` | The whole UI in one file (CSS, markup, JS; no build step) |
| `runledger.py` | Run ledger of self-changes (requirements checklist, plan, stages; `self_improve/runs/`) and `verify_checkout` for the preview |
| `gpusched.py` | GPU queue between agents (fair slots, same-model affinity, side/main priority) |
| `chat.py` | Chat store (`projects/.ui/chats/`), project context, SSE streaming |
| `knowledge.py` | Per-project SQLite FTS5 + optional embeddings index (`knowledge.db`, a rebuildable view) |
| `security.py` | Static security rules, external scanners, probes, LLM review for generated code |
| `webaccess.py` | Read-only web fetch/search with SSRF guards (protected, see 7) |
| `stacks.py` | Language/framework presets (toolchain, scaffold, build/test commands) |
| `models.py` | Optional model catalog, routing by task weight |
| `e2e.py`, `e2e_runner.py` | Optional Playwright browser tests (runner runs in a subprocess with Playwright) |
| `update.py` | Update from `origin/main` (fast-forward only, backup tag, rollback) |
| `qr.py` | Dependency-free QR encoder for the phone link |
| `scripts/` | `run.sh` (CLI dispatcher, loads `.env`/venv), `setup.sh`, `quick-setup-popos.sh`, `Modelfile.qwen-coder-14b` |
| `Makefile` | Thin targets over `scripts/run.sh` (`make help`) |
| `MASTER.md` | Global instructions for all projects (copied into each run) |
| `config.example.json` | Example per-project config |
| `tests/` | `unittest` suites, one `test_<area>.py` per area; `python3 -m unittest discover -s tests` |
| `docs/` | `GUIDE.md` (usage), `ARCHITECTURE.md` (internals), `OVERVIEW.md` (feature notes), `SECURITY.md` |
| `README.md` | User entry point |
| `PROJECT_MAP.md` | This file |

Gitignored runtime folders: `projects/` (generated projects, `.ui/` settings and chats), `self_improve/` (backups, proposals,
worktrees, preview, logs), `.env`, `.venv`.

## 3. Data flow of the agent loop (`Agent` in `codefactory.py`)
`run()` outer loop (the script, never the model, decides when to stop): health check → sync `IDEAS.md` (replan) →
pick next backlog task → `attempt()`: impact analysis (+ plan critique) → `implement()` (Aider via `code()`) → `verify()`
(setup/build/test/lint commands from the preset) → `debug_loop()` → security gate → optional e2e → `review()` → merge
(fast-forward from the attempt branch) → memory update (`Memory.sync`, features, ADRs, docs). When the backlog is empty:
`ideate()`, `product_review()`, periodic refactor/coverage/security/architecture/reflect passes.
Run modes (`run.mode`: default / rounds / endless), STOP file, consecutive-failure guard.

LLM calls: `LLM.chat(tag, role, system, user)`; roles `planner`, `reviewer`, `summarizer`; `LLM.json` repairs JSON. In tests
`LLM.mock` replaces the model (any new tag must tolerate generic mock JSON). Every call is wrapped in
`sched.gpu(label, model)`; streams are published to `agent/llm_stream.json`, complete calls appended to `agent/thoughts.jsonl`
(compacted into `thoughts-archive/`), totals in `agent/metrics.json`.

## 4. Files a project writes (`projects/<name>/agent/`)
`GOAL.md`, `config.json`, `state.json`, `backlog.json`, `agent.pid`, `agent.log`, `STOP`, `memory/` (`codemap.json`, `features.json`,
`decisions.jsonl`, `lessons.jsonl`, `ARCHITECTURE.md`, `knowledge.db`), `playbook.md`, `metrics.jsonl`, `metrics.json`,
`thoughts.jsonl`, `llm_stream.json`, `llm_now.json`, `coder_now.json`, `coder_live.txt`, `cmd_now.json`, `cmd_live.txt`,
`sched.json`, `runs/`, web pages in `web/`. `projects/<name>/IDEAS.md` is the user's directive file.

## 5. Web UI (`webui.py` + `web/index.html`)
- Bound to 127.0.0.1; `--lan` binds all interfaces and requires the token (cookie `SameSite=Lax`). POSTs need header `X-UI: 1`,
  host/origin are checked (DNS-rebinding guard).
- `Service` methods back the routes: projects (`summary`, `list`, `detail`, `activity`, `thoughts`, `stream`, `tree`, `changes`,
  settings), dashboard (`dashboard`, `live_rate`, `coder_rate`, `gpu_stats`, `loaded_models`), tool (`tool`, `tool_task`,
  `tool_activity`, `tool_stream`, `tool_tree`, `tool_diff`, `tool_run`, `tool_cancel`, `tool_discard`, `tool_update_branch`, `branch_status`, `preview_*`, `tool_merge`, `tool_undo_merge`), update, chats, models.
- Route table is in `make_handler` (`handle_any`): POST `/api/tool/cancel`, `/api/tool/discard`, `/api/tool/update-branch`; GET `/api/build`, `/api/tool/run[?id=|branch=]`, `/api/projects`, `/api/project/<n>[/settings|llm|changes|diff|thoughts|tree|file|stream]`,
  `/api/dashboard`, `/api/tool[/stream|activity|tree|file|diff]`, `/api/chats`, `/api/chat/<id>`, `/api/models`, `/api/lan`, `/api/update/*`;
  POST `/api/projects`, `/api/project/<n>/<action>`, `/api/tool/task`, `/api/tool/preview[/tests|stop]`, `/api/tool/merge`,
  `/api/tool/undo-merge`, `/api/chats`, `/api/ui-settings`, `/api/master`.
- Processes started by the UI (`spawn`) go through `scripts/run.sh` in their own session so they survive the UI.
- `index.html`: global `view` (`project` | `tool` | `chat` | `dash`; tool tabs Task / Plan / Reasoning / Changes / Live log), `tab`/`toolTab_`, `refresh()` (3 s), `renderNow()` (1 s),
  fast 450 ms stream poll for the Reasoning tab (`LS`, `liveInto`), `setBody()` keeps scroll/open state, sidebar drawer on phones.

## 6. Self-improve (`selfimprove.py`)
`gather_evidence` → planner proposes ≤ 3 changes (`main`) or the user gives one task (`directed`) → `_implement`: backup tag +
bundle, git worktree on `self-improve/<ts>`, per proposal: `_impact` → Aider → protected-file check → `_security_gate` →
`_tests_green` (selftest + unit tests) → `_review` → commit → `_docs_pass`. Every run writes a ledger (`runledger.Ledger`): the request split into requirements (`tool_requirements`), per proposal the stages impact / implement / new tests / security / test / review / docs, and at the end a requirement check against the diff (`tool_verify`). A change without a new test file gets one extra "Add tests" round. Nothing is merged automatically. The UI can try a
branch in a separate worktree/port (`preview_*`; `runledger.verify_checkout` proves the preview runs that exact commit, UI and routes; `/api/build` reports the commit a server was started from) and merge it with a backup tag and undo (`tool_merge`).

## 7. Protected files and invariants (do not weaken)
- Protected from self-change: `webaccess.py`, `security.py`, `selfimprove.py`, `.github/`, `.env`, `scripts/quick-setup-popos.sh`
- Repair loop (`_work_on`, `FIX_ROUNDS=2`): gates (`_gates`) failing → `_diagnose` → coder repair round → re-check. Directed runs auto-restore protected files and may edit (not delete) existing tests (`tests_modified` in the ledger); auto runs revert immediately on a protected violation.
- Helpers: `_fast_tests`, `_new_tests_red`, `_keyword_files`, `_lessons`/`_remember`; `runledger.stats` feeds the UI track record. Plan: `docs/SELF_IMPROVE_ROADMAP.md`.
  (`webui.py` too, except for directed user tasks). Existing tests may not be modified, only new test files added.
- Never loosen: URL/SSRF checks, UI host/origin/token checks, shell-injection safety, the rule "the script decides when to stop".
- Standard library only for the tool itself (Aider and Playwright are optional external tools).
- `git reset --hard` in a project must never destroy agent state: state lives in `agent/`, outside the product repo.
- Timeouts: `limits.coder_timeout` counts silence for Aider; build/test commands keep absolute timeouts.

## 8. Config (`DEFAULT_CONFIG` in `codefactory.py`, overridden by `agent/config.json`, `.env`)
Keys: `preset`, `doc_language`, `llm_api`, `api_key`, `models`, `coder_model`, `routing`, `limits`, `context`, `run`, `scheduler`
(`priority`, `min_idle`, `slots`), `web`, `knowledge`, `features`, `commands`, `source_exts`, `src_dirs`, `ignore_dirs`. Env:
`LLM_API`, `PLANNER_MODEL`, `REVIEWER_MODEL`, `SUMMARIZER_MODEL`, `CODER_MODEL`, `CODEFACTORY_PRIORITY`, `CODEFACTORY_GPU_DIR`.
UI validation of settings lives in `webui.py` (`save_settings`), the form in `index.html` (settings tab).

## 9. CLI
`python3 codefactory.py <init|run|status|stop|force-stop|try|say|do|name|list|selftest|improve>`; `make` targets: `setup init start run
status stop force-stop kill list start-tmux attach thinking logs test-build try feedback say task do ui ui-lan models update
selftest self-improve e2e-setup`.

## 10. Testing rules
- `python3 -m unittest discover -s tests` (about 90 s) and `python3 codefactory.py selftest` (full agent loop with a mock LLM) must be green.
- New behaviour gets a new test file or new test methods in a new file; do not edit existing tests.
- The UI is tested by hand with Playwright against `webui.py --port <free> --projects-dir <tmp>`; the sandbox has no Ollama.

## 11. Where to change what
| Goal | Look at |
|---|---|
| Prompts / loop behaviour | `Agent.*` in `codefactory.py` (tags like `impact`, `review`, `ideate`) |
| Model calls, streaming, token counts | `LLM._read`, `_publish`, `record`, `_metrics` |
| Aider invocation and timeouts | `Agent.code`, `sh_live` (`idle=True`) |
| GPU sharing | `gpusched.py`, `Scheduler.gpu` |
| A new UI page | `index.html` (view + render function) and a `Service` method + route in `webui.py` |
| A new setting | `DEFAULT_CONFIG`, `webui.py` `save_settings` validation, settings form in `index.html`, docs |
| A new language preset | `stacks.py` |
| Docs | `README.md`, `docs/*.md`, and this file |
