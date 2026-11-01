#!/usr/bin/env bash
# Makes Ollama fast on a single 12 GB GPU: flash attention, quantised KV cache, one model in VRAM, long keep-alive.
# Usage: sudo bash scripts/ollama-tune.sh   (systemd installs); afterwards: ollama create qwen2.5-coder-agent -f scripts/Modelfile.qwen-coder-14b
set -euo pipefail
DIR=/etc/systemd/system/ollama.service.d
if ! command -v systemctl >/dev/null || ! systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
  echo "No ollama systemd service. Start the server with:"
  echo "  OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_KEEP_ALIVE=30m ollama serve"
  exit 0
fi
mkdir -p "$DIR"
cat > "$DIR/tune.conf" <<'CONF'
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
CONF
systemctl daemon-reload
systemctl restart ollama
echo "Ollama tuned. Check with: python3 perf.py"
