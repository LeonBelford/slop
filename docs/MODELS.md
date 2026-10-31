# Modelle für RTX 3060 (12 GB VRAM) + 16 GB RAM

Das sind Richtwerte aus allgemeinem Wissen. Ich konnte die Ollama-Tags hier nicht online prüfen und habe nichts auf deiner
Hardware gemessen. Prüfe Namen mit `ollama search`/der [Ollama-Bibliothek](https://ollama.com/library) und Lizenzen auf der jeweiligen Modellseite.

| Modell (Ollama-Name) | Größe @ Q4 | passt in 12 GB? | Einsatz |
|---|---|---|---|
| `qwen2.5-coder:14b` | ca. 9 GB | ja, mit 8–12k Kontext | Standardwahl für alle Rollen |
| `qwen2.5-coder:7b` | ca. 5 GB | ja, viel Kontext | schneller, für Zusammenfassungen/Cleanup |
| `qwen3:8b` | ca. 5 GB | ja | Planung/Review, Denkmodus an |
| `qwen3:14b` | ca. 9 GB | ja, knapp | stärkere Planung, aber langsamer |
| `deepseek-coder-v2:16b` | ca. 9 GB (MoE) | ja | schnell durch wenige aktive Parameter |
| `gemma3:12b` | ca. 8 GB | ja | Generalist für Ideen/Doku |
| `gpt-oss:20b` | ca. 13 GB | nur mit CPU-Offload | langsam, bei 16 GB RAM eng |
| 24B+ Modelle (z. B. Devstral) | 14 GB+ | nein | nur mit starkem Offload, nicht empfohlen |

## Praktische Regeln
- Ein Modell für alle Rollen vermeidet Neuladen (`OLLAMA_MAX_LOADED_MODELS=1`).
- `num_ctx` setzen (Modelfile in `scripts/`), sonst schneidet Ollama den Kontext früh ab und der Agent wird dumm.
- Wenn zu langsam: `qwen2.5-coder:7b` als `summarizer`, 14B nur für Planung und Coder.
- Qualitätshebel Nr. 1: Planer/Reviewer auf ein größeres Modell legen (`LLM_API` auf einen Cloud- oder Server-Endpunkt).
  Der Code kommt weiterhin vom lokalen Coder.
