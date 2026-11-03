# AGENTS.md: instructions for local coding agents

Read by VS Code Copilot agent mode (also with a local Ollama model), Aider (`--read AGENTS.md`), and any other agent that supports `AGENTS.md`.
This repo is **CodeFactory**: a fully local autonomous coding agent (Python standard library only, Ollama + Aider). Work in **small, verified steps**.

## 0. Work like this (the rules that prevent most mistakes)
1. **Read first, then edit.** Open `PROJECT_MAP.md` (layout, invariants, "where to change what") and the module you will touch. Never guess a function name: search for it (`grep -rn "def name" core dash webapi infra`).
2. **One small change at a time**: 1 to 3 files, at most about 4 steps. If the task needs more, write the steps down, do the first, verify, then continue.
3. **Verify after every change** (see section 2). A red test is never "fine for now". Fix the cause, do not weaken the test.
4. **Finish what you start**: code, test, docs (section 4) in the same change.
5. **No new dependencies.** The tool itself is standard library only.
6. **Do not touch protected files** (section 3) and never loosen security checks.
7. If you are unsure which of two designs is right, pick the simpler one that matches existing code, and say why in one sentence.
8. Output complete edits. Do not leave `...` or "rest unchanged" placeholders inside files.

## 1. Where things live
- Entry points: `codefactory.py` (Agent core + CLI + selftest), `webui.py` (web UI server), `selfimprove.py`.
- `core/`: agent engine as mixins (`cfa_build.py` attempt/debug loop, `cfa_plan.py` planning, `cfa_split.py` task splitting, `cfa_flow.py` quality steps, `cfa_gates.py` gates, `cfgit.py` Git + Backlog, `cfllm.py` model client, `progress.py` step pipelines).
- `webapi/`: `Service` mixins behind the `webui.py` routes. `web/`: browser UI (plain JS, no build step). `dash/`: dashboard logic. `infra/`: GPU, models, security, update.
- Tests in `tests/`, one file per area (`test_split.py`, `test_pipeline_speed.py`, `test_planning.py`, ...).
- **New logic goes into a new or the matching small module or mixin, never into `codefactory.py`** (it sits near the limit). Every module must stay **under 700 lines** (`tests/test_module_size.py`).

| Goal | Look at |
|---|---|
| Task order / queue | `Backlog.next`, `run_order` in `core/cfgit.py` |
| Planning, splitting, plan steps | `core/cfa_build.py` `impact_analysis`, `core/cfa_split.py`, `core/cfa_flow.py` (`clean_steps`, `split_steps`, `reuse_plan`) |
| Failures and debug loop | `core/failures.py`, `debug_loop` in `core/cfa_build.py` |
| Graph review / blueprint | `core/blueprint.py`, `core/cfa_plan.py` |
| A new setting | `DEFAULT_CONFIG`, `webui.py` `save_settings`, `web/` settings form, docs |
| A UI page or node card | `web/*.js` plus a route in `webui.py` / `webapi/` |

## 2. Commands (run the smallest one that covers your change)
- Lint: `.venv-lint/bin/ruff check .` (or `make lint`)
- JS syntax: `node --check web/<file>.js`
- One test file: `python3 -m unittest tests.test_split`
- Full suite (about 2.5 min): `python3 -m unittest discover -s tests`
- Whole loop with a mock model: `python3 codefactory.py selftest`
- Before you finish: lint, the touched tests, then the full suite and the selftest.

## 3. Invariants (do not weaken)
- Protected: `infra/webaccess.py`, `infra/security.py`, `selfimprove.py`, `.github/`, `.env`, `scripts/quick-setup-popos.sh`.
- Never loosen URL/SSRF checks, UI host/origin/token checks, shell-injection safety.
- Finished backlog tasks are never deleted or reopened by a re-plan (done ledger in `Backlog`).
- Only user tasks and health/layer-gate repairs may jump the plan order. Everything else follows the layers.
- Split slices keep `parent` / `split_into` so the tree stays traceable.
- `git reset --hard` in a project must not destroy agent state (`agent/` lives outside the product repo).
- Existing tests are only changed when they encode behaviour you deliberately changed. Say so in the commit message.

## 4. Definition of done
- [ ] Behaviour works and a test covers it (new test method or file; tests use stub LLMs, there is no Ollama in CI).
- [ ] `ruff` clean, full suite and selftest green, every module < 700 lines.
- [ ] `PROJECT_MAP.md` / `README.md` updated when structure, config, routes or commands changed.
- [ ] Commit message explains why. Open a PR against `main`.

## 5. Code style
Dense but readable Python 3.12, type hints where cheap, docstrings only for non-obvious behaviour, comments only where the code needs clarification. English identifiers and UI text; docs in English. Prefer pure helper functions (easy to test) over adding state to the `Agent`.

## 6. Pitfalls we already hit (check these before you finish)
- **Model JSON is untrusted.** Models return strings where dicts are expected (layers, lists of steps). Normalize with helpers like `blueprint.as_layers` and guard with `isinstance`.
- **Retries:** after a `no_edit` attempt the saved plan is reused. Do not re-plan when the plan was not the problem.
- **Filler plan steps** ("run tests", "update docs", "commit") are removed by `clean_steps`. Real refactor/cleanup/test steps stay.
- **Small model window:** the server window may be 4096 tokens even if 16384 is requested. Keep prompts short and respect `small_ctx()`.
- **Stacked PRs:** a PR merged into another PR's branch never reaches `main`. Check `gh pr list` and the base branch.
- **Shell quoting:** never wrap test commands in backticks or nested quotes.
- **Editing tests:** a failing old test may encode old behaviour. Check whether you changed that behaviour on purpose before editing it.

## 7. Grow this knowledge (do this yourself)
When you learn something a future agent needs (a pitfall, a non-obvious rule, a command that works, a decision and its reason), add **one short line** to `docs/LEARNED.md` in the same change:
`- YYYY-MM-DD <area>: <fact>. (why / where: path:line)`.
Rules: only verified facts, no duplicates (search first), no secrets, one fact per line. If a fact becomes wrong, fix or delete the line. Move facts that apply to every change up into this file.

## 8. How the user talks (read this to understand requests)
The user writes German, fast, with typos, mixed with English terms, often without punctuation. Never ask what a word means; use this table. Answer in German, short and concrete, unless asked otherwise. The user merges PRs manually: open PRs against `main` and say which one to merge first. If a request is ambiguous, pick the reasonable reading, state the assumption in one sentence, and continue.

| User says | It means in this repo |
|---|---|
| Pipeline | the whole path of one task: size_gate, plan, spec, tests first, implement (Aider), debug loop, gates, commit, cleanup, docs, merge (`core/cfa_build.py`, `core/progress.py`) |
| Step / Steps / Stepper / "oben angezeigte Steps" | a stage of that pipeline as shown in the UI stepper (`progress.route_for`, `web/app.js` `stepsFor`); "Plan-Schritte" are the 1-4 lines the planner writes for one task |
| Task / Aufgabe / Knoten / Node / Bubble | one backlog item (`T0001`), shown as a bubble in the project graph |
| Tree / Baum / Graph / Projekt-Graph | the dependency graph of all tasks (`depends_on`, `parent`, `split_into`, layers); "der Tree soll wachsen" = tasks split into linked slices (`plan_split`) |
| Layer / Schicht | build stage: core and base functions first, UI and features later (`layer`, layer gate) |
| Planen / Plan / Reevaluation / Neu evaluieren | planner and strategist calls: blueprint, graph review (`reevaluate`), per-task plan (`impact_analysis`) |
| Aufteilen / Splitten / klein halten | `split` into 2-4 slices (`core/cfa_split.py`) |
| Er skippt Steps / Mischmasch | the agent left its own plan order. Fix = follow the graph order (`Backlog.next`, `run_order`) |
| Fertig / done / nicht überschreiben | status `done` is final; the done ledger protects it from re-plans |
| Debugging / Debug-Runden | `debug_loop`, at most 3 rounds; targeted tests first |
| Clean code / Cleanup / Refactor | cleanup pass (full tier) and refactor tasks; keep them, but never as filler plan steps |
| Reasoning / Denken | model thinking shown in the Reasoning panel (old steps collapsed, current step open) |
| Agent / Agents / Orchestrator | the roles planner, strategist, coder, reviewer, summarizer, all driven by one script that decides when to stop |
| Feature | a backlog task of type `feature`; "Features schnell einbauen" = small tasks, tests and debugging placed at the right tier |
| Tier (klein/mittel/groß) | tiny, small, full: decides which gates run (`core/cfa_tiers.py`) |
| Schnell / blazing fast / weniger Zeit | fewer model calls and steps per task, no duplicate planning, tests only as deep as the risk needs, but never more errors |
| Dynamisch / smart | decided from the actual plan and project state, not a fixed list |
| Nachvollziehbar | traceable: links such as `parent`, `depends_on`, run order and reasons are visible in the UI |
| Vibecoden / Ideen | idea in, the agent plans and builds a whole project (`make init IDEA=...`) |
| Lokal / Locale KI | Ollama on the user's RTX 3060 12 GB with 16 GB RAM: small models, small context, so keep prompts and files small |
| Tool selbst ändern / self-improve | `selfimprove.py`, the agent improving CodeFactory itself |
| PR machen | commit, push a branch, `gh pr create --base main` (stacked PRs: base = previous branch) |

When the user pastes a log, read it for logic errors first (wrong order, repeated planning, lost work, wrong hand-over between stages) and fix the cause in the code, not the symptom. When the user says "überlege/prüfe nochmal", audit what you built for hand-over bugs, loops and broken features, then verify with tests.
