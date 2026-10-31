#!/usr/bin/env bash
# Starts the agent with .env and venv. Usage:
#   scripts/run.sh init "<idea>" [project name]
#   scripts/run.sh run|status|stop|try [project name]
#   scripts/run.sh say <project name> "<feedback>"
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
  ui) python3 webui.py "$@" ;;
  *) echo "unknown: $cmd"; exit 1 ;;
esac
