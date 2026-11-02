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
Code lives in five places: the entry points in the root (`codefactory.py`, `webui.py`, `selfimprove.py`), `core/` (agent engine), `infra/` (GPU scheduling, models, security, web access, e2e, update, ...), `dash/` (dashboard/Plan-tab logic: diagnostics, estimates, run ledger, chat) and `webapi/` (the `Service` mixins behind `webui.py`'s routes). Imports are `from core import cfutil`, `from infra import gpusched`, `from dash import estimate`, `from webapi import common`. Modules are kept small on purpose (each under 700 lines except `webui.py` and `selfimprove.py`, which are next in line; checked by `tests/test_module_size.py`): a small local model can read and edit a whole file within its context window. New features belong in a new or the matching small module, not into `codefactory.py`.

| Path | Role |
|---|---|
| `codefactory.py` | Entry point: `Agent` core (init, config, loop), CLI (`main`), `selftest`. Re-exports everything below, so `import codefactory as sa` still gives `LLM`, shell helpers, ... as attributes |
| `core/cfutil.py` | `DEFAULT_CONFIG`, constants, shell/JSON/process helpers |
| `core/cfllm.py` | `LLM` client (streaming, reasoning capture, reasoning cap, JSON answers) |
| `core/cfgit.py` | `Git`, `Backlog` |
| `core/cfmemory.py` | `Memory` (code map, dependency graph, ADRs, lessons, docs upkeep) |
| `core/cfprofile.py` | Per-project agent profile (`agent/profile.json`) and context files (`agent/context/`) fed into prompts |
| `dash/throughput.py` | Tokens, words/s, tokens/min and GPU share from `agent/metrics.json` (header cards, per project and for self-improve) |
| `core/cfa_build.py` | `BuildMixin`: model choice, coder call, verify, debug loop, review, `attempt` |
| `core/cfa_verify.py` | `VerifyMixin`: targeted tests, flaky-test retry, verify cache (tree hash), changed-line coverage gate, mutation check |
| `core/failures.py` | Failure classification (`classify`/`signature`/`advice`): detects stalled debug loops, gives the LLM a sharper hint |
| `core/progress.py` | Shared step pipelines + monotonic progress percentage and per-task sub-step log for the "what is it doing right now" UI |
| `core/cfa_flow.py` | `FlowMixin`: optional quality steps (tests first, step by step, acceptance check, retry strategy, test protection) |
| `core/cfa_security.py` | `SecurityMixin`: security gate/probe/audit, e2e check |
| `core/cfa_research.py` | `ResearchMixin`: ideation, product review, web research, sources/ideas sync, reflection |
| `core/cfa_plan.py` | `PlanMixin`: strategic planning. `make_blueprint` (init) designs layers, core first, and seeds the backlog with `layer`/`depends_on`; `blueprint_ctx`, `top_layer`, `layer_done` (tags `layer-N`), `has_ui`, `ui_note` |
| `core/cfa_split.py` | `SplitMixin`: a task that keeps failing is split into 2-4 smaller slices (`split_into_steps`, strategist model, max depth 2); blocked tasks are revived after other merges (`revive_blocked`, max 2x); `block_task` |
| `core/cfa_conductor.py` | `ConductorMixin`: per-task pipeline profile from recent outcomes (`conducting`: fast/flow/careful, never disables correctness gates), task journal (`note`, shown to every prompt of the task), `replan_if_stuck` |
| `core/cfa_write.py` | Last-resort writer: files the coder only printed in the chat are taken from its answer and written to disk (only allowed files) |
| `core/cfa_handoff.py` | `HandoffMixin`: every step passes the next one a pinned instruction block (goal, decisions with reasons, do, do not, done when, open); it leads every coder prompt and model call of the task |
| `core/cfa_priority.py` | `PriorityMixin.light_check`: light strategist priority check after every merge, changes logged with reasons; feature `triage_light` |
| `core/cfa_quality.py` | `QualityMixin`: size estimate before the start, spec before code (design + review, pinned), protected contract tests |
| `core/cfa_gates.py` | `GateMixin`: layer gate (integration, smoke, strategist) and the deterministic drift check after merges |
| `core/taskgraph.py` | Pure graph functions over backlog items: `components`, `visible_ids` (finished graphs hidden, finished tasks of active graphs kept), `trees`, `break_cycles`, `unlocks`, `levels`, `render` (prompt text), `raise_layers`, `signature` |
| `core/cfa_bench.py` | `BenchMixin`: performance guard; performance-graph tasks are rejected in the debug loop when `commands.bench` gets >15% slower (feature `perf_guard`) |
| `core/cfa_scout.py` | `ScoutMixin`: parallel, model-free look-ahead (likely files + symbols) for the next ready tasks, shown in the planning prompt (feature `scout`) |
| `core/graph_templates.py` | Ready-made task graphs (API endpoint, UI screen, migration, refactor module) added from the Overview |
| `core/cfa_hygiene_graph.py` | `HygieneGraphMixin`: refactor and performance graphs (chains with a `why` per task) scheduled from `schedule_hygiene`, never while one is open |
| `core/cfa_graph.py` | `GraphMixin`: `merge_tasks`, `split_into_steps` (applied from the re-evaluation's `merge`/`split`), `graph_replan_start` (a graph review at the start of every run; the re-evaluation changes nothing without a reason (`keep_plan`); feature `graph_replan`), `downstream_note` |
| `core/cfa_triage.py` | `TriageMixin`: every iteration re-scores open tasks for the current project state (fan-out bonus, failure penalty, `dyn`) and lays the plan out again (strategist) when enough was merged or tasks lack a layer; feature `triage` |
| `core/cfa_spike.py` | `SpikeMixin`: for uncertain tasks (revived, many risks or known consequences) a throwaway spike proves the approach before the real attempt; a failed spike triggers re-planning |
| `core/cfa_milestone.py` | `MilestoneMixin`: after each finished layer the strategist checks the product against the vision, adds missing tasks, cancels obsolete ones and can schedule a re-plan |
| `core/cfa_rules.py` | `RulesMixin`: the project playbook is versioned (`save_playbook`) and judged by its effect on the failure rate (`judge_playbook` rolls back worse rules); `master_suggestions` collects general rules for MASTER.md |
| `core/blueprint.py` | Blueprint data: `normalize`, `context_for`, `progress`, `render_md` (per project: blueprint.json in the agent folder, BLUEPRINT.md in the product docs) |
| `core/uidetect.py` | Whether a product has a UI (brief `interface`/`has_ui` plus UI files in the repo); CLI/API/library projects get no UI tests |
| `core/cfa_ops.py` | `OpsMixin`: user tasks, stop, test version, feedback, status, `reload_models` (re-reads models/coder/routing from `config.json` before each task) |
| `dash/estimate.py` | Duration estimates from comparable past tasks (`durations.jsonl` per project and for self-improve), queue forecast, write-access checks |
| `dash/diagnostics.py` | Dashboard key figures (`/api/diagnostics`): first-try rate, attempts, failure causes, model time, reasoning share, stalls, repo size, system load, findings |
| `dash/pdextra.py` | Extra project-dashboard data: Gantt timeline, failure causes, time per pipeline step, models per role, `task_detail` for the drawer |
| `web/widgets.js` | Widget board for dashboards: drag-and-drop order, hide, size, layout in localStorage and URL |
| `dash/sievidence.py` | Self-improve evidence: metric rule engine (`suggestions`), task journals and triage/gate history of the projects, `retry_plan` (stall detection: escalate model, change strategy), `run_times` for the dashboard |
| `dash/recommend.py` | Model advice per role from the project's call statistics (coder without reasoning, planner/reviewer with reasoning) |
| `dash/replay.py` | Run replay: copies a project to `projects/.replay/`, applies another model or hint, records the result in `agent/replays.jsonl` |
| `bench/harness.py` | Benchmark harness: fixed tasks (`bench/tasks.json`), per-task check, reports in `bench/results/`, A/B `compare` verdict; CLI `bench/run.py` (`make bench`) |
| `dash/history.py` | Per-day history series and quality figures (fail/retry rate, GPU hours, failure-class histogram) from `metrics.json`/`metrics.jsonl`, backs `/api/history` and the dashboard History section |
| `dash/toolmodels.py` | Model choice for self-improve (`self_improve/models.json`: planner/reviewer/summarizer/coder, light/heavy/final reviewer, auto or fixed); `apply` merges it into the run config, a per-task model pins the coder (`improve --model`) |
| `dash/chatweb.py` | Opt-in web search for chats: `search_context` returns the top pages as prompt context plus sources (per-chat `web` flag, project web settings apply) |
| `dash/toolchat.py` | Assistant chat about CodeFactory itself: system prompt, context (PROJECT_MAP, self-improve state, runs, branches, files the user names) and `extract_tasks` for ```task blocks; the chat is stored as a `tool: true` chat in `dash/chat.py` (`ChatStore.tool_chat`) |
| `infra/perf.py` | Ollama speed diagnostics, pinned context settings and context-budget trimming (`fit_context`) for Aider (`make doctor`) |
| `infra/notify.py` | Optional webhook notifications (ntfy/Slack/Discord/...) on task done/blocked/stalled/run stopped |
| `dash/sichecks.py` | Self-improve helpers split out of `selfimprove.py`: git wrapper, change lists, backup, requirement check, `gather_evidence` (projects plus benchmark and code health) |
| `dash/sifiles.py` | Self-improve file selection: dependents, context budget (`_fit`), content and symbol-name candidates (`_keyword_files`) |
| `dash/siroute.py` | Self-improve model routing, learned job class and the lessons notebook |
| `dash/sigate.py` | Benchmark gate (`SI_BENCH_GATE=off|warn|block`): the changed code must not lower the benchmark success rate |
| `dash/rollback.py` | Layer rollback: `git reset --hard layer-N` with a backup tag (agent stopped, clean tree) |
| `core/templates.py` | Project templates (CLI, Web-API, Library): layer plans appended to the idea |
| `infra/report.py` | Daily report (built, blocked, needs you) sent through the notify webhook (event `report`) |
| `infra/export.py` | Export a project as a clean repo with README, CI and LICENSE |
| `infra/window.py` | GPU time windows (`22:00-07:00`) to choose heavy or light models |
| `dash/siwatch.py` | Post-merge watchdog: `check` (compile, import web UI, selftest), `rollback` (revert the last self-improve merge), `install_hook` |
| `dash/modcards.py` | Module cards (purpose, public signatures, users) for small models; `make cards` writes `docs/MODULES.md` |
| `core/control.py` | `ControlMixin`: pause/resume/step/next-task/priority from the UI, pinned mid-task hints, spec-review gate (approve/edit/reject a design before code); `control_wait` also clears a stale pause and any leftover spec files on the first call of a fresh run |
| `core/cfa_ext.py` | `ExtMixin`: wires the control-tools modules below into the agent loop (`control_wait`/`control_pick`/`control_after_task`, spec gate via `make_spec`, changelog layer heading via `docsync.mark_layer`) |
| `dash/roles.py` | Per-project role overrides (temperature, reasoning on/off, extra prompt) in `agent/roles.json`; the model itself stays in the existing model settings |
| `infra/ctxtune.py` | Context-window auto-tuner: reads installed Ollama models and the GPU's free VRAM, recommends `num_ctx` per model, writes a ready-made Modelfile |
| `dash/codesearch.py` | Symbol search, "who uses this" and a Python call graph for the UI (ast for Python, regex for other languages) |
| `dash/testgen.py` | Finds source files without tests and queues "write tests" tasks; mutation check reuses `core/cfa_verify.py`'s mutation operators to judge whether the tests would notice a change |
| `dash/refactor.py` | Guarded refactor runs (tests green before and after, otherwise reset): symbol rename, dead-code candidates, oversized-module split task |
| `dash/sandbox.py` | Terminal and playground: commands and a live-started app run in a throwaway copy of the repo (`agent/sandbox`), never in the real one |
| `dash/events.py` | UI action log (`agent/events.jsonl`) merged with pipeline metrics and finished tasks into one "what happened" timeline |
| `dash/adrs.py` | Decision log search (reuses `infra/knowledge.py`'s KB index) and a per-project health score (tests, module size, drift, blockers) |
| `dash/docsync.py` | `mark_layer` adds a "Layer N done" heading to the existing CHANGELOG.md; `api_markdown` renders the code map as docs/API.md |
| `dash/chatagent.py` | Chat-agent tool layer: a fenced \`\`\`action block from the model or the user is validated against a tool/arg schema and executed through the existing project actions (`add_task`, `set_priority`, ...), nothing new underneath. Chats have a route: `pipeline` (tasks go through plan/build/test/merge, you confirm cards) or `direct` (`webapi/chat.py` runs a server-side loop of up to 14 model turns: the project .md files are read first, then the model explores, edits, tests and summarises on its own) |
| `dash/directtools.py`, `dash/directedit.py` | Direct-route tools: `read_docs`, `list_dir`, line-numbered `read_file`, `edit_file` (exact unique match), `write_file`, `delete_file`, `make_folder`, `write_note`; path-traversal guarded, every change is a git commit, refused while the project's agent is running |
| `dash/review.py` | Diff review with inline comments; a comment on a line becomes a new fix task referencing that file/line |
| `webapi/ext.py` | `ExtMixin` for `webui.py`: routes all of the above (control, roles, ctx tuner, code search, test gen, refactor, sandbox, events, ADRs, chat-agent actions) behind `/api/project/<name>/x/<what>` (GET) and the project action POST, plus background job polling (`JOBS`) for long-running mutation checks and sandbox runs |
| `web/ctl.js` | Frontend for the Control / Code search / Insight project tabs and the chat "Agent mode" action blocks; patches `web/app.js`'s `GROUPS`/`LEAF`/`fetchExtra`/`projectBody` (plain scripts, no module system) |
| `docs/RECIPES.md` | Step lists for the usual changes (flag, module, pipeline step, tile, route, bug fix) read by the coder |
| `selfimprove.py` | Self-improvement of this repo on `self-improve/<ts>` branches (worktree, tests, security gate, review, docs) |
| `webui.py` | HTTP server, router (`make_handler`), `Service` base class (combines the `webapi/` mixins below), process spawning, update/master-prompt endpoints |
| `webapi/common.py` | Shared constants and helpers for the `Service` mixins: `ROOT`, `PROCS`/`PULLS` (mutable, import the module and read `common.ROOT`/`common.PULLS` rather than binding the name, so monkeypatching in tests still works), `UiError`, `git`/`git_rc`, `spawn`, `tail`, `alive`, `read_live` |
| `webapi/graphedit.py` | `GraphEditMixin`: graph summaries/blocks/proposal for the overview, manual link/unlink/merge/split, templates, plan proposal apply/discard, replay history |
| `webapi/project.py` | `ProjectMixin`: per-project detail/activity/thoughts/stream, settings, create/rename/delete, backlog edit, agent profile |
| `webapi/dashboard.py` | `DashboardMixin`: `dashboard`, `history`, `gpu_stats`, `loaded_models`, `diagnostics` |
| `webapi/tool.py` | `ToolMixin`: self-improve review branches (`tool_*`), preview builds/merge, the tool's own activity/diff/tree views, class attribute `PREVIEW` |
| `webapi/models.py` | `ModelsMixin`: installed/catalog models, pull/cancel/delete |
| `webapi/chat.py` | `ChatMixin`: chat list/create/stream, default model |
| `web/index.html` | Head + body markup only; loads `web/style.css`, `web/app.js` (main UI), `web/dashboard.js` (dashboard), `web/init.js` |
| `dash/runledger.py` | Run ledger of self-changes (requirements checklist, plan, stages; `self_improve/runs/`) and `verify_checkout` for the preview |
| `infra/gpusched.py` | GPU queue between agents (fair slots, same-model affinity, side/main priority) |
| `dash/chat.py` | Chat store (`projects/.ui/chats/`), project context, SSE streaming |
| `infra/knowledge.py` | Per-project SQLite FTS5 + optional embeddings index (`knowledge.db`, a rebuildable view) |
| `infra/security.py` | Static security rules, external scanners, probes, LLM review for generated code |
| `infra/webaccess.py` | Read-only web fetch/search with SSRF guards (protected, see 7) |
| `infra/stacks.py` | Language/framework presets (toolchain, scaffold, build/test commands) |
| `infra/models.py` | Optional model catalog, routing by task weight |
| `infra/e2e.py`, `infra/e2e_runner.py` | Optional Playwright browser tests (runner runs in a subprocess with Playwright) |
| `infra/update.py` | Update from `origin/main` (fast-forward only, backup tag, rollback) |
| `infra/qr.py` | Dependency-free QR encoder for the phone link |
| `scripts/` | `run.sh` (CLI dispatcher, loads `.env`/venv), `setup.sh`, `quick-setup-popos.sh`, `Modelfile.qwen-coder-14b` |
| `Makefile` | Thin targets over `scripts/run.sh` (`make help`), plus `lint`/`check` |
| `ruff.toml`, `requirements-dev.txt` | Lint config and the pinned ruff version; `make lint` auto-creates `.venv-lint` from `requirements-dev.txt` |
| MASTER.md (personal, git-ignored) | Global instructions for all projects (copied into each run) |
| `config.example.json` | Example per-project config |
| `tests/` | `unittest` suites, one `test_<area>.py` per area; `python3 -m unittest discover -s tests` |
| `docs/` | `GUIDE.md` (usage), `ARCHITECTURE.md` (internals), `OVERVIEW.md` (feature notes), `SECURITY.md` |
| `README.md` | User entry point |
| `PROJECT_MAP.md` | This file |

Gitignored runtime folders: `projects/` (generated projects, `.ui/` settings and chats), `self_improve/` (backups, proposals,
worktrees, preview, logs), `.env`, `.venv`.

## 3. Data flow of the agent loop (`Agent` in `codefactory.py` plus the `cfa_*` mixins)
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
  `/api/dashboard`, `/api/history`, `/api/dashboard/stream` (SSE), `/static/<name>` (whitelisted css/js), `/api/tool[/stream|activity|tree|file|diff|models]`, `/api/chats`, `/api/chat/<id>`, `/api/models`, `/api/lan`, `/api/update/*`;
  POST `/api/projects`, `/api/project/<n>/<action>`, `/api/tool/task`, `/api/tool/preview[/tests|stop]`, `/api/tool/models`, `/api/tool/merge`,
  `/api/tool/undo-merge`, `/api/chats`, `/api/ui-settings`, `/api/master`.
- Processes started by the UI (`spawn`) go through `scripts/run.sh` in their own session so they survive the UI.
- `web/index.html` loads `web/style.css` + `web/app.js` + `web/dashboard.js` + `web/init.js` (served via `/static/`, no build step). Global `view` (`project` | `tool` | `chat` | `dash`; tool tabs Task / Plan / Reasoning / Changes / Live log), `tab`/`toolTab_`, `refresh()` (3 s), `renderNow()` (1 s, also shows an estimated % done + the recent sub-steps for the running task via `core/progress.py`),
  fast 450 ms stream poll for the Reasoning tab (`LS`, `liveInto`), `setBody()` keeps scroll/open state, sidebar drawer on phones.
- Dashboard (`dashboard.js`): filter/sort/hide/collapse per section, state mirrored into the URL hash (`#dash?...`) for shareable views, live updates over SSE (`dstream`) instead of plain polling, browser notifications on task/agent state changes, a History section (per-day bar charts from `/api/history`), and mobile card layout for tables below 700px.

## 6. Self-improve (`selfimprove.py`)
`gather_evidence` → planner proposes ≤ 3 changes (`main`) or the user gives one task (`directed`) → `_implement`: backup tag +
bundle, git worktree on `self-improve/<ts>`, per proposal: `_impact` → Aider → protected-file check → `_security_gate` →
`_tests_green` (selftest + unit tests) → `_review` → commit → `_docs_pass`. Every run writes a ledger (`runledger.Ledger`): the request split into requirements (`tool_requirements`), per proposal the stages impact / implement / new tests / security / test / review / docs, and at the end a requirement check against the diff (`tool_verify`). A change without a new test file gets one extra "Add tests" round. Nothing is merged automatically. The UI can try a
branch in a separate worktree/port (`preview_*`; `runledger.verify_checkout` proves the preview runs that exact commit, UI and routes; `/api/build` reports the commit a server was started from) and merge it with a backup tag and undo (`tool_merge`).

## 7. Protected files and invariants (do not weaken)
- Protected from self-change: `infra/webaccess.py`, `infra/security.py`, `selfimprove.py`, `.github/`, `.env`, `scripts/quick-setup-popos.sh`
- Repair loop (`_work_on`, `FIX_ROUNDS=2`): gates (`_gates`) failing → `_diagnose` → coder repair round → re-check. Directed runs auto-restore protected files and may edit (not delete) existing tests (`tests_modified` in the ledger); auto runs revert immediately on a protected violation.
- Helpers: `_fast_tests`, `_new_tests_red`, `_keyword_files`, `_lessons`/`_remember`; `runledger.stats` feeds the UI track record. Plan: `docs/SELF_IMPROVE_ROADMAP.md`.
- Model routing (`_route`, `_coder_for`, built on `models.resolve`/`classify`): light/standard/heavy coder per job, strongest reviewer for review jobs, defaults when a model is not installed. The user picks the model per job (UI: Change the tool → Models, stored by `dash/toolmodels.py`) or per task (model dropdown in the Task tab → `improve --task ... --model`).
- Learning router: `_learned_cls` + `runledger.model_stats` (attempts per model in the run ledger).
- Context budget `_fit` (Aider edit files, `CONTEXT_TOKENS`); impact analysis shared between directed steps; plan critique only for multi-file changes.
- Speed: `infra/perf.py` (pinned num_ctx for Aider via `--model-settings-file`, Ollama diagnostics, `make doctor`), `scripts/ollama-tune.sh`; reasoning cap in `LLM._read`; review runs in a thread beside the full test suite in `_gates`.
- Directed tasks with several requirements run as one proposal per requirement (`directed`), so passing steps are kept on the branch.
  (`webui.py` too, except for directed user tasks). Existing tests may not be modified, only new test files added.
- Never loosen: URL/SSRF checks, UI host/origin/token checks, shell-injection safety, the rule "the script decides when to stop".
- Standard library only for the tool itself (Aider and Playwright are optional external tools).
- `git reset --hard` in a project must never destroy agent state: state lives in `agent/`, outside the product repo.
- Timeouts: `limits.coder_timeout` counts silence for Aider; build/test commands keep absolute timeouts.

## 8. Config (`DEFAULT_CONFIG` in `codefactory.py`, overridden by `agent/config.json`, `.env`)
Keys: `preset`, `doc_language`, `llm_api`, `api_key`, `models`, `coder_model`, `routing`, `limits`, `context`, `run`, `scheduler`
(`priority`, `min_idle`, `slots`), `web`, `notify` (`webhook`, `events`: done/blocked/stalled/stopped), `knowledge`, `features` (includes `targeted_tests`, `flaky_retry`, `verify_cache`, `coverage_gate`, `mutation_check`), `commands`, `source_exts`, `src_dirs`, `ignore_dirs`. Env:
`LLM_API`, `PLANNER_MODEL`, `REVIEWER_MODEL`, `SUMMARIZER_MODEL`, `CODER_MODEL`, `CODEFACTORY_PRIORITY`, `CODEFACTORY_GPU_DIR`.
UI validation of settings lives in `webui.py` (`save_settings`), the form in `index.html` (settings tab).

## 9. CLI
`python3 codefactory.py <init|run|status|stop|force-stop|try|say|do|name|list|selftest|improve>`; `make` targets: `setup init start run
status stop force-stop kill list start-tmux attach thinking logs test-build try feedback say task do ui ui-lan models update
selftest self-improve e2e-setup`.

## 10. Testing rules
- `python3 -m unittest discover -s tests` (about 2.5 min) and `python3 codefactory.py selftest` (full agent loop with a mock LLM) must be green; `make check` runs the selftest plus `make lint` (ruff, auto-installs `.venv-lint`).
- New behaviour gets a new test file or new test methods in a new file; do not edit existing tests.
- `tests/test_ui_e2e.py` starts `webui.py` on a free port with a temp projects dir and drives it with Playwright (dashboard loads with no console errors, tab switching); skipped if Playwright/Chromium is not installed (`make e2e-setup`). Anything beyond that is still checked by hand with Playwright against `webui.py --port <free> --projects-dir <tmp>`; the sandbox has no Ollama.

## 11. Where to change what
| Goal | Look at |
|---|---|
| Prompts / loop behaviour | `Agent.*` methods in `core/cfa_build.py`, `core/cfa_research.py`, `core/cfa_security.py`, `codefactory.py` (tags like `impact`, `review`, `ideate`) |
| Model calls, streaming, token counts | `LLM._read`, `_publish`, `record`, `_metrics` |
| Aider invocation and timeouts | `Agent.code`, `sh_live` (`idle=True`) |
| GPU sharing | `infra/gpusched.py`, `Scheduler.gpu` |
| A new UI page | `index.html` (view + render function) and a `Service` method + route in `webui.py` |
| A new setting | `DEFAULT_CONFIG`, `webui.py` `save_settings` validation, settings form in `index.html`, docs |
| A new language preset | `infra/stacks.py` |
| Docs | `README.md`, `docs/*.md`, and this file |

## Recent additions
- `webapi/editor.py` + `web/editor.js`: in-browser code editor (tree, tabs, highlighting, find/replace, search, git, tests).
- `web/sidechat.js`: collapsible side chat (Ctrl+J); sticky mini bar (`drawMini` in `web/app.js`).
- `/api/models/status` (GPU/VRAM/boot state) and `/api/models/warm` (preload a model with keep_alive).
- Self-improve chat has its own Direct mode (SELF_TOOLS) editing CodeFactory itself.
- `dash/checkpoints.py`: preview/show/undo of direct chat edits (each write is a commit); `/api/chat/<id>/checkpoint`.
- `@file` / `@T0001` mentions in chat (`dash/chat.py` `mention_context`, autocomplete via `editor/mentions`); repo map (`codesearch.repo_map`) in chat context.
- `web/graphtools.js`: edit mode (Needs…/Merge/Split…, click an arrow to unlink), plan proposal card, templates, history replay slider.
- Graph review (`core/cfa_graph.py`): all triggers (run start, milestone, stuck, stale layout, blocked chain) are collected and ONE re-evaluation runs; the start review is skipped when the graph is unchanged and flawless (`graph_review_always` forces it). Harmful results (mass cancel, torn graphs) are rolled back; `plan_confirm` / "Preview re-plan" saves a proposal to confirm. `next()` prefers the chain of the last finished task.
- `web/taskgraph.js` + `web/tasktree.js`: the Overview is the task graph (bands per graph, chain highlight, zoom) plus the tree list below; finished graphs only via "Show finished graphs" (pref `showDone`). Editor Terminal panel: editor op `exec`.
