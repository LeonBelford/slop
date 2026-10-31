#!/usr/bin/env python3
"""Autonomer SaaS-Agent: erfindet Features, baut, testet, debuggt, räumt auf und dokumentiert.

Nur die Startidee kommt von dir. Danach läuft eine äußere Schleife, die selbst entscheidet
(das Modell entscheidet NICHT über das Ende):

  health -> backlog leer? -> Produkt-Review / Ideation (+Kritiker) -> Aufgabe wählen
  -> Impact-Analyse mit Gedächtnis (Codemap, Abhängigkeitsgraph, ADRs, Lessons)
  -> Plan-Kritik -> Implementierung (Aider) -> Build+Tests -> Debug-Runden
  -> Review -> Cleanup-Pass (nur mit grünem Build) -> Gedächtnis+Doku aktualisieren
  -> Commit -> Merge nach main -> Build auf main (bei Rot: Rollback)

Layout (pro Idee ein eigener Ordner unter projects/<name>/):
  repo/     Git-Repo des Produkts (inkl. docs/)
  agent/    Zustand dieses Projekts AUSSERHALB des Repos, damit `git reset` ihn nie zerstört:
            config.json state.json backlog.json GOAL.md memory/ runs/ agent.log metrics.jsonl STOP

Aufrufe:
  python saas_agent.py init   --idea "Rechnungs-Tool für Freelancer"   # legt projects/<name>/ an
  python saas_agent.py run    --project <name>
  python saas_agent.py status --project <name>
  python saas_agent.py list
  python saas_agent.py stop   --project <name>     # sauberer Stopp nach der aktuellen Aufgabe
  python saas_agent.py selftest                    # Mechanik-Test mit Stub-LLM, ohne Ollama

Nur Standardbibliothek. Benötigt: git, aider (pip install aider-chat), einen OpenAI-kompatiblen
LLM-Endpunkt (Standard Ollama). Details: README.md und docs/.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------- Konfiguration

DEFAULT_CONFIG = {
    "preset": "fastapi",
    "doc_language": "English",
    "llm_api": os.getenv("LLM_API", "http://localhost:11434/v1/chat/completions"),
    "api_key": os.getenv("LLM_API_KEY", "ollama"),
    "models": {  # Rollen -> Modellname am LLM-Endpunkt (Planung ruhig stärker wählen)
        "planner": os.getenv("PLANNER_MODEL", "qwen2.5-coder:14b"),
        "reviewer": os.getenv("REVIEWER_MODEL", "qwen2.5-coder:14b"),
        "summarizer": os.getenv("SUMMARIZER_MODEL", "qwen2.5-coder:14b"),
    },
    "coder_model": os.getenv("CODER_MODEL", "ollama_chat/qwen2.5-coder:14b"),  # Aider-Modellstring
    "limits": {
        "max_iterations": 500,
        "max_hours": 24,
        "max_attempts_per_task": 3,
        "max_debug_rounds": 3,
        "max_consecutive_failures": 5,
        "coder_timeout": 1500,
        "cmd_timeout": 900,
        "llm_timeout": 900,
        "ideation_batch": 4,
        "ideation_min_score": 6,
        "refactor_every": 4,
        "coverage_every": 5,
        "product_review_every": 6,
        "architecture_refresh_every": 3,
        "health_every": 5,
        "ideas_review_every": 5,
        "min_review_score": 7,
        "max_files_per_task": 8,
    },
    "context": {"max_chars": 26000, "file_clip": 6000},
    "features": {"cleanup_pass": True, "require_tests": True, "plan_critique": True},
    "commands": {},  # wird vom Preset gefüllt, in config.json überschreibbar
    "source_exts": [".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".toml", ".yml", ".yaml"],
    "ignore_dirs": [".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build", "docs"],
}

PRESETS = {
    "fastapi": {
        "commands": {
            "setup": "python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt",
            "build": ".venv/bin/python -m compileall -q app tests && .venv/bin/python -c 'import app.main'",
            "test": ".venv/bin/python -m pytest -q -x",
            "lint": "",
        },
        "stack": "Python 3, FastAPI, Jinja2 templates, SQLite via sqlite3/SQLAlchemy, pytest",
    },
    "minimal": {  # für den Selftest, keine Abhängigkeiten
        "commands": {
            "setup": "",
            "build": "python3 -m compileall -q app tests",
            "test": "python3 -m unittest discover -s tests -q",
            "lint": "",
        },
        "stack": "Python 3 standard library only, unittest",
    },
}

SAAS_CHECKLIST = [
    "authentication & sessions", "multi-tenancy / workspaces & roles", "billing/plans/usage limits (stub ok)",
    "onboarding & activation flow", "settings & profile", "audit log", "notifications / email (stub ok)",
    "public API + API keys", "input validation & error handling", "structured logging & health endpoint",
    "rate limiting & security headers", "DB migrations / schema versioning", "data export/import",
    "analytics dashboard", "admin area", "accessibility & UX polish", "performance & caching",
    "test coverage & CI config", "Dockerfile & deployment docs", "documentation",
]
LENSES = [
    "activation & onboarding", "retention & habit loops", "monetization & plans", "collaboration & teams",
    "insight & analytics", "integrations & API", "security & admin", "reliability & performance",
    "UX polish & accessibility", "automation & time saving",
]

# --------------------------------------------------------------------------- Helfer


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n // 2] + "\n...[gekürzt]...\n" + s[-n // 2:]


def tail(s: str, n: int) -> str:
    return s if len(s) <= n else "...[gekürzt]...\n" + s[-n:]


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def parse_json(text: str):
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return dec.raw_decode(text[i:])[0]
            except ValueError:
                continue
    raise ValueError("kein JSON gefunden")


def sh(cmd, cwd: Path, timeout: int = 900, shell: bool = False, env: dict | None = None):
    """Führt einen Befehl aus. Rückgabe: (returncode, stdout+stderr)."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), shell=shell, capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT nach {timeout}s: {cmd}"
    except FileNotFoundError as e:
        return 127, f"Befehl nicht gefunden: {e}"


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z_]{4,}", text.lower())}


IDEAS_TEMPLATE = """# Ideen und Vorgaben für dieses Projekt

<!-- Der Agent liest diese Datei in jeder Iteration. Wenn du etwas änderst, plant er neu: Er ergänzt Aufgaben, ändert
Prioritäten, streicht überholte offene Aufgaben und baut bestehenden Code bei Bedarf um. Zusätzlich gleicht er alle paar
Merges ab, ob das Produkt zu deinen Vorgaben passt. Alles hier ist verbindlich, solange es nicht gelöscht wird.
Kommentare wie dieser und leere Abschnitte werden ignoriert. Jederzeit editierbar, auch während der Agent läuft. -->

Startidee: {idea}

## Wünsche und neue Features
<!-- z. B. - Rechnungen als PDF exportieren, mit eigenem Logo -->

## Wie es umgesetzt werden soll
<!-- z. B. - Server-seitig rendern mit Jinja, kein JavaScript-Framework
     - Jede Route bekommt einen Test; Geldbeträge immer als Integer in Cent -->

## Umbau-Wünsche
<!-- z. B. - Datenbankzugriff in ein eigenes Modul auslagern
     - Auth von Session-Cookies auf Tokens umstellen -->

## Nicht tun
<!-- z. B. - keine externen Bezahldienste anbinden -->
"""


def read_ideas(path: Path) -> str:
    """Liest IDEAS.md ohne Kommentare. Leer, wenn nur Überschriften und Startidee drinstehen."""
    try:
        text = re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.S)
    except OSError:
        return ""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    if not [l for l in lines if not l.lstrip().startswith("#") and not l.startswith("Startidee:")]:
        return ""
    return "\n".join(lines)


# --------------------------------------------------------------------------- LLM


class LLM:
    """Dünner OpenAI-kompatibler Client. `mock(tag, role, system, user) -> str` ersetzt ihn im Selftest."""

    mock = None

    def __init__(self, cfg: dict, log):
        self.cfg, self.log = cfg, log

    def chat(self, tag: str, role: str, system: str, user: str, temperature: float = 0.2) -> str:
        if LLM.mock:
            return LLM.mock(tag, role, system, user)
        body = json.dumps({
            "model": self.cfg["models"][role],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
        }).encode()
        last = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    self.cfg["llm_api"], data=body,
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.cfg["api_key"]})
                with urllib.request.urlopen(req, timeout=self.cfg["limits"]["llm_timeout"]) as r:
                    return json.loads(r.read())["choices"][0]["message"]["content"]
            except Exception as e:  # Netzwerk/Modell kurz weg: warten und neu versuchen
                last = e
                self.log(f"LLM-Fehler ({tag}), Versuch {attempt + 1}: {e}")
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"LLM nicht erreichbar: {last}")

    def json(self, tag: str, role: str, system: str, user: str):
        system += "\nAntworte AUSSCHLIESSLICH mit gültigem JSON, ohne Erklärtext."
        prompt = user
        for _ in range(3):
            text = self.chat(tag, role, system, prompt, temperature=0.2)
            try:
                return parse_json(text)
            except ValueError:
                prompt = user + "\n\nDeine letzte Antwort war kein gültiges JSON. Nur JSON ausgeben."
        raise RuntimeError(f"Modell liefert kein JSON ({tag})")


# --------------------------------------------------------------------------- Git


class Git:
    def __init__(self, repo: Path):
        self.repo = repo
        self.id = ["-c", "user.name=saas-agent", "-c", "user.email=agent@local"]

    def run(self, *args, timeout: int = 120):
        return sh(["git", *self.id, *args], self.repo, timeout)

    def out(self, *args) -> str:
        return self.run(*args)[1].strip()

    def ensure(self) -> None:
        if not (self.repo / ".git").exists():
            self.repo.mkdir(parents=True, exist_ok=True)
            self.run("init", "-q")
            self.run("symbolic-ref", "HEAD", "refs/heads/main")

    def sha(self) -> str:
        return self.out("rev-parse", "HEAD")

    def commit_all(self, msg: str) -> bool:
        self.run("add", "-A")
        if not self.out("status", "--porcelain"):
            return False
        return self.run("commit", "-q", "-m", msg)[0] == 0

    def hard_reset(self, ref: str = "HEAD") -> None:
        self.run("reset", "--hard", ref)
        self.run("clean", "-fdq")

    def branch_from_main(self, branch: str) -> None:
        self.hard_reset()
        self.run("checkout", "-q", "main")
        self.run("checkout", "-q", "-B", branch, "main")

    def changed_files(self, base: str = "main") -> list[str]:
        out = self.out("diff", "--name-only", base)
        untracked = self.out("ls-files", "--others", "--exclude-standard")
        return sorted({f for f in (out + "\n" + untracked).splitlines() if f.strip()})

    def diff(self, base: str = "main") -> str:
        self.run("add", "-A")
        return self.run("diff", "--cached", base)[1]

    def merge_ff(self, branch: str) -> bool:
        self.run("checkout", "-q", "main")
        return self.run("merge", "--ff-only", "-q", branch)[0] == 0

    def churn(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for line in self.out("log", "--name-only", "--pretty=format:").splitlines():
            if line.strip():
                c[line.strip()] = c.get(line.strip(), 0) + 1
        return c


# --------------------------------------------------------------------------- Backlog


class Backlog:
    def __init__(self, path: Path):
        self.path = path
        self.items: list[dict] = read_json(path, [])

    def save(self) -> None:
        write_json(self.path, self.items)

    def titles(self) -> list[str]:
        return [i["title"] for i in self.items]

    def is_dup(self, title: str, extra: list[str] | None = None) -> bool:
        t = title.lower().strip()
        for other in self.titles() + (extra or []):
            if difflib.SequenceMatcher(None, t, other.lower().strip()).ratio() > 0.78:
                return True
        return False

    def add(self, title, description, type_="feature", priority=5.0, acceptance=None, rationale="",
            source="ideation", extra_titles=None):
        if self.is_dup(title, extra_titles):
            return None
        item = {"id": f"T{len(self.items) + 1:04d}", "title": title, "description": description,
                "type": type_, "priority": float(priority), "status": "pending", "attempts": 0,
                "acceptance": acceptance or [], "rationale": rationale, "source": source,
                "created": now(), "notes": []}
        self.items.append(item)
        self.save()
        return item

    def pending(self) -> list[dict]:
        return [i for i in self.items if i["status"] == "pending"]

    def next(self):
        p = self.pending()
        if not p:
            return None
        return max(p, key=lambda i: i["priority"] + (1000 if i["type"] == "bug" else 0))

    def set(self, item: dict, **kw) -> None:
        item.update(kw)
        self.save()


# --------------------------------------------------------------------------- Gedächtnis


class Memory:
    """Langzeitgedächtnis des Agenten.

    codemap.json   pro Datei: Hash, Zweck, API, Daten, Seiteneffekte, Invarianten, Stolperfallen,
                   imports / imported_by (statisch per AST/Regex) -> verlässlicher Abhängigkeitsgraph
    features.json  Feature -> Dateien/Tests/Akzeptanz (Rückverfolgbarkeit)
    decisions.jsonl ADRs: warum wurde etwas so entschieden
    lessons.jsonl  was ist schiefgegangen, was vermeiden
    ARCHITECTURE.md verdichtete Gesamtsicht, regelmäßig neu erzeugt
    """

    def __init__(self, agent_dir: Path, repo: Path, cfg: dict, llm: LLM, log):
        self.dir, self.repo, self.cfg, self.llm, self.log = agent_dir / "memory", repo, cfg, llm, log
        self.dir.mkdir(parents=True, exist_ok=True)
        self.codemap: dict = read_json(self.dir / "codemap.json", {})
        self.features: dict = read_json(self.dir / "features.json", {})

    # ---- Dateien & statischer Graph
    def source_files(self) -> list[str]:
        exts, ign = set(self.cfg["source_exts"]), set(self.cfg["ignore_dirs"])
        out = []
        for p in self.repo.rglob("*"):
            rel = p.relative_to(self.repo)
            if p.is_file() and p.suffix in exts and not (set(rel.parts[:-1]) & ign) and p.stat().st_size < 400_000:
                out.append(rel.as_posix())
        return sorted(out)

    def static_imports(self, rel: str, text: str, known: set[str]) -> list[str]:
        found: set[str] = set()
        base = Path(rel).parent
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                return []
            for n in ast.walk(tree):
                mods = []
                if isinstance(n, ast.Import):
                    mods = [(a.name, 0, []) for a in n.names]
                elif isinstance(n, ast.ImportFrom):
                    mods = [(n.module or "", n.level, [a.name for a in n.names])]
                for mod, level, names in mods:
                    root = base
                    for _ in range(max(level - 1, 0)):
                        root = root.parent
                    start = root if level else Path(".")
                    stem = (start / mod.replace(".", "/")).as_posix().lstrip("./") if mod else start.as_posix()
                    for cand in [stem + ".py", stem + "/__init__.py"] + [f"{stem}/{x}.py" for x in names]:
                        cand = cand.lstrip("/")
                        if cand in known:
                            found.add(cand)
        elif rel.endswith((".js", ".ts", ".tsx", ".jsx")):
            for m in re.finditer(r"""(?:from\s+|require\(\s*|import\s+)['"](\.[^'"]+)['"]""", text):
                tgt = (base / m.group(1)).as_posix()
                tgt = os.path.normpath(tgt).replace("\\", "/")
                for ext in ("", ".js", ".ts", ".tsx", ".jsx", "/index.js", "/index.ts"):
                    if tgt + ext in known:
                        found.add(tgt + ext)
        elif rel.endswith(".html"):
            for m in re.finditer(r"""\{%\s*(?:extends|include|import)\s+['"]([^'"]+)['"]""", text):
                for k in known:
                    if k.endswith("/" + m.group(1)) or k == m.group(1):
                        found.add(k)
        found.discard(rel)
        return sorted(found)

    # ---- Synchronisation mit dem Code
    def sync(self, force_docs_for: list[str] | None = None) -> list[str]:
        files = self.source_files()
        known = set(files)
        changed = []
        for rel in files:
            text = (self.repo / rel).read_text("utf-8", "replace")
            h = sha(text)
            entry = self.codemap.get(rel)
            if not entry or entry.get("hash") != h:
                info = self.summarize(rel, text)
                self.codemap[rel] = {"hash": h, "loc": text.count("\n") + 1, **info}
                changed.append(rel)
            self.codemap[rel]["imports"] = self.static_imports(rel, text, known)
        for gone in set(self.codemap) - known:
            del self.codemap[gone]
        for rel in self.codemap:
            self.codemap[rel]["imported_by"] = sorted(o for o, e in self.codemap.items() if rel in e.get("imports", []))
        write_json(self.dir / "codemap.json", self.codemap)
        write_text(self.dir / "CODEMAP.md", self.render_codemap())
        write_text(self.repo / "docs" / "CODEMAP.md", self.render_codemap())
        for rel in sorted(set(changed) | set(force_docs_for or [])):
            if rel in self.codemap:
                self.write_module_doc(rel)
        return changed

    def summarize(self, rel: str, text: str) -> dict:
        schema = ('{"purpose": "1-2 Sätze", "public_api": ["Signatur - Wirkung"], "data_and_state": ["..."], '
                  '"side_effects": ["IO/DB/Netz"], "invariants": ["was immer gelten muss"], "gotchas": ["Stolperfallen"]}')
        try:
            d = self.llm.json("summarize", "summarizer",
                              "Du dokumentierst Quellcode für einen KI-Maintainer. Präzise, faktenbasiert, nichts erfinden.",
                              f"Datei: {rel}\nSchema: {schema}\n\n```\n{clip(text, 8000)}\n```")
            return {k: d.get(k, [] if k != "purpose" else "") for k in
                    ("purpose", "public_api", "data_and_state", "side_effects", "invariants", "gotchas")}
        except Exception as e:
            self.log(f"Zusammenfassung fehlgeschlagen für {rel}: {e}")
            return {"purpose": "(Zusammenfassung fehlgeschlagen)", "public_api": [], "data_and_state": [],
                    "side_effects": [], "invariants": [], "gotchas": []}

    def render_entry(self, rel: str, e: dict) -> str:
        def lst(k):
            return "".join(f"  - {x}\n" for x in e.get(k, [])) or "  - (keine)\n"
        return (f"### `{rel}` ({e.get('loc', '?')} LOC)\n{e.get('purpose', '')}\n"
                f"- API:\n{lst('public_api')}- Daten/State:\n{lst('data_and_state')}"
                f"- Seiteneffekte:\n{lst('side_effects')}- Invarianten:\n{lst('invariants')}"
                f"- Stolperfallen:\n{lst('gotchas')}"
                f"- importiert: {', '.join(e.get('imports', [])) or '-'}\n"
                f"- wird benutzt von: {', '.join(e.get('imported_by', [])) or '-'}\n")

    def render_codemap(self) -> str:
        return "# Codemap (automatisch erzeugt)\n\n" + "\n".join(self.render_entry(r, e) for r, e in sorted(self.codemap.items()))

    def write_module_doc(self, rel: str) -> None:
        e = self.codemap[rel]
        text = (self.repo / rel).read_text("utf-8", "replace")
        lang = self.cfg["doc_language"]
        try:
            body = self.llm.chat(
                "module_doc", "summarizer",
                f"Du schreibst detaillierte Modul-Dokumentation in {lang} als Markdown für Entwickler. Nur Fakten aus dem Code.",
                f"Datei {rel}\nBekannte Metadaten:\n{self.render_entry(rel, e)}\n\nCode:\n```\n{clip(text, 9000)}\n```\n"
                "Struktur: Überblick, Verantwortung, Funktionen/Klassen mit Parametern und Rückgaben, Datenfluss, "
                "Fehlerfälle, Abhängigkeiten, Beispiel.")
        except Exception as ex:
            body = f"(Dokumentation fehlgeschlagen: {ex})"
        write_text(self.repo / "docs" / "modules" / (rel.replace("/", "__") + ".md"),
                   f"# `{rel}`\n\n{self.render_entry(rel, e)}\n---\n\n{body}\n")

    # ---- Abruf vor Änderungen
    def select_candidates(self, item: dict) -> list[str]:
        if not self.codemap:
            return []
        index = "\n".join(f"{r} — {e.get('purpose', '')[:110]}" for r, e in sorted(self.codemap.items()))
        picked: list[str] = []
        try:
            d = self.llm.json("select_files", "planner", "Du wählst Dateien aus, die für eine Änderung relevant sind.",
                              f"Aufgabe: {item['title']}\n{item['description']}\n\nDateiindex:\n{clip(index, 9000)}\n\n"
                              'Schema: {"files": ["pfad", ...]} (max 10, auch Tests und Dateien, die betroffen sein könnten)')
            picked = [f for f in d.get("files", []) if f in self.codemap]
        except Exception as e:
            self.log(f"Dateiauswahl per LLM fehlgeschlagen: {e}")
        kw = words(item["title"] + " " + item["description"])
        scored = sorted(self.codemap, key=lambda r: -len(kw & words(r + " " + self.codemap[r].get("purpose", ""))))
        for r in scored[:4]:
            if kw & words(r + " " + self.codemap[r].get("purpose", "")) and r not in picked:
                picked.append(r)
        return picked[:12]

    def neighbors(self, files: list[str]) -> list[str]:
        out: set[str] = set()
        for f in files:
            e = self.codemap.get(f, {})
            out |= set(e.get("imports", [])) | set(e.get("imported_by", []))
        return sorted(out - set(files))

    def _rank(self, path: Path, query: str, limit: int, text_of) -> list[dict]:
        rows = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()] if path.exists() else []
        q = words(query)
        rows.sort(key=lambda r: -len(q & words(text_of(r))))
        return [r for r in rows[:limit] if q & words(text_of(r))]

    def context_pack(self, item: dict, files: list[str]) -> str:
        cfg_c = self.cfg["context"]
        query = item["title"] + " " + item["description"] + " " + " ".join(files)
        parts = []
        goal = self.dir.parent / "GOAL.md"
        if goal.exists():
            parts.append("## Produktziel\n" + clip(goal.read_text("utf-8"), 2500))
        wishes = read_ideas(self.dir.parent.parent / "IDEAS.md")
        if wishes:
            parts.append("## Vorgaben des Nutzers (verbindlich, auch Umsetzungshinweise und 'Nicht tun')\n" + clip(wishes, 3000))
        arch = self.dir / "ARCHITECTURE.md"
        if arch.exists():
            parts.append("## Architektur\n" + clip(arch.read_text("utf-8"), 5000))
        dec = self._rank(self.dir / "decisions.jsonl", query, 5, lambda r: json.dumps(r))
        if dec:
            parts.append("## Relevante Entscheidungen\n" + "\n".join(
                f"- {d['title']}: {d['decision']} (Folgen: {d.get('consequences', '')})" for d in dec))
        les = self._rank(self.dir / "lessons.jsonl", query, 6, lambda r: json.dumps(r))
        if les:
            parts.append("## Lessons Learned (nicht wiederholen)\n" + "\n".join(f"- {l['lesson']}" for l in les))
        feats = [f for f in self.features.values() if set(f.get("files", [])) & set(files) or
                 words(f["title"]) & words(item["title"])][:6]
        if feats:
            parts.append("## Verwandte Features\n" + "\n".join(
                f"- {f['title']} → {', '.join(f.get('files', []))}" for f in feats))
        ctx_files = files + self.neighbors(files)
        parts.append("## Codemap der betroffenen Dateien und Nachbarn\n" + "\n".join(
            self.render_entry(f, self.codemap[f]) for f in ctx_files if f in self.codemap))
        return clip("\n\n".join(parts), cfg_c["max_chars"])

    # ---- Schreiben
    def add_lesson(self, lesson: str, files: list[str], item_id: str) -> None:
        with open(self.dir / "lessons.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": now(), "item": item_id, "files": files, "lesson": lesson},
                               ensure_ascii=False) + "\n")
        self.consolidate_lessons()

    def consolidate_lessons(self) -> None:
        p = self.dir / "lessons.jsonl"
        rows = [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()]
        if len(rows) <= 60:
            return
        try:
            d = self.llm.json("consolidate", "summarizer", "Du verdichtest Lessons Learned ohne Information zu verlieren.",
                              "Fasse zu max. 30 prägnanten Lessons zusammen. Schema: "
                              '{"lessons": [{"lesson": "...", "files": ["..."]}]}\n' + json.dumps(rows)[:14000])
            new = [{"time": now(), "item": "consolidated", "files": x.get("files", []), "lesson": x["lesson"]}
                   for x in d["lessons"]]
            write_text(p, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in new))
        except Exception as e:
            self.log(f"Lesson-Konsolidierung fehlgeschlagen: {e}")

    def add_decision(self, item: dict, plan: dict, review: dict, diff: str) -> None:
        try:
            d = self.llm.json("decision", "summarizer", "Du schreibst ein ADR (Architecture Decision Record).",
                              f"Aufgabe: {item['title']}\nPlan: {json.dumps(plan)[:3000]}\nReview: {review.get('summary', '')}\n"
                              f"Diff:\n{clip(diff, 5000)}\nSchema: "
                              '{"title": "", "context": "", "decision": "", "alternatives": "", "consequences": ""}')
        except Exception:
            d = {"title": item["title"], "context": item["description"], "decision": review.get("summary", ""),
                 "alternatives": "", "consequences": ""}
        d["item"], d["time"] = item["id"], now()
        with open(self.dir / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        rows = [json.loads(l) for l in (self.dir / "decisions.jsonl").read_text("utf-8").splitlines() if l.strip()]
        write_text(self.repo / "docs" / "DECISIONS.md", "# Architekturentscheidungen\n\n" + "\n".join(
            f"## {r['item']}: {r['title']}\n- Kontext: {r.get('context', '')}\n- Entscheidung: {r.get('decision', '')}\n"
            f"- Alternativen: {r.get('alternatives', '')}\n- Folgen: {r.get('consequences', '')}\n" for r in rows))

    def record_feature(self, item: dict, files: list[str], review: dict) -> None:
        if item["type"] not in ("feature", "bug"):
            return
        tests = [f for f in files if "test" in f.lower()]
        self.features[item["id"]] = {"title": item["title"], "type": item["type"], "files": files, "tests": tests,
                                     "acceptance": item.get("acceptance", []), "summary": review.get("summary", ""),
                                     "done": now()}
        write_json(self.dir / "features.json", self.features)
        lines = ["# Features & Fixes\n"]
        for fid, f in self.features.items():
            lines.append(f"## {fid}: {f['title']} ({f['type']})\n{f['summary']}\n- Dateien: {', '.join(f['files'])}\n"
                         f"- Tests: {', '.join(f['tests']) or '-'}\n- Akzeptanz: {'; '.join(f['acceptance']) or '-'}\n")
        write_text(self.repo / "docs" / "FEATURES.md", "\n".join(lines))

    def changelog(self, item: dict, review: dict) -> None:
        p = self.repo / "CHANGELOG.md"
        old = p.read_text("utf-8") if p.exists() else "# Changelog\n"
        entry = f"\n## {now()} — {item['id']} {item['title']} ({item['type']})\n{review.get('summary', '')}\n"
        write_text(p, old + entry)

    def refresh_architecture(self) -> None:
        arch = self.dir / "ARCHITECTURE.md"
        prev = arch.read_text("utf-8") if arch.exists() else ""
        compact = "\n".join(f"{r}: {e.get('purpose', '')} -> {', '.join(e.get('imports', []))}"
                            for r, e in sorted(self.codemap.items()))
        goal = (self.dir.parent / "GOAL.md").read_text("utf-8") if (self.dir.parent / "GOAL.md").exists() else ""
        try:
            md = self.llm.chat(
                "architecture", "planner",
                f"Du bist Software-Architekt und schreibst {self.cfg['doc_language']} Markdown. Nur Fakten aus der Codemap.",
                f"Produktziel:\n{clip(goal, 2500)}\n\nCodemap:\n{clip(compact, 9000)}\n\nFeatures:\n"
                f"{json.dumps([f['title'] for f in self.features.values()])}\n\nBisherige Architektur:\n{clip(prev, 5000)}\n\n"
                "Aktualisiere vollständig mit Abschnitten: Überblick, Komponenten & Verantwortung, Datenmodell, "
                "Daten-/Request-Fluss, zentrale Invarianten, Erweiterungspunkte, bekannte technische Schulden.")
        except Exception as e:
            self.log(f"Architektur-Refresh fehlgeschlagen: {e}")
            return
        write_text(arch, md)
        write_text(self.repo / "docs" / "ARCHITECTURE.md", md)

    def refresh_readme(self, goal: str) -> None:
        try:
            md = self.llm.chat("readme", "planner", f"Du schreibst eine {self.cfg['doc_language']} README (Markdown).",
                               f"Produkt:\n{clip(goal, 2500)}\nFeatures: {json.dumps([f['title'] for f in self.features.values()])}\n"
                               "Abschnitte: Zweck, Features, Installation, Konfiguration, Starten, Tests, Projektstruktur "
                               "(verweise auf docs/), Roadmap.")
            write_text(self.repo / "README.md", md)
        except Exception as e:
            self.log(f"README fehlgeschlagen: {e}")

    # ---- Snapshot für Rollback
    def snapshot(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="memsnap-"))
        shutil.copytree(self.dir, tmp / "m")
        return tmp

    def restore(self, snap: Path) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.copytree(snap / "m", self.dir)
        shutil.rmtree(snap, ignore_errors=True)
        self.codemap = read_json(self.dir / "codemap.json", {})
        self.features = read_json(self.dir / "features.json", {})


# --------------------------------------------------------------------------- Scaffolds

SCAFFOLDS = {
    "fastapi": {
        "requirements.txt": "fastapi\nuvicorn\njinja2\nhttpx\npytest\npython-multipart\n",
        "app/__init__.py": "",
        "app/main.py": ('"""Einstiegspunkt der Anwendung."""\nfrom fastapi import FastAPI\n\napp = FastAPI(title="SaaS")\n\n\n'
                        '@app.get("/health")\ndef health() -> dict:\n    """Liveness-Check."""\n    return {"status": "ok"}\n'),
        "tests/__init__.py": "",
        "tests/test_health.py": ('from fastapi.testclient import TestClient\n\nfrom app.main import app\n\n\n'
                                 'def test_health():\n    assert TestClient(app).get("/health").json() == {"status": "ok"}\n'),
    },
    "minimal": {
        "app/__init__.py": "",
        "app/main.py": '"""Einstiegspunkt."""\n\n\ndef health() -> str:\n    return "ok"\n',
        "tests/__init__.py": "",
        "tests/test_health.py": ('import unittest\n\nfrom app.main import health\n\n\nclass T(unittest.TestCase):\n'
                                 '    def test_health(self):\n        self.assertEqual(health(), "ok")\n'),
    },
}


# --------------------------------------------------------------------------- Agent


class Agent:
    def __init__(self, ws: Path, cfg: dict | None = None):
        self.ws = ws.resolve()
        self.repo, self.dir = self.ws / "repo", self.ws / "agent"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or self.load_config()
        self.lim = self.cfg["limits"]
        self.llm = LLM(self.cfg, self.log)
        self.git = Git(self.repo)
        self.backlog = Backlog(self.dir / "backlog.json")
        self.mem = Memory(self.dir, self.repo, self.cfg, self.llm, self.log)
        self.state = read_json(self.dir / "state.json", {
            "iteration": 0, "elapsed": 0.0, "merged": 0, "consecutive_failures": 0, "empty_refills": 0,
            "req_hash": "", "ideation_min_score": self.lim["ideation_min_score"], "last_review_at": 0,
            "last_refactor_at": 0, "last_coverage_at": 0, "last_arch_at": 0})
        self.run_dir = self.dir
        self.ideas_path = self.ws / "IDEAS.md"
        self.coder_mock = None

    # ---- Konfiguration/Logging
    def load_config(self) -> dict:
        path = self.dir / "config.json"
        user = read_json(path, {})
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        preset = PRESETS.get(cfg["preset"], PRESETS["fastapi"])
        cfg["commands"] = {**preset["commands"], **cfg.get("commands", {})}
        cfg["stack"] = user.get("stack", preset["stack"])
        return cfg

    def log(self, msg: str) -> None:
        line = f"[{now()}] {msg}"
        print(line, flush=True)
        with open(self.dir / "agent.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save_state(self) -> None:
        write_json(self.dir / "state.json", self.state)

    def metric(self, **kw) -> None:
        with open(self.dir / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": now(), **kw}, ensure_ascii=False) + "\n")

    @property
    def goal(self) -> str:
        p = self.dir / "GOAL.md"
        return p.read_text("utf-8") if p.exists() else ""

    # ---- Bootstrap
    def init(self, idea: str) -> None:
        if (self.dir / "GOAL.md").exists():
            self.log("Workspace bereits initialisiert.")
            return
        if not (self.dir / "config.json").exists():
            write_json(self.dir / "config.json", {k: self.cfg[k] for k in ("preset", "doc_language", "models", "coder_model")})
        self.git.ensure()
        if not self.ideas_path.exists():
            write_text(self.ideas_path, IDEAS_TEMPLATE.format(idea=idea))
        for rel, content in SCAFFOLDS[self.cfg["preset"]].items():
            write_text(self.repo / rel, content)
        write_text(self.repo / ".gitignore", ".venv/\n__pycache__/\n.pytest_cache/\n.aider*\nnode_modules/\n*.db\n.env\n")
        self.git.commit_all("scaffold")
        brief = self.llm.json(
            "brief", "planner", "Du bist Produktstratege für SaaS-Produkte. Realistisch, fokussiert, klein anfangen.",
            f"Idee: {idea}\nStack: {self.cfg['stack']}\nSchema: "
            '{"name": "", "vision": "", "target_users": ["..."], "problems": ["..."], "mvp_features": '
            '[{"title": "", "description": "", "acceptance": ["testbar"], "value": 1-10, "effort": 1-10}], '
            '"non_goals": ["..."], "monetization": "", "quality_bar": ["..."]}')
        goal = (f"# {brief.get('name', 'Produkt')}\n\nIdee: {idea}\n\n## Vision\n{brief.get('vision', '')}\n\n"
                f"## Zielgruppe\n" + "".join(f"- {x}\n" for x in brief.get("target_users", [])) +
                "\n## Probleme\n" + "".join(f"- {x}\n" for x in brief.get("problems", [])) +
                f"\n## Monetarisierung\n{brief.get('monetization', '')}\n\n## Nicht-Ziele\n" +
                "".join(f"- {x}\n" for x in brief.get("non_goals", [])) + "\n## Qualitätsmaßstab\n" +
                "".join(f"- {x}\n" for x in brief.get("quality_bar", [])) + f"\n## Stack\n{self.cfg['stack']}\n")
        write_text(self.dir / "GOAL.md", goal)
        write_text(self.repo / "docs" / "PRODUCT.md", goal)
        for f in brief.get("mvp_features", []):
            self.backlog.add(f["title"], f.get("description", ""), "feature",
                             float(f.get("value", 5)) * 2 - float(f.get("effort", 5)),
                             f.get("acceptance", []), "MVP", "bootstrap")
        self.mem.sync()
        self.mem.refresh_architecture()
        self.mem.refresh_readme(goal)
        self.git.commit_all("docs: initial product brief and architecture")
        self.save_state()
        self.log(f"Initialisiert: {len(self.backlog.items)} MVP-Aufgaben im Backlog.")

    # ---- Coder (Aider)
    def code(self, tag: str, edit: list[str], read: list[str], prompt: str) -> str:
        if self.coder_mock:
            return self.coder_mock(self.repo, tag, edit, read, prompt)
        pf = self.run_dir / f"{tag}.prompt.md"
        write_text(pf, prompt)
        cmd = ["aider", "--yes-always", "--no-auto-commits", "--no-pretty", "--no-stream", "--no-check-update",
               "--model", self.cfg["coder_model"], "--message-file", str(pf)]
        for r in read:
            cmd += ["--read", r]
        cmd += edit
        env = {"OLLAMA_API_BASE": os.getenv("OLLAMA_API_BASE", "http://localhost:11434")}
        rc, out = sh(cmd, self.repo, self.lim["coder_timeout"], env=env)
        write_text(self.run_dir / f"{tag}.aider.log", out)
        if rc != 0:
            self.log(f"Aider-Fehlercode {rc} bei {tag}: {tail(out, 300)}")
        return out

    # ---- Build/Test
    def verify(self) -> tuple[bool, str]:
        cmds, t = self.cfg["commands"], self.lim["cmd_timeout"]
        req = self.repo / "requirements.txt"
        h = sha(req.read_text("utf-8")) if req.exists() else ""
        if cmds.get("setup") and h != self.state.get("req_hash"):
            rc, out = sh(cmds["setup"], self.repo, t, shell=True)
            if rc:
                return False, f"[setup] FAILED rc={rc}\n{tail(out, 5000)}"
            self.state["req_hash"] = h
            self.save_state()
        for name in ("build", "test", "lint"):
            c = cmds.get(name)
            if not c:
                continue
            rc, out = sh(c, self.repo, t, shell=True)
            if rc:
                return False, f"[{name}] FAILED rc={rc}\n$ {c}\n{tail(out, 6000)}"
        return True, "build + tests ok"

    # ---- Planung
    def impact_analysis(self, item: dict, previous: list[str]) -> dict:
        cands = self.mem.select_candidates(item)
        ctx = self.mem.context_pack(item, cands)
        src = "\n\n".join(f"### {f}\n```\n{clip((self.repo / f).read_text('utf-8', 'replace'), self.cfg['context']['file_clip'])}\n```"
                          for f in (cands + self.mem.neighbors(cands))[:10] if (self.repo / f).exists())
        sys_p = ("Du bist Tech-Lead. Vor jeder Änderung analysierst du Zusammenhänge: wer ruft die Dateien auf, "
                 "welche Invarianten gelten, was kann brechen. Plane minimal und konkret.")
        schema = ('{"understanding": "", "files_to_change": ["existierende Pfade"], "new_files": ["neue Pfade"], '
                  '"read_only_context": ["Pfade"], "affected_dependents": ["Pfade die brechen könnten"], '
                  '"invariants": ["..."], "risks": ["..."], "steps": ["..."], "tests_to_add": ["..."], '
                  '"docs_to_update": ["..."]}')
        plan = self.llm.json("impact", "planner", sys_p,
                             f"Aufgabe {item['id']} ({item['type']}): {item['title']}\n{item['description']}\n"
                             f"Akzeptanz: {item.get('acceptance')}\nStack: {self.cfg['stack']}\n"
                             f"Frühere Fehlversuche:\n{chr(10).join(previous) or '-'}\n\n{ctx}\n\nQuellcode:\n{src}\n\nSchema: {schema}")
        if self.cfg["features"]["plan_critique"]:
            try:
                crit = self.llm.json("plan_critique", "reviewer", "Du kritisierst Pläne streng auf übersehene Abhängigkeiten.",
                                     f"Plan: {json.dumps(plan)}\nAbhängigkeitsgraph der betroffenen Dateien:\n" +
                                     "\n".join(f"{f}: used_by={self.mem.codemap.get(f, {}).get('imported_by', [])}"
                                               for f in plan.get("files_to_change", [])) +
                                     '\nSchema: {"ok": true, "missing_files": ["..."], "concerns": ["..."]}')
                for f in crit.get("missing_files", []):
                    if f in self.mem.codemap and f not in plan.get("files_to_change", []):
                        plan.setdefault("files_to_change", []).append(f)
                plan["risks"] = plan.get("risks", []) + crit.get("concerns", [])
            except Exception as e:
                self.log(f"Plan-Kritik fehlgeschlagen: {e}")
        plan["files_to_change"] = [f for f in plan.get("files_to_change", []) if (self.repo / f).exists()][:self.lim["max_files_per_task"]]
        plan["new_files"] = plan.get("new_files", [])
        write_json(self.run_dir / "plan.json", plan)
        write_text(self.run_dir / "context.md", ctx)
        return plan

    RULES = ("Regeln: Ändere nur, was für die Aufgabe nötig ist. Halte die genannten Invarianten ein. Passe alle "
             "betroffenen Aufrufer an. Schreibe/aktualisiere Tests (Happy Path und Fehlerfälle). Kleine, typisierte "
             "Funktionen mit Docstrings, keine toten Code-Reste, keine neuen Abhängigkeiten ohne requirements.txt-Eintrag. "
             "Das Projekt muss nach deiner Änderung bauen und alle Tests bestehen.")

    def implement(self, item: dict, plan: dict) -> None:
        prompt = (f"Aufgabe {item['id']}: {item['title']}\n{item['description']}\nAkzeptanz: {item.get('acceptance')}\n\n"
                  f"Verständnis: {plan.get('understanding', '')}\nSchritte: {plan.get('steps')}\nInvarianten: {plan.get('invariants')}\n"
                  f"Risiken: {plan.get('risks')}\nBetroffene Aufrufer: {plan.get('affected_dependents')}\n"
                  f"Zu schreibende Tests: {plan.get('tests_to_add')}\nNeue Dateien: {plan.get('new_files')}\n\n{self.RULES}")
        edit = plan["files_to_change"] + plan["new_files"]
        read = [str(self.run_dir / "context.md")] + [f for f in plan.get("read_only_context", []) if (self.repo / f).exists()]
        self.code("implement", edit, read, prompt)

    def debug_loop(self, item: dict) -> tuple[bool, str]:
        ok, report = self.verify()
        for n in range(1, self.lim["max_debug_rounds"] + 1):
            if ok and self.cfg["features"]["require_tests"] and item["type"] in ("feature", "bug") \
                    and not any("test" in f.lower() for f in self.git.changed_files()):
                ok, report = False, "[policy] Es wurden keine Tests geschrieben/geändert. Füge Tests für die Änderung hinzu."
            if ok:
                return True, report
            self.log(f"Debug-Runde {n}: {report.splitlines()[0]}")
            mentioned = [f for f in re.findall(r"[\w./-]+\.(?:py|js|ts|html)", report) if (self.repo / f).exists()]
            files = sorted(set(self.git.changed_files()) | set(mentioned))[:self.lim["max_files_per_task"] + 4]
            self.code(f"debug{n}", files, [str(self.run_dir / "context.md")],
                      f"Der Build/Test ist fehlgeschlagen. Finde die Ursache (nicht Symptome) und behebe sie.\n"
                      f"Aufgabe war: {item['title']}\n\nFehlerbericht:\n{report}\n\n{self.RULES}\n"
                      "Lösche oder entschärfe keine Tests, nur um sie grün zu bekommen.")
            ok, report = self.verify()
        return ok, report

    def review(self, item: dict, plan: dict) -> dict:
        diff = self.git.diff()
        try:
            return self.llm.json(
                "review", "reviewer", "Du bist strenger Code-Reviewer (Korrektheit, Sicherheit, Lesbarkeit, Tests, Invarianten).",
                f"Aufgabe: {item['title']}\nAkzeptanz: {item.get('acceptance')}\nInvarianten: {plan.get('invariants')}\n"
                f"Diff:\n{clip(diff, 14000)}\nSchema: "
                '{"score": 1-10, "verdict": "approve|revise", "summary": "2-3 Sätze, was geändert wurde und warum", '
                '"issues": [{"file": "", "problem": "", "fix": ""}]}')
        except Exception as e:
            self.log(f"Review fehlgeschlagen: {e}")
            return {"score": 7, "verdict": "approve", "summary": item["title"], "issues": []}

    # ---- Eine Aufgabe komplett bearbeiten
    def process(self, item: dict) -> bool:
        self.run_dir = self.dir / "runs" / f"{self.state['iteration']:04d}-{item['id']}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        previous: list[str] = []
        self.backlog.set(item, status="in_progress")
        for attempt in range(1, self.lim["max_attempts_per_task"] + 1):
            self.backlog.set(item, attempts=item["attempts"] + 1)
            self.log(f"== {item['id']} {item['title']} (Versuch {attempt})")
            branch = f"agent/{item['id']}-{attempt}"
            self.git.branch_from_main(branch)
            base_sha = self.git.out("rev-parse", "main")
            try:
                ok, why = self.attempt(item, previous, branch, base_sha)
            except Exception as e:
                ok, why = False, f"Ausnahme: {e}"
            if ok:
                self.backlog.set(item, status="done")
                return True
            self.git.hard_reset()
            self.git.run("checkout", "-q", "main")
            self.git.run("branch", "-D", branch)
            previous.append(f"Versuch {attempt}: {clip(why, 600)}")
            self.record_failure(item, why)
        self.backlog.set(item, status="blocked")
        return False

    def record_failure(self, item: dict, why: str) -> None:
        try:
            d = self.llm.json("lesson", "summarizer", "Du formulierst Lessons Learned.",
                              f"Aufgabe: {item['title']}\nFehler:\n{clip(why, 3000)}\n"
                              'Schema: {"lesson": "1-2 Sätze: Ursache und wie man es vermeidet"}')
            lesson = d["lesson"]
        except Exception:
            lesson = clip(why, 300)
        self.mem.add_lesson(lesson, [], item["id"])

    def attempt(self, item: dict, previous: list[str], branch: str, base_sha: str) -> tuple[bool, str]:
        plan = self.impact_analysis(item, previous)
        self.implement(item, plan)
        ok, report = self.debug_loop(item)
        if not ok:
            return False, "Build/Test nach Debug-Runden rot:\n" + report
        review = self.review(item, plan)
        if review.get("verdict") == "revise" or review.get("score", 10) < self.lim["min_review_score"]:
            self.log(f"Review verlangt Überarbeitung (Score {review.get('score')})")
            self.code("review_fix", sorted(set(self.git.changed_files()))[:self.lim["max_files_per_task"] + 4],
                      [str(self.run_dir / "context.md")],
                      f"Behebe diese Review-Punkte:\n{json.dumps(review.get('issues', []), indent=1)}\n\n{self.RULES}")
            ok, report = self.debug_loop(item)
            if not ok:
                return False, "Nach Review-Fix rot:\n" + report
            review = self.review(item, plan)
        self.git.commit_all(f"{item['id']}: {item['title']}")
        checkpoint = self.git.sha()
        if self.cfg["features"]["cleanup_pass"] and item["type"] != "docs":
            changed = [f for f in self.git.changed_files(base_sha) if (self.repo / f).exists() and f.endswith(tuple(self.cfg["source_exts"]))]
            self.code("cleanup", changed[:self.lim["max_files_per_task"]], [str(self.run_dir / "context.md")],
                      "Cleanup ohne Verhaltensänderung: tote Code-Reste entfernen, Namen klären, Duplikate zusammenführen, "
                      "Docstrings ergänzen, Funktionen vereinfachen. Öffentliche Schnittstellen und Tests unverändert lassen.")
            ok, report = self.verify()
            if ok:
                self.git.commit_all(f"{item['id']}: cleanup")
            else:
                self.log("Cleanup brach den Build, verworfen.")
                self.git.hard_reset(checkpoint)
        snap = self.mem.snapshot()
        try:
            changed_all = [f for f in self.git.changed_files(base_sha) if (self.repo / f).exists()]
            self.mem.sync(force_docs_for=[f for f in changed_all if f in self.mem.codemap])
            self.mem.add_decision(item, plan, review, self.git.diff(base_sha))
            self.mem.record_feature(item, changed_all, review)
            self.mem.changelog(item, review)
            if self.state["merged"] + 1 - self.state["last_arch_at"] >= self.lim["architecture_refresh_every"]:
                self.mem.refresh_architecture()
                self.mem.refresh_readme(self.goal)
                self.state["last_arch_at"] = self.state["merged"] + 1
            self.git.commit_all(f"{item['id']}: update docs and memory")
            ok, report = self.verify()
            if not ok:
                raise RuntimeError("Build nach Doku-Update rot:\n" + report)
            old_main = self.git.out("rev-parse", "main")
            if not self.git.merge_ff(branch):
                raise RuntimeError("Fast-Forward-Merge nach main fehlgeschlagen")
            ok, report = self.verify()
            if not ok:
                self.git.hard_reset(old_main)
                raise RuntimeError("Build auf main nach Merge rot, Rollback:\n" + report)
        except Exception as e:
            self.mem.restore(snap)
            return False, str(e)
        shutil.rmtree(snap, ignore_errors=True)
        self.git.run("branch", "-D", branch)
        self.state["merged"] += 1
        self.metric(item=item["id"], title=item["title"], type=item["type"], attempts=item["attempts"],
                    review_score=review.get("score"), files=len(changed_all), loc=sum(e.get("loc", 0) for e in self.mem.codemap.values()))
        self.log(f"✔ {item['id']} gemerged (Review {review.get('score')})")
        return True, "ok"

    # ---- Ideen, Review, Hygiene
    def ideate(self) -> int:
        lens = LENSES[self.state["iteration"] % len(LENSES)]
        feats = [f["title"] for f in self.mem.features.values()]
        lessons = ""
        lp = self.mem.dir / "lessons.jsonl"
        if lp.exists():
            lessons = "\n".join(json.loads(l)["lesson"] for l in lp.read_text("utf-8").splitlines()[-8:] if l.strip())
        ideas = self.llm.json(
            "ideate", "planner", "Du bist Produktmanager eines wachsenden SaaS. Schlage Verbesserungen vor, die Nutzerwert liefern, "
            "klein genug für eine Iteration sind und zum Produktziel passen. Keine Dubletten.",
            f"Produktziel:\n{clip(self.goal, 2500)}\n\nBereits gebaut: {feats}\nIm Backlog: {self.backlog.titles()[-30:]}\n"
            f"Fokus-Linse: {lens}\nSaaS-Checkliste (Lücken füllen): {SAAS_CHECKLIST}\nLessons: {lessons}\n"
            f"Gib {self.lim['ideation_batch'] * 2} Ideen. Schema: "
            '{"ideas": [{"title": "", "description": "", "type": "feature|refactor|bug|docs|test", "rationale": "", '
            '"acceptance": ["testbar"], "value": 1-10, "effort": 1-10, "risk": 1-10}]}')
        cands = ideas.get("ideas", [])
        try:
            crit = self.llm.json("ideate_critic", "reviewer", "Du bewertest Ideen kritisch auf Wert, Passung und Machbarkeit.",
                                 f"Ziel:\n{clip(self.goal, 1500)}\nIdeen: {json.dumps(cands)}\nSchema: "
                                 '{"scores": [{"title": "", "fit": 1-10, "keep": true}]}')
            keep = {s["title"]: s for s in crit.get("scores", [])}
        except Exception:
            keep = {}
        scored = []
        for c in cands:
            k = keep.get(c.get("title"), {"fit": 6, "keep": True})
            if not k.get("keep", True):
                continue
            score = float(c.get("value", 5)) * 2 - float(c.get("effort", 5)) - 0.5 * float(c.get("risk", 5)) + float(k.get("fit", 6)) / 2
            scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        added = 0
        for score, c in scored:
            if added >= self.lim["ideation_batch"] or float(c.get("value", 5)) < self.state["ideation_min_score"] - 2:
                continue
            if self.backlog.add(c["title"], c.get("description", ""), c.get("type", "feature"), score, c.get("acceptance", []),
                                c.get("rationale", ""), f"ideation:{lens}", feats):
                added += 1
        self.log(f"Ideation ({lens}): {added} neue Aufgaben")
        return added

    def product_review(self) -> int:
        ok, report = self.verify()
        data = self.llm.json(
            "product_review", "reviewer", "Du bist unabhängiger Produkt-Auditor. Finde konkrete Lücken in Qualität und Vollständigkeit.",
            f"Ziel:\n{clip(self.goal, 2000)}\nCodemap:\n{clip(self.mem.render_codemap(), 9000)}\nBuild/Test: {report[:500]}\n"
            f"Checkliste: {SAAS_CHECKLIST}\nSchema: "
            '{"scores": {"completeness": 1-10, "code_quality": 1-10, "test_coverage": 1-10, "docs": 1-10, "ux": 1-10}, '
            '"items": [{"title": "", "description": "", "type": "feature|bug|refactor|docs|test", "acceptance": [], "priority": 1-10}]}')
        self.metric(kind="product_review", scores=data.get("scores"))
        added = 0
        for i in data.get("items", [])[:6]:
            if self.backlog.add(i["title"], i.get("description", ""), i.get("type", "refactor"), float(i.get("priority", 5)),
                                i.get("acceptance", []), "product review", "review"):
                added += 1
        self.state["last_review_at"] = self.state["merged"]
        self.log(f"Produkt-Review: {added} neue Aufgaben, Scores {data.get('scores')}")
        return added

    def schedule_hygiene(self) -> None:
        merged = self.state["merged"]
        if merged - self.state["last_refactor_at"] >= self.lim["refactor_every"]:
            churn = self.git.churn()
            ranked = sorted(self.mem.codemap, key=lambda r: -(self.mem.codemap[r].get("loc", 0) * churn.get(r, 1)))[:3]
            if ranked:
                self.backlog.add(f"Refactor hotspots: {', '.join(ranked)}",
                                 "Vereinfache die größten/meist geänderten Dateien: Funktionen aufteilen, Duplikate entfernen, "
                                 "Namen verbessern, ohne Verhalten zu ändern. Tests müssen grün bleiben.",
                                 "refactor", 4.0, ["Build und Tests grün", "keine Verhaltensänderung"], "Hygiene", "hygiene")
            self.state["last_refactor_at"] = merged
        if merged - self.state["last_coverage_at"] >= self.lim["coverage_every"]:
            tested = {t for e in self.mem.codemap.values() if "test" in str(e) for t in e.get("imports", [])}
            untested = [r for r in self.mem.codemap if r.startswith("app/") and r not in tested and not r.endswith("__init__.py")][:4]
            if untested:
                self.backlog.add(f"Add tests for: {', '.join(untested)}", "Schreibe fehlende Unit-Tests inkl. Fehlerfälle.",
                                 "test", 4.5, ["Tests für alle genannten Dateien vorhanden"], "Hygiene", "hygiene")
            self.state["last_coverage_at"] = merged

    def health_check(self) -> None:
        self.git.hard_reset()
        self.git.run("checkout", "-q", "main")
        ok, report = self.verify()
        if not ok and not any(i["status"] == "pending" and i["title"].startswith("Repair main") for i in self.backlog.items):
            self.log("main ist rot, Reparatur-Aufgabe angelegt")
            self.backlog.add("Repair main build/tests", f"Der Build/Test auf main ist rot. Behebe die Ursache:\n{report}",
                             "bug", 2000, ["Build und Tests grün"], "health", "health")

    # ---- Nutzer-Vorgaben (IDEAS.md)
    def sync_ideas(self) -> None:
        """Liest IDEAS.md. Bei Änderung (oder periodisch zum Abgleich) wird neu geplant."""
        text = read_ideas(self.ideas_path)
        h = sha(text)
        due = self.state["merged"] - self.state.get("ideas_review_at", 0) >= self.lim["ideas_review_every"]
        changed = h != self.state.get("ideas_hash", "")
        if not text:
            self.state["ideas_hash"] = h
            return
        if not changed and not due:
            return
        self.log("IDEAS.md geändert, plane neu." if changed else "Periodischer Abgleich mit IDEAS.md.")
        seen_p = self.dir / "ideas_seen.md"
        seen = seen_p.read_text("utf-8") if seen_p.exists() else ""
        open_items = [{"id": i["id"], "title": i["title"], "type": i["type"], "priority": i["priority"], "status": i["status"]}
                      for i in self.backlog.items if i["status"] in ("pending", "blocked")]
        try:
            d = self.llm.json(
                "replan", "planner",
                "Du bist Produktplaner. Der Nutzer pflegt Wünsche und Vorgaben; du gleichst sie mit dem Stand des Produkts ab. "
                "Ergänze nur, was fehlt. Größere Umbauten zerlegst du in kleine, geordnete Schritte (type refactor, frühere "
                "Schritte höhere Priorität). Offene Aufgaben, die den Vorgaben widersprechen, werden gestrichen.",
                f"Vorgaben des Nutzers (aktuell):\n{clip(text, 5000)}\n\nZuletzt verarbeitete Fassung:\n{clip(seen, 3000) or '(keine)'}\n\n"
                f"Produktziel:\n{clip(self.goal, 1500)}\nBereits gebaut: {[f['title'] for f in self.mem.features.values()]}\n"
                f"Offene Aufgaben: {json.dumps(open_items)}\nArchitektur:\n"
                f"{clip((self.mem.dir / 'ARCHITECTURE.md').read_text('utf-8') if (self.mem.dir / 'ARCHITECTURE.md').exists() else '', 3000)}\n\n"
                "Schema: "
                '{"summary": "was hat sich geändert", "new_items": [{"title": "", "description": "", '
                '"type": "feature|refactor|bug|docs|test", "acceptance": ["testbar"], "priority": 1-10}], '
                '"reprioritize": [{"id": "T0001", "priority": 1-10}], "cancel": ["T0001"]}')
        except Exception as e:
            self.log(f"Neuplanung fehlgeschlagen, versuche es später erneut: {e}")
            return
        added = 0
        for i in d.get("new_items", []):
            # Nutzerwünsche haben Vorrang vor selbst erfundenen Ideen (Priorität > 20)
            if self.backlog.add(i["title"], i.get("description", ""), i.get("type", "feature"),
                                20 + float(i.get("priority", 5)), i.get("acceptance", []), "Nutzervorgabe (IDEAS.md)", "ideas"):
                added += 1
        by_id = {i["id"]: i for i in self.backlog.items}
        for r in d.get("reprioritize", []):
            it = by_id.get(r.get("id"))
            if it and it["status"] == "pending":
                self.backlog.set(it, priority=20 + float(r.get("priority", 5)))
        cancelled = 0
        for cid in d.get("cancel", []):
            it = by_id.get(cid)
            if it and it["status"] in ("pending", "blocked"):
                self.backlog.set(it, status="cancelled", notes=it.get("notes", []) + ["gestrichen wegen IDEAS.md"])
                cancelled += 1
        marker = "\n\n## Nutzer-Vorgaben (aus IDEAS.md)\n"
        write_text(self.dir / "GOAL.md", self.goal.split(marker)[0].rstrip() + marker + clip(text, 3000) + "\n")
        write_text(seen_p, text)
        write_text(self.repo / "docs" / "PRODUCT.md", self.goal)
        self.git.commit_all("docs: update product goals from IDEAS.md")
        self.state["ideas_hash"], self.state["ideas_review_at"] = h, self.state["merged"]
        self.state["empty_refills"] = 0
        self.save_state()
        self.metric(kind="replan", added=added, cancelled=cancelled, summary=d.get("summary", ""))
        self.log(f"Neuplanung: {added} neue Aufgaben, {cancelled} gestrichen. {d.get('summary', '')}")

    # ---- Hauptschleife
    def should_stop(self, started: float) -> str | None:
        if (self.dir / "STOP").exists():
            return "STOP-Datei gefunden"
        if self.state["iteration"] >= self.lim["max_iterations"]:
            return "max_iterations erreicht"
        if self.state["elapsed"] + time.time() - started > self.lim["max_hours"] * 3600:
            return "max_hours erreicht"
        if self.state["consecutive_failures"] >= self.lim["max_consecutive_failures"]:
            return "zu viele Fehlschläge in Folge, bitte Logs prüfen"
        return None

    def refill(self) -> bool:
        added = 0
        if self.state["merged"] - self.state["last_review_at"] >= self.lim["product_review_every"] or not self.mem.features:
            added += self.product_review()
        if not added:
            added += self.ideate()
        if added:
            self.state["empty_refills"] = 0
            return True
        self.state["empty_refills"] += 1
        self.state["ideation_min_score"] = max(3, self.state["ideation_min_score"] - 1)
        self.log(f"Keine neuen Ideen ({self.state['empty_refills']}/3), senke Schwelle auf {self.state['ideation_min_score']}")
        return self.state["empty_refills"] < 3

    def run(self) -> None:
        if not (self.dir / "GOAL.md").exists():
            sys.exit("Workspace nicht initialisiert: erst `init --idea ...`.")
        (self.dir / "STOP").unlink(missing_ok=True)
        for it in self.backlog.items:  # Aufgaben eines abgebrochenen Laufs wieder aufnehmen
            if it["status"] == "in_progress":
                it["status"] = "pending"
        self.backlog.save()
        self.state["empty_refills"] = 0  # neuer Start: nach `stop`/Sättigung erneut Ideen versuchen
        started = time.time()
        self.git.hard_reset()
        self.health_check()
        reason = None
        while True:
            self.state["elapsed"] += 0  # Platzhalter, echte Summe wird beim Speichern addiert
            reason = self.should_stop(started)
            if reason:
                break
            self.state["iteration"] += 1
            if self.state["iteration"] % self.lim["health_every"] == 0:
                self.health_check()
            self.sync_ideas()
            self.schedule_hygiene()
            item = self.backlog.next()
            if not item:
                if not self.refill():
                    reason = "Produkt gesättigt: keine sinnvollen neuen Ideen mehr"
                    break
                self.save_state()
                continue
            ok = self.process(item)
            self.state["consecutive_failures"] = 0 if ok else self.state["consecutive_failures"] + 1
            self.save_state()
        self.state["elapsed"] += time.time() - started
        self.save_state()
        self.log(f"Ende: {reason}")

    def status(self) -> str:
        c: dict[str, int] = {}
        for i in self.backlog.items:
            c[i["status"]] = c.get(i["status"], 0) + 1
        return (f"Iteration {self.state['iteration']}, gemerged {self.state['merged']}, Backlog {c}, "
                f"Features {len(self.mem.features)}, Dateien {len(self.mem.codemap)}, "
                f"Fehler in Folge {self.state['consecutive_failures']}, Laufzeit {self.state['elapsed'] / 3600:.1f} h")


# --------------------------------------------------------------------------- Selftest


def selftest() -> None:
    """Prüft die Schleifenmechanik mit Stub-LLM und Stub-Coder (kein Ollama/Aider nötig)."""
    ws = Path(tempfile.mkdtemp(prefix="saas-selftest-"))
    counter = {"n": 0}

    def fake_llm(tag, role, system, user):
        if tag == "brief":
            return json.dumps({"name": "Demo", "vision": "v", "mvp_features": [
                {"title": "Greeting endpoint", "description": "greet", "acceptance": ["test"], "value": 8, "effort": 2}]})
        if tag == "summarize":
            return json.dumps({"purpose": "demo module", "public_api": ["f()"], "invariants": ["pure"]})
        if tag == "select_files":
            return json.dumps({"files": ["app/main.py"]})
        if tag == "impact":
            return json.dumps({"understanding": "u", "files_to_change": ["app/main.py"], "new_files": [], "steps": ["s"],
                               "invariants": ["health stays ok"], "tests_to_add": ["t"]})
        if tag == "replan":
            return json.dumps({"summary": "neue Wuensche", "new_items": [{"title": "Dark mode toggle", "description": "d",
                               "type": "feature", "acceptance": ["x"], "priority": 7}], "reprioritize": [], "cancel": []})
        if tag == "plan_critique":
            return json.dumps({"ok": True, "missing_files": [], "concerns": []})
        if tag == "review":
            return json.dumps({"score": 8, "verdict": "approve", "summary": "added things", "issues": []})
        if tag == "decision":
            return json.dumps({"title": "t", "context": "c", "decision": "d", "alternatives": "a", "consequences": "q"})
        if tag in ("ideate",):
            counter["n"] += 1
            return json.dumps({"ideas": [{"title": ["Export invoices as CSV", "Team workspace roles", "Usage based billing page",
                                          "Public REST API keys", "Weekly email digest"][counter["n"] % 5], "description": "d",
                                          "type": "feature", "value": 9, "effort": 2, "risk": 1, "acceptance": ["x"]}]})
        if tag == "ideate_critic":
            return json.dumps({"scores": []})
        if tag == "product_review":
            return json.dumps({"scores": {"ux": 5}, "items": []})
        if tag == "lesson":
            return json.dumps({"lesson": "avoid bad things"})
        return "# generated markdown"

    def fake_coder(repo, tag, edit, read, prompt):
        if tag == "implement":
            n = len(list((repo / "app").glob("feat_*.py"))) + 1
            (repo / "app" / f"feat_{n}.py").write_text(f"from app.main import health\n\n\ndef feat_{n}() -> str:\n    return health()\n")
            (repo / "tests" / f"test_feat_{n}.py").write_text(
                f"import unittest\nfrom app.feat_{n} import feat_{n}\n\n\nclass T(unittest.TestCase):\n"
                f"    def test(self):\n        self.assertEqual(feat_{n}(), 'ok')\n")
        return ""

    LLM.mock = fake_llm
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["preset"] = "minimal"
    cfg["commands"] = PRESETS["minimal"]["commands"]
    cfg["stack"] = PRESETS["minimal"]["stack"]
    cfg["limits"]["max_iterations"] = 6
    cfg["limits"]["architecture_refresh_every"] = 1
    a = Agent(ws, cfg)
    a.coder_mock = fake_coder
    a.init("demo idea")
    with open(ws / "IDEAS.md", "a", encoding="utf-8") as f:
        f.write("\n- Dark mode umschaltbar machen\n")
    a.run()
    assert any(i["source"] == "ideas" for i in a.backlog.items), "IDEAS.md muss zu Backlog-Eintraegen fuehren"
    assert "Nutzer-Vorgaben (aus IDEAS.md)" in a.goal
    print(a.status())
    assert a.state["merged"] >= 3, "es sollten mehrere Features gemerged worden sein"
    assert a.git.out("status", "--porcelain") == "", "Arbeitsverzeichnis muss sauber sein"
    assert (ws / "repo" / "docs" / "ARCHITECTURE.md").exists() and (ws / "repo" / "CHANGELOG.md").exists()
    assert (ws / "repo" / "docs" / "modules").exists() and a.mem.codemap["app/feat_1.py"]["imports"] == ["app/main.py"]
    assert "app/feat_1.py" in a.mem.codemap["app/main.py"]["imported_by"], "Rückwärts-Graph fehlt"
    print("SELFTEST OK ->", ws)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return "-".join(s.strip("-").split("-")[:5])[:40] or "projekt"


def unique_slug(root: Path, text: str) -> str:
    slug = slugify(text)
    n, cand = 2, slug
    while (root / cand).exists():
        cand, n = f"{slug}-{n}", n + 1
    return cand


def list_projects(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*") if (p / "agent" / "GOAL.md").exists())


def pick_project(root: Path, name: str | None) -> Path:
    if name:
        ws = root / name
        if not (ws / "agent").exists():
            sys.exit(f"Projekt '{name}' nicht gefunden in {root}/. Vorhanden: {[p.name for p in list_projects(root)]}")
        return ws
    found = list_projects(root)
    if len(found) != 1:
        sys.exit(f"Bitte --project angeben. Vorhanden: {[p.name for p in found]}")
    return found[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "run", "status", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--projects-dir", default="projects", help="Ordner, der alle Projekte enthält")
        if name == "init":
            p.add_argument("--idea", required=True)
            p.add_argument("--name", help="Projektordner (Standard: aus der Idee abgeleitet)")
            p.add_argument("--preset", default="fastapi", choices=list(PRESETS))
            p.add_argument("--config", help="Pfad zu einer config.json (siehe config.example.json)")
        else:
            p.add_argument("--project", help="Projektname (optional, wenn es nur ein Projekt gibt)")
    p_list = sub.add_parser("list")
    p_list.add_argument("--projects-dir", default="projects")
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    root = Path(a.projects_dir)
    if a.cmd == "list":
        for p in list_projects(root):
            print(f"{p.name}: {Agent(p).status()}")
        return
    if a.cmd == "init":
        ws = root / unique_slug(root, a.name or a.idea)
        (ws / "agent").mkdir(parents=True, exist_ok=True)
        base = read_json(Path(a.config), {}) if a.config else {}
        write_json(ws / "agent" / "config.json", {"preset": a.preset, **base})
        print(f"Projekt: {ws}")
        return Agent(ws).init(a.idea)
    ws = pick_project(root, a.project)
    if a.cmd == "stop":
        write_text(ws / "agent" / "STOP", "stop\n")
        print(f"STOP gesetzt für {ws.name}")
    elif a.cmd == "run":
        Agent(ws).run()
    else:
        print(Agent(ws).status())


if __name__ == "__main__":
    main()
