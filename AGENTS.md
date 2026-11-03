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

## 0b. Using tools (VS Code agent mode; for analysis see section 9)
- Answer the user's question. Never paste raw tool output (environment info, JSON like `resourcePath`) as your answer.
- To analyse a bug or "why does X happen": find the code with search/grep first, read the relevant function, then explain the cause with `path:line` and propose or make the fix. Logs the user pastes are evidence: quote the repeating line and find what produces it.
- Do not call environment-setup tools (configure Python, create workspace) unless asked; the venv is `.venv` and the commands are in section 2.

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

## 9. Analysis playbook ("schau dir die Pipeline an", "warum passiert das", "wie kann man das besser machen")
Use your own engineering knowledge (known failure patterns, better designs), but ground every statement in THIS codebase, which keeps growing: do not rely on this file alone, re-read the current code and `PROJECT_MAP.md` / `docs/LEARNED.md` each time. Do NOT call setup tools. Analyse the real code like a reviewer, in this order:
1. **Scope the question** in one sentence (which stage, which symptom). Map the user's words with section 8.
2. **Trace the real path.** Start at the entry (`_process` / `attempt` in `core/cfa_build.py`) and follow the calls stage by stage with grep and by reading each function. Write the order down as you verified it (stage, file:line). For a UI question trace `web/*.js` to the `webapi/` route.
3. **Follow the data.** For every stage note: what it receives, what it returns, what it stores (`run_dir/*.json`, `Backlog`, handoff text, memory) and who reads that later. Hand-over bugs hide here: a value written but never read, read before it is written, overwritten by a re-plan, or passed as the wrong type (model JSON).
4. **Use evidence.** Read pasted logs and `run_dir` files line by line. Quote the exact repeating or surprising line, then grep for the code that prints it. Count how often a stage runs per task and how long it takes (planning twice, retries that re-plan, loops).
5. **Look for these defect classes:** stage order that makes work wasted (plan before split), repeated model calls with the same input, loops without a stop condition (idle never triggers while output keeps coming), state lost on retry or restart, fallbacks that hide errors, limits that do not match the real model window (4096), tasks that jump the queue, filler steps, unguarded model output.
6. **Check your claim** before you report it: reproduce with a small test or a stub (`tests/` use stub LLMs and `coder_mock`) or read the exact lines again. Drop findings you cannot support.
7. **Report** a short ranked list: `finding, evidence (path:line or log line), why it costs time or correctness, concrete fix`. Highest impact first, at most 7. Say what you did not check.
8. **Then fix the top items** in small steps (section 0), with a test each, and update `docs/LEARNED.md`.
Do not stop at "it looks fine": if nothing is wrong, show which stages and hand-overs you verified.
