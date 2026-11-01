# Self-improve roadmap

Goal: you say "add X", the tool plans, builds, tests, repairs itself and hands you a verified branch to try. Nothing reaches `main` without your merge, and nothing can break what already works.

## 1. The flow

request → requirements checklist → one small step per requirement → per step: impact analysis → code → gates → (failed? diagnose → repair, up to 2 rounds) → commit → docs → preview → you merge.

## 2. Safety net (must never regress)

| Layer | What it guarantees |
|---|---|
| Backup | git tag + bundle before every run, restore with `git reset --hard <tag>` |
| Worktree | all work happens on a copy, never in your checkout |
| Protected files | web access, security, `selfimprove.py`, CI, `.env` are off limits (auto mode reverts at once; directed mode restores them) |
| Gates | security gate, selftest, full unit tests, LLM review, requirement verification |
| Preview | the branch is started locally and checked (`verify_checkout`) before you merge |
| Merge by hand | no auto-merge, conflicts are shown (`mergeable`), "update branch" button |
| Undo | discard branch / revert merge / restore tag |

## 3. Quality levers (implemented)

- [x] Requirements checklist with per-requirement verification
- [x] Step-wise execution, passing steps are kept (`directed`)
- [x] Diagnose-and-repair loop (`FIX_ROUNDS`, `tool_diagnose`)
- [x] Test-first hint plus a red check: new tests that already pass without the change are flagged
- [x] Fast tests first (only the tests of the change), then the full suite
- [x] Lessons notebook `self_improve/lessons.md` fed back into later prompts
- [x] Content-based file candidates (`_keyword_files`) next to the planner's guess
- [x] Optional stronger model for the last repair round (`CODER_MODEL_BIG`)
- [x] Smart model routing across installed models (light docs coder, heavy coder for hard steps/late repair rounds, strongest reviewer)
- [x] Learning router: model choice adapts to recorded success per model
- [x] Track record in the UI (success rate, repaired steps, failed rounds)
- [x] Per-round log (problem and diagnosis) in the Plan tab

## Evaluation: making a small model succeed

| Option | Verdict |
|---|---|
| Split the 2700-line `codefactory.py` into small modules | **Chosen.** A 14B model with a 16k window cannot hold a 30k-token file; with modules under 700 lines it edits whole files reliably, steps stay local, `_fit` fits more files |
| Bigger context window | Rejected: KV cache pushes the model onto the CPU on 12 GB VRAM, slower overall |
| Plugin system with auto-discovery | Later: more moving parts than the benefit; mixins + re-export already give the same locality |
| Rewrite the pipeline as one agent loop | Rejected: the gate pipeline (tests, security, review) is what keeps small-model output safe |
| Run review beside the tests, pin context, cut endless reasoning | **Done** (see Speed in the README) |

## 4. Next

- [ ] Benchmark set of fixed tasks run against a scratch copy; compare success rate before/after a change to self-improve
- [ ] Queue: several wishes in a row, one branch each
- [ ] Post-merge watchdog: if the UI or selftest fails after a merge, offer one-click rollback
- [ ] Symbol index (ctags) for file selection instead of word counts
- [ ] Aider `--test-cmd` with the fast tests for in-session self-correction
- [ ] Smarter model scheduling between planner, coder and reviewer on 12 GB VRAM
