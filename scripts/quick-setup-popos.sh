#!/usr/bin/env bash
# Quick-Setup für Pop!_OS / Ubuntu: installiert alles, was der Agent braucht, und prüft danach die Mechanik.
# Nutzung:  bash scripts/quick-setup-popos.sh [--no-model] [--nvidia]
#   --no-model  Modell-Download überspringen (ca. 9 GB)
#   --nvidia    fehlenden NVIDIA-Treiber über Pop!_OS-Paket installieren (Neustart nötig)
set -euo pipefail
cd "$(dirname "$0")/.."

PULL_MODEL=1; INSTALL_NVIDIA=0
for a in "$@"; do
  case "$a" in
    --no-model) PULL_MODEL=0 ;;
    --nvidia) INSTALL_NVIDIA=1 ;;
    *) echo "Unbekannte Option: $a"; exit 1 ;;
  esac
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mFehler: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Bitte als normaler Benutzer starten (sudo wird bei Bedarf selbst genutzt)."
command -v apt-get >/dev/null || die "Dieses Skript ist für apt-basierte Systeme (Pop!_OS/Ubuntu)."

step "Systempakete (git, python3-venv, pip, tmux, curl, make)"
sudo apt-get update -qq
sudo apt-get install -y git python3 python3-venv python3-pip tmux curl make build-essential

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python >= 3.10 nötig."

step "NVIDIA-Treiber prüfen"
if nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
elif [ "$INSTALL_NVIDIA" -eq 1 ]; then
  sudo apt-get install -y system76-driver-nvidia
  warn "Treiber installiert. Bitte neu starten und das Skript danach erneut ausführen."
  exit 0
else
  warn "Keine NVIDIA-GPU erkannt. Ollama läuft dann nur auf der CPU (sehr langsam)."
  warn "Treiber installieren: bash scripts/quick-setup-popos.sh --nvidia"
fi

step "Ollama"
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
if command -v systemctl >/dev/null && systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
  sudo systemctl enable --now ollama
else
  warn "Kein systemd-Dienst gefunden, starte 'ollama serve' im Hintergrund."
  (nohup ollama serve >/tmp/ollama.log 2>&1 &)
fi
for _ in $(seq 1 30); do
  curl -fs http://localhost:11434/api/tags >/dev/null && break
  sleep 1
done
curl -fs http://localhost:11434/api/tags >/dev/null || die "Ollama antwortet nicht auf localhost:11434."

step "Python-Umgebung und Aider"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

step "Konfiguration"
cp -n .env.example .env && echo ".env angelegt" || echo ".env existiert bereits"

if [ "$PULL_MODEL" -eq 1 ]; then
  step "Modell laden (qwen2.5-coder:14b, ca. 9 GB)"
  ollama pull qwen2.5-coder:14b
  ollama create qwen2.5-coder-agent -f scripts/Modelfile.qwen-coder-14b
else
  warn "Modell übersprungen. Später: ollama pull qwen2.5-coder:14b && ollama create qwen2.5-coder-agent -f scripts/Modelfile.qwen-coder-14b"
fi

step "Mechanik-Test (ohne Modell)"
python3 saas_agent.py selftest | tail -2

printf '\n\033[1;32mFertig.\033[0m Los geht es mit:\n'
echo '  make init IDEA="Deine Produktidee"'
echo '  make tmux            # im Hintergrund starten (P=<name>, falls mehrere Projekte)'
echo '  make status          # Fortschritt, make stop zum Beenden'
