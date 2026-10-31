#!/usr/bin/env bash
# Einmaliges Setup: Python-venv, Aider, Ollama-Modell mit passendem Kontext (12 GB VRAM).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v git >/dev/null || { echo "git fehlt"; exit 1; }
command -v ollama >/dev/null || { echo "Ollama fehlt: https://ollama.com/download"; exit 1; }

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -r requirements.txt

ollama pull qwen2.5-coder:14b
ollama create qwen2.5-coder-agent -f scripts/Modelfile.qwen-coder-14b

cp -n .env.example .env || true
echo "Fertig. Test: make selftest   Start: make init IDEA='...'  dann make run"
