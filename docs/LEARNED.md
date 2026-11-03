# Learned facts

Short, verified lessons for coding agents (see `AGENTS.md` section 7). One fact per line, newest at the bottom.

- 2026-11-03 queue: bug tasks no longer pre-empt layers by type; only `source` in `user`/`health`/`gate` is urgent (`urgent()` in `core/cfgit.py`).
- 2026-11-03 planning: `plan_split` runs after `impact_analysis`; steps > `PLAN_MAX_STEPS` (4) or files > `task_max_files` grow the tree into linked slices (`core/cfa_split.py`).
- 2026-11-03 retries: `reuse_plan` reloads `run_dir/plan.json` after a `[no_edit]` attempt (`core/cfa_flow.py`), which saves about 3 min of re-planning.
- 2026-11-03 models: `deepseek-r1` is weak at tool calling; `qwen2.5-coder:14b` / `qwen3:14b` are better for agent mode. Set `OLLAMA_CONTEXT_LENGTH=16384`.
- 2026-11-03 tests: `tests/test_selfimprove.py` copies the repo and runs the whole suite (70-210 s); it also fails when a module exceeds 700 lines.
- 2026-11-03 git: a PR merged into a stacked PR's branch does not reach `main`; retarget or re-apply it.
