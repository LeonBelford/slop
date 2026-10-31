#!/usr/bin/env bash
# Startet den Agenten mit .env und venv. Nutzung: scripts/run.sh <init|run|status> <workspace> [Idee]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -d .venv ] && export PATH="$PWD/.venv/bin:$PATH"

cmd="${1:?init|run|status}"; ws="${2:?workspace-pfad}"
case "$cmd" in
  init) python3 saas_agent.py init --workspace "$ws" ${CONFIG:+--config "$CONFIG"} --idea "${3:?Idee als dritter Parameter}" ;;
  run|status) python3 saas_agent.py "$cmd" --workspace "$ws" ;;
  *) echo "unbekannt: $cmd"; exit 1 ;;
esac
