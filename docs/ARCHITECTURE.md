# Architektur von saas_agent.py

## Komponenten
| Klasse | Aufgabe |
|---|---|
| `LLM` | OpenAI-kompatibler Client mit Retry und JSON-Reparatur; `LLM.mock` ersetzt ihn im Selftest |
| `Git` | Branch pro Versuch, `commit_all`, Fast-Forward-Merge, harter Reset, Churn-Statistik |
| `Backlog` | Aufgaben mit Priorität, Status, Versuchen; Dubletten-Filter per Textähnlichkeit |
| `Memory` | Langzeitgedächtnis (siehe unten) |
| `Agent` | Hauptschleife, Impact-Analyse, Implementierung, Debug, Review, Cleanup, Merge, Hygiene |

## Gedächtnis (`agent/memory/`)
| Datei | Inhalt | Zweck |
|---|---|---|
| `codemap.json` / `CODEMAP.md` | je Datei: Hash, Zweck, API, Daten, Seiteneffekte, Invarianten, Stolperfallen, `imports`, `imported_by` | Zusammenhänge vor einer Änderung |
| `features.json` | Feature → Dateien, Tests, Akzeptanz | Rückverfolgbarkeit |
| `decisions.jsonl` | ADRs (Kontext, Entscheidung, Alternativen, Folgen) | Warum wurde so entschieden |
| `lessons.jsonl` | Fehlversuche und Vermeidung (wird ab 60 Einträgen verdichtet) | gleiche Fehler vermeiden |
| `ARCHITECTURE.md` | verdichtete Gesamtsicht, alle `architecture_refresh_every` Merges neu | Orientierung |

Der Abhängigkeitsgraph wird **statisch** erzeugt (Python-AST, JS/TS-Imports, Jinja `extends/include`), nicht vom Modell geraten.
Das Modell liefert nur die Zusammenfassung pro Datei. Änderungen werden per Datei-Hash erkannt, nur geänderte Dateien werden neu zusammengefasst.

## Impact-Analyse (vor jeder Änderung)
1. Dateiauswahl: LLM wählt aus dem Index relevante Dateien, ergänzt um Schlüsselwort-Treffer.
2. Kontextpaket: Ziel, Architektur, passende ADRs und Lessons, verwandte Features, Codemap der Dateien **und aller Nachbarn**.
3. Plan (JSON): Verständnis, zu ändernde/neue Dateien, betroffene Aufrufer, Invarianten, Risiken, Schritte, Tests.
4. Plan-Kritik: prüft anhand des Graphen auf übersehene Aufrufer und ergänzt fehlende Dateien.

## Ablauf einer Aufgabe
`Branch agent/<id>-<versuch>` → Plan → Aider → `verify()` (setup bei geänderter requirements.txt, build, test, optional lint)
→ Debug-Runden → Review → Commit → Cleanup (Checkpoint-Reset bei Rot) → Gedächtnis-Snapshot → Codemap/Docs/ADR/Features/Changelog
→ Commit → `verify()` → Merge → `verify()` auf `main` (bei Rot Reset und Gedächtnis-Rollback).

## Schutzmechanismen
- Das Skript entscheidet über Ende und Weitermachen, nie das Modell.
- Zustand liegt außerhalb des Repos, deshalb zerstört `git reset --hard` ihn nicht. Lauf ist nach Absturz fortsetzbar.
- Zyklus: 3 Versuche pro Aufgabe → blockiert; 5 Fehlschläge in Folge → Stopp; `main` rot → Repair-Aufgabe mit höchster Priorität.
- Tests dürfen nicht entfernt werden, um grün zu werden (Prompt-Regel; das Review prüft es nur indirekt).

## Konfiguration (`agent/config.json`)
Überschreibt `DEFAULT_CONFIG`. Wichtige Schlüssel: `preset` (`fastapi`, `minimal`), `commands` (setup/build/test/lint),
`models`, `coder_model`, `limits.*`, `features.*`, `doc_language`. Für einen anderen Stack die `commands` setzen und das Scaffold anpassen (`SCAFFOLDS`).

## Bekannte Grenzen
- Nicht mit echtem Modell/Aider erprobt. Aider-Flags hängen von der Version ab (`--no-check-update` u. a.).
- Der Agent führt Build-/Test-Befehle des generierten Codes aus. Das ist nicht sandboxed.
- Die Review-Qualität eines 14B-Modells ist begrenzt; Tests sind das eigentliche Sicherheitsnetz.
