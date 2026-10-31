P ?=

.PHONY: setup selftest init run status stop list tmux try say ui improve watch logs do

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

tmux:
	bash scripts/run.sh tmux $(P)

# Attach to the running agent (or follow its log): make watch P=invoices
watch:
	bash scripts/run.sh watch $(P)

# Follow what the model is asked and answers (planning, review, ...): make logs P=invoices
logs:
	bash scripts/run.sh logs $(P)

# Pause the agent and build a test version: make try P=invoices
try:
	bash scripts/run.sh try $(P)

# Add feedback: make say P=invoices MSG="The button does not work"
say:
	bash scripts/run.sh say $(P) "$(MSG)"

# Web UI at http://127.0.0.1:8765
ui:
	bash scripts/run.sh ui

# Let the agent improve its own code on a review branch (never auto-merged): make improve [ARGS=--dry-run]
improve:
	bash scripts/run.sh improve $(ARGS)

# Give the agent one explicit task and nothing else: make do P=name TASK="add a dark mode"
do:
	bash scripts/run.sh do $(P) "$(TASK)"
