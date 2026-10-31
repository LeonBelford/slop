# slop — autonomer lokaler SaaS-Agent

Du gibst **eine Idee** vor. Der Agent entwirft das Produkt, baut es in kleinen Schritten, erfindet danach selbst neue Features,
testet, debuggt, räumt auf und dokumentiert, bis du ihn stoppst oder ein Limit greift. Alles läuft lokal mit Ollama + Aider.

> Die Schleifenmechanik ist mit einem Stub-Modell getestet (`make selftest`, auch in CI). Mit echtem Modell wurde sie noch nicht
> gelaufen. Starte mit einer kleinen Idee und beobachte `agent.log`.

## Voraussetzungen
Linux/macOS/WSL, Python ≥ 3.10, git, [Ollama](https://ollama.com/download). Empfohlen: 12 GB VRAM (RTX 3060) und 16 GB RAM.

## Benutzung (Kurzfassung)
Die ausführliche Schritt-für-Schritt-Anleitung steht in **[docs/ANLEITUNG.md](docs/ANLEITUNG.md)**.

**1. Einmalig installieren**
```bash
git clone -b main https://github.com/LeonBelford/slop && cd slop
bash scripts/quick-setup-popos.sh            # Pop!_OS/Ubuntu: alles installieren (--no-model, --nvidia)
make selftest                                # Mechanik ohne Modell prüfen
```

**2. Pro Idee starten**
```bash
make init IDEA="Rechnungs-Tool für Freelancer" NAME=rechnungen   # legt projects/rechnungen/ an (dauert einige Minuten)
make tmux P=rechnungen                                           # läuft im Hintergrund
```
Eigene Wünsche, Umsetzungshinweise und Umbauten schreibst du jederzeit (auch während des Laufs) in `projects/rechnungen/IDEAS.md`. Der Agent liest die Datei jede Iteration, plant bei Änderungen neu und gleicht sie alle 5 Merges mit dem Produkt ab.

**Selbst testen, ohne den Agenten abzuwürgen**
```bash
make try P=rechnungen                      # wartet bis die aktuelle Aufgabe fertig ist, baut den Stand, schreibt projects/rechnungen/TESTEN.md
make say P=rechnungen MSG="Der Button geht nicht"   # Feedback landet in IDEAS.md, der Agent plant neu
make tmux P=rechnungen                     # danach weiterarbeiten lassen
```
`make try` erkennt .NET (`dotnet publish` → `dist/`), Rust, Go, Node und FastAPI und nennt dir den Startbefehl.

**3. Bei Bedarf, einzeln**
```bash
tmux attach -t agent-rechnungen              # zuschauen; Strg+B, dann D löst dich wieder
make status P=rechnungen                     # Fortschritt
make stop P=rechnungen                       # sauberer Stopp nach der aktuellen Aufgabe
make list                                    # alle Projekte
```
`P` darf entfallen, wenn es nur ein Projekt gibt. Eigene Einstellungen: `config.example.json` kopieren, anpassen und mit
`CONFIG=meine.json make init ...` übergeben.

## Struktur
```
slop/
├─ saas_agent.py        die komplette Schleife (nur Standardbibliothek)
├─ Makefile, scripts/   quick-setup-popos.sh · setup.sh (nur venv+Modell) · run.sh · Modelfile
├─ .env.example, config.example.json
├─ docs/                ANLEITUNG.md (Benutzung) · ARCHITECTURE.md (Technik)
├─ tests/, .github/     Selftest und CI
└─ projects/            pro Idee ein Ordner (nicht im Git)
   └─ <name>/
      ├─ TESTEN.md      Startanleitung der letzten Testversion (von `make try`)
      ├─ IDEAS.md       deine Wünsche und Vorgaben (optional, vom Agenten laufend gelesen)
      ├─ repo/          das Produkt als eigenes Git-Repo inkl. docs/, CHANGELOG, README
      └─ agent/         Zustand und Gedächtnis dieses Projekts (außerhalb von repo/)
         ├─ GOAL.md, backlog.json, state.json, config.json, agent.log, metrics.jsonl
         ├─ memory/     codemap · features · decisions · lessons · ARCHITECTURE
         └─ runs/       Prompts, Pläne, Aider-Logs pro Aufgabe
```
Jedes Projekt hat damit seine eigene Codebase und sein eigenes Gedächtnis; nichts wird zwischen Projekten geteilt.

## Ablauf pro Aufgabe
1. `main` grün? Sonst zuerst reparieren.
2. Backlog leer → Produkt-Review und Ideation (rotierende Linsen, SaaS-Checkliste, Kritiker, Dubletten-Filter).
3. **Impact-Analyse** mit Gedächtnis (Codemap, Abhängigkeiten in beide Richtungen, ADRs, Lessons) → Plan → Plan-Kritik.
4. Umsetzung mit Aider auf eigenem Branch → **Build + Tests**, bis zu 3 Debug-Runden; Features ohne Tests werden abgelehnt.
5. Review (Score < 7 → Überarbeitung), Cleanup-Pass (bei rotem Build verworfen).
6. Gedächtnis und Doku aktualisieren, Commit, **Build**, Merge, **Build auf main** (bei Rot Rollback).
7. Fehlversuche werden zu Lessons; nach 3 Versuchen gilt die Aufgabe als blockiert.

Ende durch: `stop`, `max_hours`, `max_iterations`, 5 Fehlschläge in Folge oder dauerhaft keine neuen Ideen.

## Modelle (RTX 3060, 12 GB)
Richtwerte, nicht auf deiner Hardware gemessen; Tags und Lizenzen auf [ollama.com/library](https://ollama.com/library) prüfen.

| Modell | ca. Größe Q4 | Einsatz |
|---|---|---|
| `qwen2.5-coder:14b` | 9 GB | Standard für alle Rollen |
| `qwen2.5-coder:7b` | 5 GB | schneller, z. B. als `summarizer` |
| `qwen3:8b` / `qwen3:14b` | 5 / 9 GB | Planung und Review |
| `deepseek-coder-v2:16b` | 9 GB (MoE) | schnell durch wenige aktive Parameter |
| `gemma3:12b` | 8 GB | Ideen und Doku |
| `gpt-oss:20b`, 24B+ | 13 GB+ | nur mit CPU-Offload, bei 16 GB RAM nicht empfohlen |

`num_ctx` setzen (macht das Modelfile), sonst wird der Kontext abgeschnitten. Größter Qualitätshebel: `models.planner`/`reviewer`
auf ein stärkeres Modell über `LLM_API` legen; der Code kommt weiter vom lokalen Coder.

## Rust-Engines?
Es gibt kein "Rust-Modell", Rust steckt in Inferenz-Engines. Open Source (per GitHub geprüft): mistral.rs (MIT), candle und burn
(Apache-2.0). Für diesen Agenten lohnt der Wechsel von Ollama kaum, da Tempo vom Modell und der GPU kommt. Möglich ist er über
jeden OpenAI-kompatiblen Server (`LLM_API`, für Aider `CODER_MODEL=openai/<name>` + `OPENAI_API_BASE`), ungetestet.

## Probleme
Siehe [docs/ANLEITUNG.md](docs/ANLEITUNG.md#10-probleme).

## Sicherheit
Der Agent arbeitet nur in `projects/<name>/repo/`, führt dort aber Build- und Testbefehle ohne Sandbox aus. Nutze bei Bedarf
Container oder VM. Er veröffentlicht nichts und gibt kein Geld aus; prüfe den Code vor einem Deployment.
