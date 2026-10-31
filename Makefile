P ?=

.DEFAULT_GOAL := help
.PHONY: help setup selftest init start run status stop list ui start-tmux tmux attach watch thinking logs task do self-improve improve test-build try feedback say

help:
	@echo "First time:   make setup"
	@echo "New project:  make init IDEA=\"your idea\" NAME=myproj"
	@echo "Start agent:  make start P=myproj     (background, survives closing the terminal)"
	@echo "Watch it:     make attach P=myproj     (live view; Ctrl+B then D leaves tmux)"
	@echo "Model chat:   make thinking P=myproj      (prompts and answers)"
	@echo "Status/Stop:  make status P=myproj / make stop P=myproj / make list"
	@echo "Web UI:       make ui                 (http://127.0.0.1:8765, can start/stop too)"
	@echo "Feedback:     make feedback P=myproj MSG=\"...\"   Test build: make test-build P=myproj"

setup:
	bash scripts/setup.sh

selftest:
	python3 saas_agent.py selftest

# make init IDEA="Invoicing tool for freelancers" [NAME=invoices]
init:
	bash scripts/run.sh init "$(IDEA)" $(NAME)

run status stop:
	bash scripts/run.sh $@ $(P)

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

# Let the agent improve its own code on a review branch (never auto-merged): make self-improve [ARGS=--dry-run]
self-improve improve:
	bash scripts/run.sh improve $(ARGS)

# Give the agent one explicit task and nothing else: make task P=name TASK="add a dark mode"
task do:
	bash scripts/run.sh do $(P) "$(TASK)"
