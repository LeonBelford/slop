P ?=

.PHONY: setup selftest init run status stop list tmux try say ui improve

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
	tmux new -d -s agent-$(P) "bash scripts/run.sh run $(P)" && echo "running: tmux attach -t agent-$(P)"

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
