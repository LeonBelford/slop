# Learned facts

Short, verified lessons for coding agents (see `AGENTS.md` section 7). One fact per line, newest at the bottom.

- 2026-11-03 queue: bug tasks no longer pre-empt layers by type; only `source` in `user`/`health`/`gate` is urgent (`urgent()` in `core/cfgit.py`).
- 2026-11-03 planning: `plan_split` runs after `impact_analysis`; steps > `PLAN_MAX_STEPS` (4) or files > `task_max_files` grow the tree into linked slices (`core/cfa_split.py`).
- 2026-11-03 retries: `reuse_plan` reloads `run_dir/plan.json` after a `[no_edit]` attempt (`core/cfa_flow.py`), which saves about 3 min of re-planning.
- 2026-11-03 models: `deepseek-r1` is weak at tool calling; `qwen2.5-coder:14b` / `qwen3:14b` are better for agent mode. Set `OLLAMA_CONTEXT_LENGTH=16384`.
- 2026-11-03 tests: `tests/test_selfimprove.py` copies the repo and runs the whole suite (70-210 s); it also fails when a module exceeds 700 lines.
- 2026-11-03 git: a PR merged into a stacked PR's branch does not reach `main`; retarget or re-apply it.

- 2026-11-03 aider: a looping Aider never goes idle, so `idle=True` never cut it off (hard cap 6 h); `perf.looping` + `sh_live(stuck=...)` now stops it with rc 125 (`coder_loop`) and `code()` retries once with a minimal context in whole-file mode.
- 2026-11-03 local-agent: weak models answered tool errors (invalid tool name, failed search) as if they were the user's problem; AGENTS.md sections 10/11 define the fallback ladder and free web search (`#fetch`, DuckDuckGo HTML, SearXNG).
- 2026-11-03 pipeline: tasks target 15-25 min (`core/cfa_budget.py`): cut after an attempt only if another one of the same length would not fit and the task can still be split (`split_depth < MAX_SPLIT_DEPTH`); inside an attempt the debug loop stops at the soft limit when stalled, at the hard limit always (`[budget]` -> class `too_slow`). (core/cfa_build.py `_process`)
- 2026-11-03 notify: `infra/digest.tick` runs from the agent loop and after failed attempts; `done` pushes are suppressed while `notify.digest_minutes > 0`; idle alert uses `state["notify"]["seen"]`, not the unsent digest baseline. (infra/digest.py)
- 2026-11-03 graph: after a re-plan that adds, merges or splits tasks, blocked tasks the new plan does not carry are retired (cancelled, `retired=true`) and drop out of the graph; they only show in the per-project dashboard widget "Removed tasks" (feature `retire_blocked`).
