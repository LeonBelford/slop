# Troubleshooting

| Problem | Ursache / Lösung |
|---|---|
| `LLM nicht erreichbar` | Läuft `ollama serve`? `curl http://localhost:11434/api/tags` prüfen, `LLM_API` kontrollieren |
| Modell liefert kein JSON | kleineres/anderes Modell testen; Temperatur bleibt 0.2; Agent versucht 3×, dann schlägt die Aufgabe fehl |
| Aider startet nicht / unbekannte Option | `aider --version`, `pip install -U aider-chat`; Flags in `Agent.code` anpassen |
| Agent wirkt vergesslich | `num_ctx` zu klein: Modelfile prüfen, `ollama show qwen2.5-coder-agent` |
| Sehr langsam | `OLLAMA_NUM_PARALLEL=1`, kleineres Modell als `summarizer`, `cleanup_pass` ausschalten |
| Aufgabe `blocked` | `agent/runs/<iter>-<id>/` und `agent/memory/lessons.jsonl` lesen, Aufgabe in `backlog.json` auf `pending` setzen |
| `main` rot | Agent legt "Repair main" an; sonst in `workspace/repo` manuell `git log`, Build-Befehl aus der Config ausführen |
| Abbruch nach 5 Fehlern | Logs prüfen, Ursache beheben, `make run` erneut (Zustand bleibt erhalten) |
| Stoppen | `make stop WS=...` oder `tmux kill-session -t agent` |
