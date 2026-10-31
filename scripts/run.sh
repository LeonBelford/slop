#!/usr/bin/env bash
# Startet den Agenten mit .env und venv. Nutzung:
#   scripts/run.sh init "<Idee>" [Projektname]
#   scripts/run.sh run|status|stop|try [Projektname]
#   scripts/run.sh say <Projektname> "<Feedback>"
#   scripts/run.sh list
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -d .venv ] && export PATH="$PWD/.venv/bin:$PATH"

cmd="${1:?init|run|status|stop|list}"; shift
case "$cmd" in
  init)
    idea="${1:?Idee als Parameter}"; name="${2:-}"
    python3 saas_agent.py init --idea "$idea" ${name:+--name "$name"} ${CONFIG:+--config "$CONFIG"} ;;
  try)
    python3 saas_agent.py try ${1:+--project "$1"} ;;
  say)
    python3 saas_agent.py say ${1:+--project "$1"} --msg "${2:?Nachricht}" ;;
  run|status|stop)
    python3 saas_agent.py "$cmd" ${1:+--project "$1"} ;;
  list) python3 saas_agent.py list ;;
  *) echo "unbekannt: $cmd"; exit 1 ;;
esac
