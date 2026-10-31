# Guide

Step by step from an empty machine to a running product. Background and technical details: [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Installation (one-time, Pop!_OS/Ubuntu)
```bash
git clone -b main https://github.com/LeonBelford/slop && cd slop
bash scripts/quick-setup-popos.sh
```
The script installs packages, Ollama, Aider and the model (about 9 GB, duration depends on your connection). It asks for your `sudo` password.
If it reports "No NVIDIA GPU detected", run `bash scripts/quick-setup-popos.sh --nvidia`, reboot, and run the command again.

Verify:
```bash
make selftest                      # mechanics without a model: must print "SELFTEST OK"
ollama list                        # qwen2.5-coder-agent must appear
echo "Say hello" | ollama run qwen2.5-coder-agent
```

## 2. Start the first project
```bash
make init IDEA="Invoicing tool for freelancers with dunning" NAME=invoices
```
- Creates `projects/invoices/`, generates a skeleton (FastAPI, test, `requirements.txt`) and has the model write a product brief
  (`agent/GOAL.md`) and the first MVP tasks (`agent/backlog.json`). This takes a few minutes.
- Without `NAME=`, the folder name is derived from the idea. `make list` shows all projects.
- The more concrete the idea (target audience, core problem, must-have features), the better the result.

**Read before starting:** `projects/invoices/agent/GOAL.md` and `backlog.json`. Wrong direction? Adjust `IDEAS.md`, `GOAL.md` and the backlog now;
this is the cheapest point to correct course.

## 3. Let it run
```bash
make start-tmux P=invoices               # starts in the background (tmux session agent-invoices)
make attach P=invoices            # attach to the live session; Ctrl+B, then D to detach (falls back to following agent.log)
make thinking  P=invoices            # the model's prompts and answers, i.e. what it "thinks" (agent/llm.log)
make status P=invoices             # one line: iteration, merges, backlog, consecutive failures
tail -f projects/invoices/agent/agent.log
```
If there is only one project, you can omit `P=...`. Without tmux: `make run P=invoices` (blocks the terminal).

For each task the log shows: plan, Aider work, build/test, debug rounds, review, merge. `✔ T0003 merged` means: built, tested, in the product's `main`.

## 4. What lives where
| Path (`projects/<name>/`) | Content |
|---|---|
| `repo/` | the product (its own Git repo): code, tests, `README.md`, `CHANGELOG.md`, `docs/` |
| `repo/docs/` | `ARCHITECTURE.md`, `CODEMAP.md`, `FEATURES.md`, `DECISIONS.md`, `modules/*.md` |
| `IDEAS.md` | your wishes and directives, read continuously by the agent |
| `agent/GOAL.md` | vision and quality bar |
| `agent/backlog.json` | all tasks with status `pending`, `in_progress`, `done`, `blocked` |
| `agent/memory/` | codemap, features, decisions, lessons (the memory) |
| `agent/runs/<iter>-<id>/` | plan, context, prompts and Aider logs per task |
| `agent/metrics.jsonl` | review scores, files, LOC per merge |

View progress in the product: `git -C projects/invoices/repo log --oneline`.

## 4a. Start and try out the product
```bash
cd projects/invoices/repo
.venv/bin/uvicorn app.main:app --reload      # http://localhost:8000
```
The agent creates `repo/.venv` itself on the first build. While the agent is running, it is better to use a copy
(`git clone projects/invoices/repo /tmp/invoices-test`), because the agent switches branches and changes files.

## 5. Stop and resume
```bash
make stop P=invoices               # ends after the current task
make start-tmux P=invoices               # resumes from the saved state
```
It also ends by itself at `max_hours`/`max_iterations` (config), after 5 consecutive failures, or when it can no longer think of anything
useful. After an abort or crash, tasks in progress are resumed on the next start.

## 6. Intervening
**Via `IDEAS.md` (recommended, also while it runs):** `projects/<name>/IDEAS.md` was created by `make init`. Write wishes,
implementation hints ("how"), restructuring requests and "Do not" items there. The agent checks the file every iteration. On a change
it replans: new tasks (with priority over self-invented ideas), changed priorities, cancelling conflicting open tasks,
restructurings as small refactoring steps. The directives end up in `GOAL.md` and in the context of every change. Every `limits.ideas_review_every`
merges (default 5) it also reconciles the product with the file, even without a change. A saturated, stopped project continues after a
change on the next `make start-tmux`. The template itself (headings/comments only) is ignored.

Everything below **only while the agent is stopped**:
**Only while the agent is stopped**, otherwise it overwrites your changes (it keeps the backlog in memory).
- **Change direction:** edit `agent/GOAL.md`.
- **Add a task:** append an entry to `agent/backlog.json`:
  ```json
  {"id": "M001", "title": "CSV export of invoices", "description": "Button on the invoice list that downloads all invoices as CSV.",
   "type": "feature", "priority": 9, "status": "pending", "attempts": 0, "acceptance": ["Test checks CSV content"], "notes": []}
  ```
  Higher `priority` comes first, type `bug` always before everything else. Types: `feature`, `bug`, `refactor`, `test`, `docs`.
- **Remove a task:** set `status` to `blocked` or delete the entry.
- **Retry a blocked task:** read the cause in `agent/runs/<iter>-<id>/` and `agent/memory/lessons.jsonl`, refine `description`,
  set `status` to `pending` and `attempts` to `0`.
- **Correct lessons:** delete wrong lines in `agent/memory/lessons.jsonl`.

## 6a. Test in between (`make test-build`) and give feedback (`make feedback`)
1. `make test-build P=<name>`: sets STOP, waits until the current task is done, builds the last working state of `main`
   and writes `projects/<name>/TESTING.md` with the start command (e.g. `dotnet run`, `cargo run --release`, `go run .`, `npm start`,
   FastAPI: `.venv/bin/uvicorn app.main:app --reload`) and the location of the finished binary (`dist/`, `target/release/`).
2. Try it out in the folder `projects/<name>/repo`.
3. Bugs or wishes you found: `make feedback P=<name> MSG="..."` (lands under "Feedback from testing" in `IDEAS.md`; the agent replans).
   This also works while the agent is running.
4. `make start-tmux P=<name>` resumes the work.

## 6b. Master prompt and web interface
- **Master prompt:** `MASTER.md` in the main folder (next to the Makefile). Applies to all projects, e.g. language, style rules, "always tests".
  It is copied to `agent/master_session.md` at the start of each session (`make start-tmux` or `run`) and feeds into planning, ideation,
  replanning and every change context. Changes only take effect from the next start. The template itself (comments only) is ignored.
- **Web UI:** `make ui`, then `http://127.0.0.1:8765`. Projects on the left (green dot = running), on the right status, backlog, finished features,
  ideas/feedback, test build, live log and goal. At the top: start/stop and "Test now". "+ New idea" creates a project
  (takes a few minutes, progress at the top left). The UI uses the same files and commands as the CLI; you can mix both.
  It listens only on 127.0.0.1 and checks the host and origin of requests; do not forward it to the network.

## 6c. Self-improvement
- **Automatic, per project:** nothing to do. Every few merges (`limits.reflect_every`, default 6) or after 3 failed tasks the agent writes
  `projects/<name>/agent/playbook.md` (rules learned from its own failures, used in every prompt) and may adjust a few limits within fixed bounds
  (`agent/tuning.json`). You can read or edit the playbook; delete it to reset.
- **The tool itself, on demand:** after a few projects have run for a while, run `make self-improve ARGS=--dry-run` to see proposals, or `make self-improve`
  to let the agent implement them on a branch `self-improve/<timestamp>` (needs Ollama + Aider running). Only changes that keep `selftest`
  and the unit tests green survive; each one gets a follow-up commit that updates README/docs and `docs/SELF_IMPROVEMENTS.md`. Review with `git diff main...self-improve/<timestamp>`, merge with `git merge self-improve/<timestamp>`
  or ignore it. Nothing changes in the code you are running unless you merge.

## 7. Settings
File `projects/<name>/agent/config.json` (template: `config.example.json`, takes effect from the next start). Key values:
```json
{
  "doc_language": "English",
  "limits": {"max_hours": 8, "max_iterations": 200, "max_attempts_per_task": 3, "ideation_batch": 3, "min_review_score": 7},
  "features": {"cleanup_pass": false, "require_tests": true, "plan_critique": true},
  "commands": {"test": ".venv/bin/python -m pytest -q -x", "lint": ".venv/bin/python -m ruff check ."}
}
```
- Too slow: turn `cleanup_pass` off, use a smaller model for `models.summarizer` (e.g. `qwen2.5-coder:7b`).
- Quality too low: use a stronger model for `models.planner`/`reviewer` via `LLM_API` in `.env` (e.g. an OpenAI-compatible server).
- Other models: first `ollama pull <name>`, then enter the name in `models.*` and `coder_model` (`ollama_chat/<name>`).
  Then run `ollama create` with a suitable `num_ctx`, following the pattern of `scripts/Modelfile.qwen-coder-14b`.
- Custom checks (lint, types) via `commands.lint`; if a command fails, the change is considered unfinished.

## 8. Multiple projects
Each idea gets its own folder with its own repo and memory. `make list` shows them, `P=<name>` selects one.
Running several agents at once on a 3060 does not make sense, because they share the GPU.

## 9. Cleaning up
Delete a project: `rm -rf projects/<name>`. Everything else is in the `slop` repo folder and can stay.

## 10. Troubleshooting
| Symptom | Solution |
|---|---|
| `LLM not reachable` | `systemctl status ollama`, `curl localhost:11434/api/tags`; otherwise `ollama serve` |
| Model returns no JSON | occurs occasionally (the agent tries 3×); if frequent → another model in `models.*` |
| Agent seems forgetful | `num_ctx` too small: check `ollama show qwen2.5-coder-agent` |
| Unknown Aider option | `.venv/bin/pip install -U aider-chat`, adjust flags in `Agent.code` (`saas_agent.py`) |
| Task `blocked` | see section 6 |
| "main is red" in the log | the agent creates a repair task itself; otherwise run the build command from the config in `repo/` |
| Abort after 5 failures | read `agent.log` and `agent/runs/`, fix the cause, restart |
| Too slow | see section 7 |

## Security
The agent runs the build and test commands of the code it generates without a sandbox. Run it only on a machine or in
a VM/container that holds nothing important, and review the code before you publish it.
