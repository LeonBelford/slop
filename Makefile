P ?=

.PHONY: setup selftest init run status stop list tmux try say

setup:
	bash scripts/setup.sh

selftest:
	python3 saas_agent.py selftest

# make init IDEA="Rechnungs-Tool für Freelancer" [NAME=rechnungen]
init:
	bash scripts/run.sh init "$(IDEA)" $(NAME)

run status stop:
	bash scripts/run.sh $@ $(P)

list:
	bash scripts/run.sh list

tmux:
	tmux new -d -s agent-$(P) "bash scripts/run.sh run $(P)" && echo "läuft: tmux attach -t agent-$(P)"

# Agent pausieren, Testversion bauen: make try P=rechnungen
try:
	bash scripts/run.sh try $(P)

# Feedback eintragen: make say P=rechnungen MSG="Der Button geht nicht"
say:
	bash scripts/run.sh say $(P) "$(MSG)"
