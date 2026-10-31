#!/usr/bin/env bash
# Starts the agent with .env and venv. Usage:
#   scripts/run.sh init "<idea>" [project name]
#   scripts/run.sh run|status|stop|force-stop|try [project name]
#   scripts/run.sh say <project name> "<feedback>"
#   scripts/run.sh start|tmux|watch|logs [project name]
#   scripts/run.sh do <project name> "<task>"
#   scripts/run.sh list
#   scripts/run.sh ui [--port 8765]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -d .venv ] && export PATH="$PWD/.venv/bin:$PATH"

cmd="${1:?init|run|status|stop|force-stop|list}"; shift
case "$cmd" in
  init)
    idea="${1:?idea as parameter}"; name="${2:-}"
    python3 codefactory.py init --idea "$idea" ${name:+--name "$name"} ${CONFIG:+--config "$CONFIG"} ${PRESET:+--preset "$PRESET"} ;;
  try)
    python3 codefactory.py try ${1:+--project "$1"} ;;
  say)
    python3 codefactory.py say ${1:+--project "$1"} --msg "${2:?message}" ;;
  run|status|stop|force-stop)
    python3 codefactory.py "$cmd" ${1:+--project "$1"} ;;
  do)
    python3 codefactory.py do ${1:+--project "$1"} --task "${2:?task}" ;;
  list) python3 codefactory.py list ;;
  tmux)
    command -v tmux >/dev/null || { echo "tmux is not installed: sudo apt install tmux (or run it in the foreground: make run P=...)"; exit 1; }
    name="$(python3 codefactory.py name ${1:+--project "$1"})" || exit 1
    sess="agent-$name"
    if tmux has-session -t "$sess" 2>/dev/null; then echo "already running: tmux attach -t $sess"; exit 0; fi
    # the pane stays open after the agent exits, so errors remain visible
    tmux new -d -s "$sess" "bash scripts/run.sh run '$name'; echo; echo '[agent exited, press Enter to close]'; read _"
    sleep 1
    tmux has-session -t "$sess" 2>/dev/null && echo "started. Watch it: make attach P=$name  (or: tmux attach -t $sess; detach with Ctrl+B, then D)" \
      || echo "session ended immediately, run it in the foreground to see the error: make run P=$name" ;;
  start)
    # Works with or without tmux: background process, output in projects/<name>/agent/run.out
    name="$(python3 codefactory.py name ${1:+--project "$1"})" || exit 1
    pidf="projects/$name/agent/agent.pid"
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then echo "$name is already running. Follow it: make attach P=$name"; exit 0; fi
    mkdir -p "projects/$name/agent"
    if command -v tmux >/dev/null; then
      bash scripts/run.sh tmux "$name" && exit 0
    fi
    nohup python3 codefactory.py run --project "$name" > "projects/$name/agent/run.out" 2>&1 < /dev/null &
    sleep 3
    echo "started in the background (no tmux). Follow it: make attach P=$name   Stop: make stop P=$name" ;;
  watch)
    name="$(python3 codefactory.py name ${1:+--project "$1"})" || exit 1
    if tmux has-session -t "agent-$name" 2>/dev/null; then exec tmux attach -t "agent-$name"; fi
    echo "No tmux session for $name; following the log instead (Ctrl+C to leave)."
    exec tail -n 40 -F "projects/$name/agent/agent.log" ;;
  logs)
    name="$(python3 codefactory.py name ${1:+--project "$1"})" || exit 1
    echo "Following the model's prompts/answers (Ctrl+C to leave): projects/$name/agent/llm.log"
    exec tail -n 60 -F "projects/$name/agent/llm.log" ;;
  ui) python3 webui.py "$@" ;;
  improve) python3 codefactory.py improve "$@" ;;
  *) echo "unknown: $cmd"; exit 1 ;;
esac
