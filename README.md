# slop — autonomer lokaler SaaS-Agent

Du gibst **eine Idee** vor. Der Agent entwirft das Produkt, baut es in kleinen Schritten, erfindet danach selbst neue Features,
testet, debuggt, räumt auf und dokumentiert, bis du ihn stoppst oder ein Limit greift. Alles läuft lokal mit Ollama + Aider.

> Stand: Die Schleifenmechanik ist mit einem Stub-Modell getestet (`make selftest`, läuft auch in CI).
> Mit einem echten Modell wurde sie noch nicht gelaufen. Starte zuerst mit einem kleinen Ziel und beobachte `agent.log`.

## Voraussetzungen
- Linux/macOS/WSL, Python ≥ 3.10, git
- [Ollama](https://ollama.com/download) mit GPU-Treiber
- Empfohlen: 12 GB VRAM (z. B. RTX 3060) und 16 GB RAM → Modelle siehe [docs/MODELS.md](docs/MODELS.md)

## Schnellstart
```bash
git clone https://github.com/LeonBelford/slop && cd slop
make setup                      # venv, aider, Modell "qwen2.5-coder-agent" (14B, 12k Kontext)
make selftest                   # Mechanik prüfen, ohne Modell
make init WS=workspaces/demo IDEA="Rechnungs-Tool für Freelancer mit Mahnwesen"
make tmux WS=workspaces/demo    # läuft im Hintergrund, `tmux attach -t agent` zum Zuschauen
make status WS=workspaces/demo
make stop WS=workspaces/demo    # sauberer Stopp nach der aktuellen Aufgabe
```
Eigene Einstellungen: `CONFIG=config.example.json make init ...` (vorher kopieren und anpassen).

## Dateistruktur
```
slop/
├─ saas_agent.py            die komplette Schleife (nur Standardbibliothek)
├─ requirements.txt         aider-chat
├─ Makefile                 setup / selftest / init / run / status / stop / tmux
├─ .env.example             Endpunkt und Modelle (nach .env kopieren)
├─ config.example.json      Limits, Modelle, Feature-Schalter pro Workspace
├─ scripts/
│  ├─ setup.sh              venv + Aider + Ollama-Modell
│  ├─ run.sh                lädt .env + venv, ruft den Agenten auf
│  └─ Modelfile.qwen-coder-14b   Basismodell mit num_ctx 12288
├─ docs/                    ARCHITECTURE · MODELS · RUST · TROUBLESHOOTING
├─ examples/ideas.md        Beispiel-Ideen
├─ tests/test_selftest.py   Mechanik-Test
└─ .github/workflows/ci.yml CI
```
Pro Projekt entsteht unter `--workspace`:
```
repo/    das Produkt als Git-Repo, inkl. docs/ (ARCHITECTURE, CODEMAP, FEATURES, DECISIONS, modules/*), CHANGELOG, README
agent/   Zustand außerhalb des Repos: GOAL.md, backlog.json, state.json, metrics.jsonl, agent.log, STOP,
         memory/ (codemap.json, features.json, decisions.jsonl, lessons.jsonl, ARCHITECTURE.md), runs/<iter>-<task>/
```

## Was die Schleife pro Aufgabe tut
1. **Health:** Ist `main` grün? Sonst wird zuerst repariert.
2. **Backlog leer?** Produkt-Review und Ideation (rotierende Linsen, SaaS-Checkliste, Kritiker-Bewertung, Dubletten-Filter).
3. **Impact-Analyse vor jeder Änderung:** Gedächtnis (Codemap, Abhängigkeitsgraph in beide Richtungen, ADRs, Lessons) → Plan → Plan-Kritik.
4. **Umsetzen** mit Aider auf eigenem Branch, **Build + Tests**, bis zu 3 Debug-Runden. Features ohne Tests werden nicht akzeptiert.
5. **Review** (Score < 7 → Überarbeitung), **Cleanup-Pass** (wird bei rotem Build verworfen).
6. **Gedächtnis + Doku** aktualisieren, committen, **Build**, Merge nach `main`, **Build auf main** (bei Rot: Rollback).
7. Fehlversuche werden zu Lessons; nach 3 Versuchen gilt die Aufgabe als blockiert.

Gestoppt wird durch: `STOP`-Datei, `max_hours`, `max_iterations`, zu viele Fehler in Folge oder dauerhaft keine neuen Ideen.
Mehr in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Wichtige Hinweise
- Der Agent arbeitet nur im `repo/` des Workspaces und führt dort Build-/Test-Befehle aus (siehe `commands` in der Config). Starte ihn nicht in einem Ordner mit privaten Daten; für mehr Isolation in einen Container oder eine VM.
- Er veröffentlicht nichts und gibt kein Geld aus. Prüfe den Code, bevor du etwas deployst.
- Das Modell sieht nur ca. 12k Token Kontext, deshalb arbeitet der Agent mit kleinen Aufgaben und dem Gedächtnis.
- Erwarte bei 14B-Modellen kleine Produkte und gelegentliche Sackgassen. Ein stärkeres Modell für Planung/Review (`models.planner`/`reviewer`, per OpenAI-kompatibler API) hebt die Qualität am meisten.
