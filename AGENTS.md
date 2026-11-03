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

## 10. Think like a senior engineer: evaluate, do not give up (applies to every request)
**Never stop after one failed tool call.** A weak model fails by looping, quitting or answering the error text. Instead:
1. **Whose error is it?** Before you react to an error message decide where it comes from: your own tool or its schema (invalid tool name, missing argument, "tool not available", network/API/quota/permission, timeout), the project (traceback, failing test, build error) or your own wrong assumption. A tool error is NOT an answer to the user and NOT a fact about the project. Do not reply to it, do not retry the same call: change the approach.
2. **Fallback ladder** (take the next rung at once, without asking): web search fails or is unavailable -> read the code and `docs/` in the repo; fetch a known URL with the `#fetch` tool or `curl`; use general knowledge and say what is unverified. A search/setup/workspace tool fails -> use `grep`, `glob`, `view`, the terminal. A command fails -> read its output, fix the cause, run a smaller command. Two failures of the same kind -> stop and use a different method.
3. **Analysis questions are answered from the code**, not from the web. "Analyse / schau dir an / warum" means section 9. Searching the web is only for unknown external facts (library versions, error codes), at most two searches, then continue without it.
4. **Evaluate before you answer:** list 2 or 3 possible causes or designs, check each against the code or a command, pick the best one and say why. State what you verified and what is a guess.
5. **Self-check before you finish:** does the answer address the user's actual question, in their words (section 8)? Is every claim backed by `path:line`, a log line or a command result? Did you run the tests? If not, continue.
6. **Be dynamic:** a one-line question gets a short answer; a big task is split into steps and executed in order, verifying each. Never produce a plan and then stop: do the first step.

## 11. Web search without paying for an API
- **VS Code agent mode:** the built-in `#fetch` tool needs no key: give it a URL (a docs page, a GitHub issue, `https://html.duckduckgo.com/html/?q=<query>`). It is not a search engine, but DuckDuckGo's HTML page works as one.
- **CodeFactory itself:** `infra/webaccess.py` searches DuckDuckGo HTML by default; set `search_url` in the web settings to your own SearXNG for more reliable results.
- **Local SearXNG (free, private):** `docker run -d --name searxng -p 8888:8080 searxng/searxng`, enable the `json` format in its `settings.yml` (`search: formats: [html, json]`), then use `http://localhost:8888/search?q={q}&format=json` as `search_url`. Hosted search APIs with a free tier (Brave, Tavily) need a key.
- The web is only a supplement: read the repo first (section 9).
