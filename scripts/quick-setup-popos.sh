#!/usr/bin/env bash
# Quick setup for Pop!_OS / Ubuntu: installs everything the agent needs, then checks the mechanics.
# Usage:  bash scripts/quick-setup-popos.sh [--no-model] [--nvidia]
#   --no-model  skip the model download (approx. 9 GB)
#   --nvidia    install a missing NVIDIA driver via the Pop!_OS package (reboot required)
set -euo pipefail
cd "$(dirname "$0")/.."

PULL_MODEL=1; INSTALL_NVIDIA=0
for a in "$@"; do
  case "$a" in
    --no-model) PULL_MODEL=0 ;;
    --nvidia) INSTALL_NVIDIA=1 ;;
    *) echo "Unknown option: $a"; exit 1 ;;
  esac
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mError: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Please run as a normal user (sudo is used automatically when needed)."
command -v apt-get >/dev/null || die "This script is for apt-based systems (Pop!_OS/Ubuntu)."

step "System packages (git, python3-venv, pip, tmux, curl, make)"
sudo apt-get update -qq
sudo apt-get install -y git python3 python3-venv python3-pip tmux curl make build-essential

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python >= 3.10 required."

step "Checking NVIDIA driver"
if nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
elif [ "$INSTALL_NVIDIA" -eq 1 ]; then
  sudo apt-get install -y system76-driver-nvidia
  warn "Driver installed. Please reboot and run this script again."
  exit 0
else
  warn "No NVIDIA GPU detected. Ollama will then run on the CPU only (very slow)."
  warn "Install the driver: bash scripts/quick-setup-popos.sh --nvidia"
fi

step "Ollama"
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
if command -v systemctl >/dev/null && systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
  sudo systemctl enable --now ollama
else
  warn "No systemd service found, starting 'ollama serve' in the background."
  (nohup ollama serve >/tmp/ollama.log 2>&1 &)
fi
for _ in $(seq 1 30); do
  curl -fs http://localhost:11434/api/tags >/dev/null && break
  sleep 1
done
curl -fs http://localhost:11434/api/tags >/dev/null || die "Ollama is not responding on localhost:11434."

step "Python environment and Aider"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

step "Configuration"
cp -n .env.example .env && echo ".env created" || echo ".env already exists"

if [ "$PULL_MODEL" -eq 1 ]; then
  step "Downloading model (qwen2.5-coder:14b, approx. 9 GB)"
  ollama pull qwen2.5-coder:14b
  ollama create qwen2.5-coder-agent -f scripts/Modelfile.qwen-coder-14b
else
  warn "Model skipped. Later: ollama pull qwen2.5-coder:14b && ollama create qwen2.5-coder-agent -f scripts/Modelfile.qwen-coder-14b"
fi

step "Mechanics test (without model)"
python3 codefactory.py selftest | tail -2

printf '\n\033[1;32mDone.\033[0m Get started with:\n'
echo '  make init IDEA="Your product idea"'
echo '  make tmux            # start in the background (P=<name> if there are several projects)'
echo '  make status          # progress, make stop to end it'
