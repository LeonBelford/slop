# Rust und lokale KI

Es gibt kein "Rust-Modell". Rust ist eine Programmiersprache; sie steckt in **Inferenz-Engines**, die Modelle ausführen.
Die Modelle (Qwen, Llama, ...) bleiben dieselben. Falls du ein bestimmtes Projekt meintest, schick mir den Namen.

## Open-Source-Status (per GitHub-API am 2026-10-31 geprüft)
| Projekt | Zweck | Lizenz |
|---|---|---|
| [mistral.rs](https://github.com/EricLBuehler/mistral.rs) | Rust-Inferenz-Engine/Server | MIT |
| [candle](https://github.com/huggingface/candle) | Rust-ML-Framework von Hugging Face | Apache-2.0 |
| [burn](https://github.com/tracel-ai/burn) | Rust-Deep-Learning-Framework | Apache-2.0 |
| [Ollama](https://github.com/ollama/ollama) | Modellserver (Go, nutzt llama.cpp) | MIT |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Inferenz-Engine (C/C++) | MIT |

Die Lizenz der Engine ist unabhängig von der Lizenz der Modellgewichte. Diese stehen auf der Modellseite.

## Wäre das für dieses Projekt eine Idee?
- **Für den Agenten selbst: eher nein.** Das Tempo kommt von Modell, Quantisierung und GPU, nicht von der Sprache der Engine.
  Ollama/llama.cpp ist auf einer 3060 ausgereift und einfach zu betreiben.
- **Sinnvoll, wenn** du Rust magst, eine Engine in ein eigenes Rust-Programm einbetten willst oder etwas Bestimmtes
  (Modellformat, Feature) nur dort läuft.
- **Technisch möglich:** Der Agent spricht nur eine OpenAI-kompatible API (`LLM_API`), und Aider kann ebenfalls einen solchen
  Endpunkt nutzen. Laut Projektdoku bietet mistral.rs einen solchen Server; das habe ich nicht ausprobiert. Dann `LLM_API` und `models.*`
  anpassen und für Aider `CODER_MODEL=openai/<name>` plus `OPENAI_API_BASE` setzen.
- **Zum Lernen/Basteln:** candle oder burn, wenn du selbst Modelle in Rust laufen lassen willst. Für "ein Produkt autonom bauen" bringt das nichts.
