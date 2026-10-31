WS ?= workspaces/demo

.PHONY: setup selftest init run status stop tmux

setup:
	bash scripts/setup.sh

selftest:
	python3 saas_agent.py selftest

init:
	bash scripts/run.sh init $(WS) "$(IDEA)"

run:
	bash scripts/run.sh run $(WS)

status:
	bash scripts/run.sh status $(WS)

stop:
	touch $(WS)/agent/STOP

tmux:
	tmux new -d -s agent "bash scripts/run.sh run $(WS)" && echo "läuft: tmux attach -t agent"
