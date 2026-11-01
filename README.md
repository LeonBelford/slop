# CodeFactory — local autonomous agent team for open-source software

You provide **one idea**. CodeFactory aims for the best, fastest and cleanest open-source project it can build — quality for everyone, no monetization. It designs the project, builds it in small steps, then invents new features on its own,
tests, debugs, cleans up and documents until you stop it or a limit is reached. Everything runs locally with Ollama + Aider.

> The loop mechanics are tested with a stub model (`make selftest`, also in CI). They have not yet been run with a real model.
> Start with a small idea and watch `agent.log`.

## Requirements
Linux/macOS/WSL, Python ≥ 3.10, git, [Ollama](https://ollama.com/download). Recommended: 12 GB VRAM (RTX 3060) and 16 GB RAM.

## Usage (short version)
The detailed step-by-step guide is in **[docs/GUIDE.md](docs/GUIDE.md)**.

**1. One-time installation**
```bash
git clone -b main https://github.com/LeonBelford/slop && cd slop
bash scripts/quick-setup-popos.sh            # Pop!_OS/Ubuntu: install everything (--no-model, --nvidia)
make selftest                                # verify the mechanics without a model
```

**Web interface (optional, the CLI remains)**
```bash
make ui                                      # opens http://127.0.0.1:8765
make ui-lan                                  # reachable from other devices in your network (token link is printed)
```
Create projects, start/stop them, "Test now", give feedback, edit `IDEAS.md`, `SOURCES.md` and the master prompt, view the backlog and live log.
Runs locally only (127.0.0.1); agents keep running when you close the UI.

**Master prompt:** `MASTER.md` in the main folder applies to all projects and is picked up at the start of each session (`run`).

**2. Start per idea**
```bash
make init IDEA="Invoicing tool for freelancers" NAME=invoices   # creates projects/invoices/ (takes a few minutes)
make start-tmux P=invoices                                            # runs in the background
```
Write your own wishes, implementation hints and restructuring requests to `projects/invoices/IDEAS.md` at any time (also while it runs). The agent reads the file every iteration, replans when it changes and reconciles it with the product every 5 merges.

**Test it yourself without stalling the agent**
```bash
make test-build P=invoices                        # waits until the current task is done, builds the current state, writes projects/invoices/TESTING.md
make feedback P=invoices MSG="The button does not work"   # feedback goes into IDEAS.md, the agent replans
make start-tmux P=invoices                       # then let it continue
```
`make test-build` detects .NET (`dotnet publish` → `dist/`), Rust, Go, Node and FastAPI and tells you the start command.

**3. Individually, as needed**
```bash
make attach P=invoices                        # attach to the live session; Ctrl+B, then D to detach
make thinking  P=invoices                        # follow what the model is asked and answers (agent/llm.log)
make status P=invoices                       # progress
make stop P=invoices                         # clean stop after the current task
make force-stop P=invoices                    # kill switch: ends the agent and its processes immediately, mid-task
make list                                    # all projects
```
`P` can be omitted if there is only one project. For custom settings, copy `config.example.json`, adjust it and pass it with
`CONFIG=mine.json make init ...`.

## Languages and frameworks
The agent picks the best fit for your idea (`PRESET=auto`, default) or you choose one (`make init IDEA=... PRESET=go`).

| Key | Stack | Needs |
|---|---|---|
| `fastapi` | Python / FastAPI | python3 |
| `javascript` | Node.js + Express | node, npm |
| `typescript` | TypeScript + Express | node, npm |
| `go` | Go (net/http) | go |
| `rust` | Rust + axum | cargo |
| `csharp` | C# / ASP.NET Core | dotnet |
| `java` | Java + Spring Boot (Maven) | java, mvn (scaffold not verified) |
| `php` | PHP (Composer) | php, composer |
| `ruby` | Ruby + Sinatra | ruby, bundle |

If a toolchain is missing, the agent stops before doing anything and prints what to install and how. The UI shows the same hint in the new-project dialog.

## Internet access
Configured in `projects/<name>/agent/config.json` under `"web"`:
- `"mode": "sources"` (default): the agent only reads the URLs you list in `projects/<name>/SOURCES.md` (one per line, optional note), refreshes them every `refresh_hours` and uses the summaries when planning and coding.
- `"mode": "open"`: additionally searches the web itself (DuckDuckGo by default, or your SearXNG via `search_url` with `format=json`).
- `"mode": "off"`: no network access.

Fetching only allows http/https, refuses local and private addresses (also via redirects) and supports `blocked_domains`. Page content is treated as untrusted reference data, never as instructions.

## Self-improvement
Two levels, both bounded:
1. **Per project (automatic):** every `reflect_every` merges (or after 3 failed tasks) the agent reviews its metrics and failures, rewrites `agent/playbook.md` (working rules that are fed into every prompt), tunes a few whitelisted limits within hard bounds (`agent/tuning.json`) and notes ideas about the tool itself in `agent/tool_ideas.jsonl`.
2. **The tool itself (on demand):** `make self-improve` turns that evidence from all projects into up to 3 code changes, implements them in a git worktree on a branch `self-improve/<timestamp>` and keeps only changes where `selftest` and the unit tests stay green. Nothing is merged automatically: review with `git diff main...self-improve/<timestamp>` and merge yourself. `make self-improve ARGS=--dry-run` only writes proposals to `self_improve/`. After each kept change the agent also updates `README.md` and `docs/` (and logs it in `docs/SELF_IMPROVEMENTS.md`) in a separate commit. Safety guards (web fetch, UI, this module, existing tests, CI) are off limits. **Before every self-change a backup is made**: git tag `backup/pre-self-improve-<timestamp>` plus a bundle in `self_improve/backups/`; restore with `git reset --hard <tag>`.

## Knowledge base per project
`projects/<name>/agent/memory/knowledge.db` is a rebuildable search index (SQLite FTS5 keyword search + optional embeddings, fused by rank) over the code map, decisions, lessons, features and stored web pages. The readable files stay the source of truth; a broken index is rebuilt automatically. Embeddings are optional: `ollama pull nomic-embed-text` and they are used automatically (`knowledge.embeddings: "off"` disables them).
- **Planning checks consequences first:** after the plan names the files to change, the agent looks up earlier decisions, lessons and features tied to them (`known_consequences`) and hands them to the critique and to the coder.
- **Lessons fade:** each has source and date; a merged task that had it in context counts as a hit, a failed one as a miss. The weight halves every 60 days (`knowledge.half_life_days`); faded or disproven lessons move to `lessons_archive.jsonl`, never deleted.
- **Web sources** keep URL and date, are cited in prompts, and a research query already covered by stored pages is not searched again.
- **Chat** linked to a project also searches this index for your question.

## Browser tests (Playwright, optional)
`make e2e-setup` installs Playwright and Chromium into `.venv`. For web products the agent then starts the app after the build is green, opens it in a real browser (localhost only), fails on console errors, JS exceptions, failed requests and broken links, writes a click-through test `e2e/e2e_<task>.py` from the acceptance criteria and the page's accessibility snapshot, and lets the coder fix failures (2 rounds). Without Playwright this step is skipped with a hint; set `features.e2e` to `"off"` to disable it.

## Code, update and chat toggle (Web UI / CLI)
- **File changes** shows the whole repo as a folder tree. Click a file to read it; new/changed lines are green (choose the comparison: uncommitted, last commit, last 5/20 ...).
- **Reasoning** streams every model call live (reasoning, answer, prompt) and the live output of Aider and build/test.
- **Update:** `make update` (CLI) or the sidebar "Update" button. Fast-forward only, backup tag, rollback if the new code does not import. Stop running agents first.
- **Chat AI enabled** checkbox in the sidebar turns the chat off so it does not compete with the agent for the GPU.

## Chat (Web UI)
**Chat** in the sidebar (or **Chat about this** in a project) opens a streaming chat with a local model. Link one or more projects: their goal, architecture, backlog, ideas and recent log are sent as context. Chats are stored in `projects/.ui/chats/`. Answers render Markdown (headings, lists, code, links); Enter sends (Shift+Enter = new line); a thinking animation shows while the model loads or reasons. Under each answer, **Add to ideas** and **Run as task** hand it to the linked project's agent.

**Settings → Models:** install any Ollama model by name or from the recommended list (live progress, cancel) and remove models you no longer need (blocked while a running project uses them). All model fields in Settings and Chat are dropdowns of installed models.

## Directed tasks, transparency and file changes (Web UI)
- **Do a task** tab: write what you want ("add a dark mode toggle"); the agent does exactly that and nothing else, ahead of its own backlog. If the agent is running it is queued, otherwise it starts, finishes and stops. CLI: `make task P=<project> TASK="..."` or `python3 codefactory.py do --project P --task "..."`.
- **Change the tool itself** (sidebar): describe a change to the tool (Makefile, UI, ...). It runs `improve --task` on a branch `self-improve/<timestamp>`, never merges, and keeps `webaccess.py`, `selfimprove.py`, `.github/`, `.env` off limits. Review with `git diff HEAD...<branch>` and merge yourself. Autonomous `make self-improve` stays CLI-only.
- **Live "Now" panel:** current step (plan, implement, build, review, cleanup, docs, merge), task, attempt, whether the agent waits for the model or Aider is editing, and recent events. Backed by `agent/activity.json`, `events.jsonl`, `llm_now.json`, `coder_now.json`.
- **AI activity** tab shows every prompt and answer (`agent/llm.log`); **File changes** shows uncommitted files, files by churn and per-commit diffs.
- **Managing projects:** the **Settings** tab edits models (dropdowns of your installed Ollama models), limits, web mode and documentation language (saved to `agent/config.json`, applied at the next start), renames a project and deletes it (agent must be stopped; you type the name to confirm). The **Goal** tab is editable. In the backlog you can move a task to the top, cancel it or reopen blocked/cancelled ones; a running agent picks these changes up on its next task. The sidebar shows whether the model server is reachable.

## Structure
```
codefactory/
├─ codefactory.py        the complete loop (standard library only)
├─ webui.py, web/       local web interface (make ui, make ui-lan)
├─ security.py          security rules, scanners and runtime probe
├─ models.py            optional model catalog and routing (make models)
├─ MASTER.md            global directives for all projects (picked up per session)
├─ Makefile, scripts/   quick-setup-popos.sh · setup.sh (venv + model only) · run.sh · Modelfile
├─ .env.example, config.example.json
├─ docs/                OVERVIEW.md (what it does) · GUIDE.md (usage) · ARCHITECTURE.md (technical) · SECURITY.md (security plan)
├─ tests/, .github/     selftest and CI
└─ projects/            one folder per idea (not in Git)
   └─ <name>/
      ├─ TESTING.md     start instructions for the latest test build (from `make test-build`)
      ├─ IDEAS.md       your wishes and directives (optional, read continuously by the agent)
      ├─ repo/          the product as its own Git repo incl. docs/, CHANGELOG, README
      └─ agent/         state and memory of this project (outside repo/)
         ├─ GOAL.md, backlog.json, state.json, config.json, agent.log, metrics.jsonl
         ├─ memory/     codemap · features · decisions · lessons · ARCHITECTURE
         └─ runs/       prompts, plans, Aider logs per task
```
Each project thus has its own codebase and its own memory; nothing is shared between projects.

## Flow per task
1. Is `main` green? If not, repair it first.
2. Backlog empty → product review and ideation (rotating lenses, quality checklist, critic, duplicate filter).
3. **Impact analysis** with memory (codemap, dependencies in both directions, ADRs, lessons) → plan → plan critique.
4. Implementation with Aider on its own branch → **build + tests**, up to 3 debug rounds; features without tests are rejected.
5. Review (score < 7 → rework), cleanup pass (discarded if the build turns red).
6. Update memory and docs, commit, **build**, merge, **build on main** (roll back if red).
7. Failed attempts become lessons; after 3 attempts the task is marked blocked.

Ends on: `stop`, `max_hours`, `max_iterations`, 5 consecutive failures, or no new ideas for a sustained period.

## Models (RTX 3060, 12 GB)
Rough guidelines, not measured on your hardware; check tags and licenses at [ollama.com/library](https://ollama.com/library).

| Model | approx. size Q4 | Use |
|---|---|---|
| `qwen2.5-coder:14b` | 9 GB | default for all roles |
| `qwen2.5-coder:7b` | 5 GB | faster, e.g. as `summarizer` |
| `qwen3:8b` / `qwen3:14b` | 5 / 9 GB | planning and review |
| `deepseek-coder-v2:16b` | 9 GB (MoE) | fast due to few active parameters |
| `gemma3:12b` | 8 GB | ideas and docs |
| `gpt-oss:20b`, 24B+ | 13 GB+ | only with CPU offload, not recommended with 16 GB RAM |

Set `num_ctx` (the Modelfile does this), otherwise the context is truncated. The biggest quality lever: point `models.planner`/`reviewer`
at a stronger model via `LLM_API`; the code still comes from the local coder.

### Optional smart model routing
No extra model is required. `make models` (or Settings in the UI) lists optional models with their `ollama pull` command and marks the installed ones.
Installed models are detected and used automatically (`routing.mode` = `auto`, set `off` to disable):
- **light** (`qwen2.5-coder:7b`/`3b`/`1.5b`): docs, tests, single-file edits and the cleanup pass.
- **standard**: your configured `coder_model`, used for normal tasks.
- **heavy** (`qwen3-coder:30b`, `devstral:24b`, `qwen2.5-coder:32b`, `codestral:22b`): tasks touching 4+ files, retries after a failed attempt and debug rounds 2+.
- **final reviewer** (strongest installed of the heavy list, else `deepseek-r1:14b`/`phi4:14b`): the last independent review of every change.
Tasks are classified from the plan (type, number of files, attempt); there is no extra model call. Each switch costs a model load (seconds to a minute on 12 GB VRAM), models above ~12 GB spill into RAM and run slower. Force specific models with `routing.light`/`heavy`/`final_reviewer` in `config.json`. Logic in `models.py`.

## Network access
`make ui-lan` listens on all interfaces and prints a link with an access token (stored in `projects/.ui/token`). Open it once per device; a cookie keeps you logged in. It is plain HTTP, so use it on trusted networks only; the UI can start agents and delete projects. `make ui` stays local-only.

## Troubleshooting
See [docs/GUIDE.md](docs/GUIDE.md#10-troubleshooting).

- **`make init` seems stuck:** the first model answer can take several minutes (the model has to load into VRAM, and a 14B model on 12 GB VRAM / 16 GB RAM is slow). The agent prints `... still waiting for the model` every 30 s. Check `ollama ps` and `nvtop`/`nvidia-smi` in a second terminal; use a smaller model (e.g. `qwen2.5-coder:7b`) in `.env` if it is too slow. Do not press Ctrl+C: an interrupted init leaves no project behind.
- **"Cannot reach the model server" / "Model(s) not installed":** the agent now checks Ollama and the model names from `.env` before starting; follow the printed hint (`ollama serve`, `ollama list`).

## Security
Every change passes a security gate (static rules, attacker-minded review, runtime probe) and the whole project is audited regularly; see [docs/SECURITY.md](docs/SECURITY.md).

The agent works only in `projects/<name>/repo/`, but runs build and test commands there without a sandbox. Use a container or VM
if needed. It publishes nothing and spends no money; review the code before deploying.
