#!/usr/bin/env bash
# Starts the agent with .env and venv. Usage:
#   scripts/run.sh init "<idea>" [project name]
#   scripts/run.sh run|status|stop|try [project name]
#   scripts/run.sh say <project name> "<feedback>"
#   scripts/run.sh tmux|watch|logs [project name]
#   scripts/run.sh list
#   scripts/run.sh ui [--port 8765]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -d .venv ] && export PATH="$PWD/.venv/bin:$PATH"

cmd="${1:?init|run|status|stop|list}"; shift
case "$cmd" in
  init)
    idea="${1:?idea as parameter}"; name="${2:-}"
    python3 saas_agent.py init --idea "$idea" ${name:+--name "$name"} ${CONFIG:+--config "$CONFIG"} ${PRESET:+--preset "$PRESET"} ;;
  try)
    python3 saas_agent.py try ${1:+--project "$1"} ;;
  say)
    python3 saas_agent.py say ${1:+--project "$1"} --msg "${2:?message}" ;;
  run|status|stop)
    python3 saas_agent.py "$cmd" ${1:+--project "$1"} ;;
  list) python3 saas_agent.py list ;;
  tmux)
    command -v tmux >/dev/null || { echo "tmux is not installed: sudo apt install tmux (or run it in the foreground: make run P=...)"; exit 1; }
    name="$(python3 saas_agent.py name ${1:+--project "$1"})" || exit 1
    sess="agent-$name"
    if tmux has-session -t "$sess" 2>/dev/null; then echo "already running: tmux attach -t $sess"; exit 0; fi
    # the pane stays open after the agent exits, so errors remain visible
    tmux new -d -s "$sess" "bash scripts/run.sh run '$name'; echo; echo '[agent exited, press Enter to close]'; read _"
    sleep 1
    tmux has-session -t "$sess" 2>/dev/null && echo "started. Watch it: make watch P=$name  (or: tmux attach -t $sess; detach with Ctrl+B, then D)" \
      || echo "session ended immediately, run it in the foreground to see the error: make run P=$name" ;;
  watch)
    name="$(python3 saas_agent.py name ${1:+--project "$1"})" || exit 1
    if tmux has-session -t "agent-$name" 2>/dev/null; then exec tmux attach -t "agent-$name"; fi
    echo "No tmux session for $name; following the log instead (Ctrl+C to leave)."
    exec tail -n 40 -F "projects/$name/agent/agent.log" ;;
  logs)
    name="$(python3 saas_agent.py name ${1:+--project "$1"})" || exit 1
    echo "Following the model's prompts/answers (Ctrl+C to leave): projects/$name/agent/llm.log"
    exec tail -n 60 -F "projects/$name/agent/llm.log" ;;
  ui) python3 webui.py "$@" ;;
  improve) python3 saas_agent.py improve "$@" ;;
  *) echo "unknown: $cmd"; exit 1 ;;
esac
