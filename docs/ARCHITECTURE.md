# Technische Details

Ergänzung zur [README](../README.md). Hier steht nur, was dort nicht steht.

## Komponenten in `saas_agent.py`
| Klasse | Aufgabe |
|---|---|
| `LLM` | OpenAI-kompatibler Client mit Retry und JSON-Reparatur (`LLM.mock` ersetzt ihn im Selftest) |
| `Git` | Branch pro Versuch, Commit, Fast-Forward-Merge, harter Reset, Churn-Statistik |
| `Backlog` | Aufgaben mit Priorität/Status; Dubletten-Filter per Textähnlichkeit |
| `Memory` | Gedächtnis (unten) |
| `Agent` | Hauptschleife, Impact-Analyse, Implementierung, Debug, Review, Cleanup, Merge, Hygiene |

## Gedächtnis pro Projekt (`projects/<name>/agent/memory/`)
Es entsteht automatisch beim `init` und gehört immer genau einem Projekt.

| Datei | Inhalt |
|---|---|
| `codemap.json` (+ `CODEMAP.md`) | je Datei: Hash, Zweck, API, Daten, Seiteneffekte, Invarianten, Stolperfallen, `imports`, `imported_by` |
| `features.json` | Feature → Dateien, Tests, Akzeptanzkriterien |
| `decisions.jsonl` | ADRs: Kontext, Entscheidung, Alternativen, Folgen |
| `lessons.jsonl` | Fehlversuche und wie man sie vermeidet (ab 60 Einträgen verdichtet) |
| `ARCHITECTURE.md` | verdichtete Gesamtsicht, alle `architecture_refresh_every` Merges neu erzeugt |

- Der Abhängigkeitsgraph wird **statisch** gebaut (Python-AST, JS/TS-Imports, Jinja `extends/include`). Das Modell fasst nur jede Datei zusammen.
- Neu zusammengefasst werden nur Dateien mit geändertem Hash.
- Lesbare Kopien landen im Produkt-Repo unter `docs/` (ARCHITECTURE, CODEMAP, FEATURES, DECISIONS, `modules/*`).

## Impact-Analyse vor jeder Änderung
1. LLM wählt aus dem Dateiindex relevante Dateien, ergänzt um Schlüsselwort-Treffer.
2. Kontextpaket: Ziel, Architektur, passende ADRs/Lessons, verwandte Features, Codemap der Dateien **und aller Nachbarn** (Aufrufer und Abhängigkeiten).
3. Plan als JSON: Verständnis, zu ändernde/neue Dateien, betroffene Aufrufer, Invarianten, Risiken, Schritte, Tests.
4. Plan-Kritik prüft per Graph auf übersehene Aufrufer und ergänzt Dateien.

## Robustheit
- Das Skript entscheidet über Weitermachen und Ende, nie das Modell.
- Der Zustand liegt außerhalb des Repos; `git reset --hard` zerstört ihn nicht, ein Lauf ist nach Absturz fortsetzbar.
- Vor dem Doku-Update wird das Gedächtnis gesichert und bei Fehlschlag zurückgerollt.
- Tests dürfen laut Prompt nicht entfernt werden, um grün zu werden; das Review prüft das nur indirekt.

## Config (`projects/<name>/agent/config.json`)
Überschreibt `DEFAULT_CONFIG` in `saas_agent.py`: `preset` (`fastapi`, `minimal`), `commands` (setup/build/test/lint), `models`,
`coder_model`, `limits.*`, `features.*`, `doc_language`. Für einen anderen Stack `commands` setzen und `SCAFFOLDS` anpassen.

## Grenzen
- Nicht mit echtem Modell/Aider erprobt; Aider-Flags hängen von der Version ab (`Agent.code`).
- Build-/Test-Befehle des generierten Codes laufen ohne Sandbox.
