# Anleitung

Schritt für Schritt vom leeren Rechner bis zum laufenden Produkt. Hintergrund und Technik: [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Installation (einmalig, Pop!_OS/Ubuntu)
```bash
git clone https://github.com/LeonBelford/slop && cd slop
bash scripts/quick-setup-popos.sh
```
Das Skript installiert Pakete, Ollama, Aider und das Modell (ca. 9 GB, dauert je nach Leitung). Es fragt bei `sudo` nach deinem Passwort.
Meldet es "Keine NVIDIA-GPU erkannt", starte `bash scripts/quick-setup-popos.sh --nvidia`, starte den Rechner neu und wiederhole den Aufruf.

Prüfen:
```bash
make selftest                      # Mechanik ohne Modell: muss "SELFTEST OK" ausgeben
ollama list                        # qwen2.5-coder-agent muss auftauchen
echo "Sag hallo" | ollama run qwen2.5-coder-agent
```

## 2. Erstes Projekt starten
```bash
make init IDEA="Rechnungs-Tool für Freelancer mit Mahnwesen" NAME=rechnungen
```
- Legt `projects/rechnungen/` an, erzeugt ein Grundgerüst (FastAPI, Test, `requirements.txt`) und lässt das Modell ein Produktbrief
  (`agent/GOAL.md`) und die ersten MVP-Aufgaben (`agent/backlog.json`) schreiben. Das dauert einige Minuten.
- Ohne `NAME=` wird der Ordnername aus der Idee abgeleitet. `make list` zeigt alle Projekte.
- Je konkreter die Idee (Zielgruppe, Kernproblem, Muss-Features), desto besser das Ergebnis.

**Vor dem Start lesen:** `projects/rechnungen/agent/GOAL.md` und `backlog.json`. Falsche Richtung? `GOAL.md` und Backlog jetzt anpassen,
das ist die billigste Stelle zum Korrigieren.

## 3. Laufen lassen
```bash
make tmux P=rechnungen             # startet im Hintergrund (tmux-Sitzung agent-rechnungen)
tmux attach -t agent-rechnungen    # zuschauen; Strg+B, dann D löst dich wieder
make status P=rechnungen           # eine Zeile: Iteration, Merges, Backlog, Fehler in Folge
tail -f projects/rechnungen/agent/agent.log
```
Wenn es nur ein Projekt gibt, darfst du `P=...` weglassen. Ohne tmux: `make run P=rechnungen` (blockiert das Terminal).

Pro Aufgabe siehst du im Log: Plan, Aider-Arbeit, Build/Test, Debug-Runden, Review, Merge. `✔ T0003 gemerged` heißt: gebaut, getestet, in `main` des Produkts.

## 4. Was wo liegt
| Pfad (`projects/<name>/`) | Inhalt |
|---|---|
| `repo/` | das Produkt (eigenes Git-Repo): Code, Tests, `README.md`, `CHANGELOG.md`, `docs/` |
| `repo/docs/` | `ARCHITECTURE.md`, `CODEMAP.md`, `FEATURES.md`, `DECISIONS.md`, `modules/*.md` |
| `agent/GOAL.md` | Vision und Qualitätsmaßstab |
| `agent/backlog.json` | alle Aufgaben mit Status `pending`, `in_progress`, `done`, `blocked` |
| `agent/memory/` | Codemap, Features, Entscheidungen, Lessons (das Gedächtnis) |
| `agent/runs/<iter>-<id>/` | Plan, Kontext, Prompts und Aider-Logs je Aufgabe |
| `agent/metrics.jsonl` | Review-Scores, Dateien, LOC je Merge |

Fortschritt im Produkt ansehen: `git -C projects/rechnungen/repo log --oneline`.

## 4a. Das Produkt starten und ausprobieren
```bash
cd projects/rechnungen/repo
.venv/bin/uvicorn app.main:app --reload      # http://localhost:8000
```
`repo/.venv` legt der Agent beim ersten Build selbst an. Während der Agent läuft, besser eine Kopie nutzen
(`git clone projects/rechnungen/repo /tmp/rechnungen-test`), weil er Branches wechselt und Dateien ändert.

## 5. Stoppen und fortsetzen
```bash
make stop P=rechnungen             # beendet nach der aktuellen Aufgabe
make tmux P=rechnungen             # setzt am gespeicherten Zustand fort
```
Er endet auch selbst bei `max_hours`/`max_iterations` (Config), nach 5 Fehlschlägen in Folge oder wenn ihm dauerhaft nichts Sinnvolles
mehr einfällt. Bei Abbruch oder Absturz werden angefangene Aufgaben beim nächsten Start wieder aufgenommen.

## 6. Eingreifen
**Nur bei gestopptem Agenten**, sonst überschreibt er deine Änderungen (er hält den Backlog im Speicher).
- **Richtung ändern:** `agent/GOAL.md` bearbeiten.
- **Aufgabe hinzufügen:** in `agent/backlog.json` einen Eintrag anfügen:
  ```json
  {"id": "M001", "title": "CSV-Export der Rechnungen", "description": "Button auf der Rechnungsliste, der alle Rechnungen als CSV lädt.",
   "type": "feature", "priority": 9, "status": "pending", "attempts": 0, "acceptance": ["Test prüft CSV-Inhalt"], "notes": []}
  ```
  Höhere `priority` kommt zuerst, Typ `bug` immer vor allem anderen. Typen: `feature`, `bug`, `refactor`, `test`, `docs`.
- **Aufgabe streichen:** `status` auf `blocked` setzen oder den Eintrag löschen.
- **Blockierte Aufgabe erneut versuchen:** Ursache in `agent/runs/<iter>-<id>/` und `agent/memory/lessons.jsonl` lesen, `description` präzisieren,
  `status` auf `pending` und `attempts` auf `0` setzen.
- **Lessons korrigieren:** falsche Zeilen in `agent/memory/lessons.jsonl` löschen.

## 7. Einstellungen
Datei `projects/<name>/agent/config.json` (Vorlage: `config.example.json`, Wirkung ab dem nächsten Start). Wichtige Werte:
```json
{
  "doc_language": "German",
  "limits": {"max_hours": 8, "max_iterations": 200, "max_attempts_per_task": 3, "ideation_batch": 3, "min_review_score": 7},
  "features": {"cleanup_pass": false, "require_tests": true, "plan_critique": true},
  "commands": {"test": ".venv/bin/python -m pytest -q -x", "lint": ".venv/bin/python -m ruff check ."}
}
```
- Zu langsam: `cleanup_pass` aus, kleineres Modell für `models.summarizer` (z. B. `qwen2.5-coder:7b`).
- Qualität zu niedrig: stärkeres Modell für `models.planner`/`reviewer` über `LLM_API` in `.env` (z. B. ein OpenAI-kompatibler Server).
- Andere Modelle: erst `ollama pull <name>`, dann den Namen in `models.*` und `coder_model` (`ollama_chat/<name>`) eintragen.
  Danach `ollama create` mit passendem `num_ctx` nach dem Muster von `scripts/Modelfile.qwen-coder-14b`.
- Eigene Prüfungen (Lint, Typen) über `commands.lint`; schlägt ein Befehl fehl, gilt die Änderung als nicht fertig.

## 8. Mehrere Projekte
Jede Idee bekommt einen eigenen Ordner mit eigenem Repo und Gedächtnis. `make list` zeigt sie, `P=<name>` wählt eins.
Mehrere Agenten gleichzeitig auf einer 3060 sind nicht sinnvoll, weil sie sich die GPU teilen.

## 9. Aufräumen
Projekt löschen: `rm -rf projects/<name>`. Alles andere ist im Repo-Ordner von `slop` und kann bleiben.

## 10. Probleme
| Symptom | Lösung |
|---|---|
| `LLM nicht erreichbar` | `systemctl status ollama`, `curl localhost:11434/api/tags`; sonst `ollama serve` |
| Modell liefert kein JSON | tritt gelegentlich auf (der Agent versucht es 3×); häufig → anderes Modell in `models.*` |
| Agent wirkt vergesslich | `num_ctx` zu klein: `ollama show qwen2.5-coder-agent` prüfen |
| Aider-Option unbekannt | `.venv/bin/pip install -U aider-chat`, Flags in `Agent.code` (`saas_agent.py`) anpassen |
| Aufgabe `blocked` | siehe Abschnitt 6 |
| "main ist rot" im Log | Agent legt selbst eine Reparatur-Aufgabe an; sonst Build-Befehl aus der Config im `repo/` ausführen |
| Abbruch nach 5 Fehlern | `agent.log` und `agent/runs/` lesen, Ursache beheben, neu starten |
| Zu langsam | siehe Abschnitt 7 |

## Sicherheit
Der Agent führt die Build- und Testbefehle des von ihm erzeugten Codes ohne Sandbox aus. Starte ihn nur auf einem Rechner oder in
einer VM/einem Container, auf dem nichts Wichtiges liegt, und prüfe den Code, bevor du ihn veröffentlichst.
