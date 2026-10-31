# Technical details

Supplement to the [README](../README.md). It covers only what the README does not.

## Components in `saas_agent.py`
| Class | Responsibility |
|---|---|
| `LLM` | OpenAI-compatible client with retry and JSON repair (`LLM.mock` replaces it in the selftest) |
| `Git` | Branch per attempt, commit, fast-forward merge, hard reset, churn statistics |
| `Backlog` | Tasks with priority/status; duplicate filter by text similarity |
| `Memory` | Memory (below) |
| `Agent` | Main loop, impact analysis, implementation, debug, review, cleanup, merge, hygiene |

## Memory per project (`projects/<name>/agent/memory/`)
It is created automatically on `init` and always belongs to exactly one project.

| File | Content |
|---|---|
| `codemap.json` (+ `CODEMAP.md`) | per file: hash, purpose, API, data, side effects, invariants, pitfalls, `imports`, `imported_by` |
| `features.json` | feature → files, tests, acceptance criteria |
| `decisions.jsonl` | ADRs: context, decision, alternatives, consequences |
| `lessons.jsonl` | failed attempts and how to avoid them (condensed from 60 entries) |
| `ARCHITECTURE.md` | condensed overall view, regenerated every `architecture_refresh_every` merges |

- The dependency graph is built **statically** (Python AST, JS/TS imports, Jinja `extends/include`). The model only summarizes each file.
- Only files with a changed hash are re-summarized.
- Readable copies are placed in the product repo under `docs/` (ARCHITECTURE, CODEMAP, FEATURES, DECISIONS, `modules/*`).

## Impact analysis before every change
1. The LLM selects relevant files from the file index, supplemented by keyword hits.
2. Context package: goal, architecture, matching ADRs/lessons, related features, codemap of the files **and all neighbors** (callers and dependencies).
3. Plan as JSON: understanding, files to change/create, affected callers, invariants, risks, steps, tests.
4. Plan critique checks the graph for overlooked callers and adds files.

## User directives (`projects/<name>/IDEAS.md`)
`Agent.sync_ideas()` runs after the health check of each iteration. The hash of the comment-stripped text (`read_ideas`) is compared with
`state.ideas_hash`; in addition, `limits.ideas_review_every` merges force a reconciliation. The model (tag `replan`) receives the
directives, the last processed version (`agent/ideas_seen.md`), goal, features, open tasks and architecture, and returns new tasks,
priorities and cancellations (status `cancelled`). New tasks get priority 20+p (ahead of ideation), source `ideas`. The text is carried into
`GOAL.md`, `repo/docs/PRODUCT.md` and the context pack of every change. If replanning fails, it is retried.

## Master prompt and web UI
MASTER.md (repo root) is copied to agent/master_session.md on run() (read_master, without comments). Agent.directive appends it to the
ideation, review and replan prompts, Memory.context_pack to every change context. webui.py is a ThreadingHTTPServer (stdlib only, 127.0.0.1)
with a JSON API; start, init and try run as subprocesses via scripts/run.sh (own session, they survive the UI). POSTs require the header
X-UI: 1; host and origin are checked.

## Robustness
- The script decides whether to continue or end, never the model.
- State lives outside the repo; `git reset --hard` does not destroy it, and a run can be resumed after a crash.
- Before the docs update, memory is backed up and rolled back on failure.
- According to the prompt, tests must not be removed to turn the build green; the review checks this only indirectly.

## Config (`projects/<name>/agent/config.json`)
Overrides `DEFAULT_CONFIG` in `saas_agent.py`: `preset` (`fastapi`, `minimal`), `commands` (setup/build/test/lint), `models`,
`coder_model`, `limits.*`, `features.*`, `doc_language`. For a different stack, set `commands` and adjust `SCAFFOLDS`.

## Limitations
- Not tried with a real model/Aider; Aider flags depend on the version (`Agent.code`).
- Build/test commands of the generated code run without a sandbox.
