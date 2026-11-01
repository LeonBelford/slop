P ?=

.DEFAULT_GOAL := help
.PHONY: doctor update e2e-setup models ui-lan force-stop kill help setup selftest lint check init start run status stop list ui start-tmux tmux attach watch thinking logs task do self-improve improve test-build try feedback say

update:
	@python3 -m infra.update

help:
	@echo "First time:   make setup"
	@echo "New project:  make init IDEA=\"your idea\" NAME=myproj"
	@echo "Start agent:  make start P=myproj     (background, survives closing the terminal)"
	@echo "Watch it:     make attach P=myproj     (live view; Ctrl+B then D leaves tmux)"
	@echo "Model chat:   make thinking P=myproj      (prompts and answers)"
	@echo "Status/Stop:  make status P=myproj / make stop P=myproj (after the task) / make force-stop P=myproj (immediately) / make list"
	@echo "Web UI:       make ui                 (http://127.0.0.1:8765, can start/stop too)"
	@echo "Models:       make models             (optional extra models to download, installed ones are used automatically)"
	@echo "Browser tests: make e2e-setup        (optional Playwright, lets the agent click through web products)"
	@echo "Update:        make update              (pulls the latest version from GitHub, with backup tag and rollback)"
	@echo "Web UI (LAN): make ui-lan             (reachable from other devices, token link is printed)"
	@echo "Feedback:     make feedback P=myproj MSG=\"...\"   Test build: make test-build P=myproj"
	@echo "Lint/checks:  make lint (ruff, auto-installs a .venv-lint)   make check (selftest + lint)"

setup:
	bash scripts/setup.sh

doctor:
	python3 -m infra.perf

selftest:
	python3 codefactory.py selftest

lint:
	@test -x .venv-lint/bin/ruff || { python3 -m venv .venv-lint && .venv-lint/bin/pip install -q -r requirements-dev.txt; }
	.venv-lint/bin/ruff check .

check: selftest lint

# make init IDEA="Invoicing tool for freelancers" [NAME=invoices]
init:
	bash scripts/run.sh init "$(IDEA)" $(NAME)

run status stop:
	bash scripts/run.sh $@ $(P)

# Kill switch: ends the agent and everything it started right now, mid-task
force-stop kill:
	bash scripts/run.sh force-stop $(P)

list:
	bash scripts/run.sh list

start-tmux tmux:
	bash scripts/run.sh tmux $(P)

# Attach to the running agent (or follow its log): make attach P=invoices
attach watch:
	bash scripts/run.sh watch $(P)

# Follow what the model is asked and answers (planning, review, ...): make thinking P=invoices
thinking logs:
	bash scripts/run.sh logs $(P)

# Pause the agent and build a test version: make test-build P=invoices
test-build try:
	bash scripts/run.sh try $(P)

# Add feedback: make feedback P=invoices MSG="The button does not work"
feedback say:
	bash scripts/run.sh say $(P) "$(MSG)"

# Web UI at http://127.0.0.1:8765
ui:
	bash scripts/run.sh ui

# Web UI for your whole network (token-protected): open the printed link on any device
ui-lan:
	bash scripts/run.sh ui --lan

# Optional: Playwright + Chromium so the agent can test web products in a real browser
e2e-setup:
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install playwright
	.venv/bin/playwright install chromium
	@echo "If Chromium fails to start on a fresh Linux: sudo .venv/bin/playwright install-deps chromium"

# Optional models to download (and which ones are already installed)
models:
	python3 -m infra.models

# Let the agent improve its own code on a review branch (never auto-merged): make self-improve [ARGS=--dry-run]
self-improve improve:
	bash scripts/run.sh improve $(ARGS)

# Give the agent one explicit task and nothing else: make task P=name TASK="add a dark mode"
task do:
	bash scripts/run.sh do $(P) "$(TASK)"
