"""Per-project knowledge index (standard library only): SQLite FTS5 keyword search plus optional embeddings.

The index is a rebuildable *view*. The sources of truth stay the human-readable files (codemap.json, decisions.jsonl,
lessons.jsonl, web/pages/*.json ...), so a broken or deleted knowledge.db can never lose knowledge: `make`-free rebuild
happens automatically on the next run. Results of both search methods are fused with reciprocal rank fusion (RRF) and
scaled by a per-entry weight (used to fade out old or disproven lessons).
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")
RRF_K = 60
EMBED_BATCH = 16
MAX_TEXT = 4000

Embedder = Callable[[list[str]], "list[list[float]] | None"]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def ollama_embedder(api: str, model: str, key: str = "ollama", timeout: int = 120) -> Embedder:
    """Embeddings through Ollama's /api/embed (same host as the chat API). Returns None on any failure (FTS-only fallback)."""
    u = urllib.parse.urlparse(api)
    url = f"{u.scheme}://{u.netloc}/api/embed"

    def embed(texts: list[str]):
        req = urllib.request.Request(url, data=json.dumps({"model": model, "input": texts}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                vecs = json.loads(r.read())["embeddings"]
            return vecs if len(vecs) == len(texts) else None
        except Exception:  # noqa: BLE001  (model not installed, server down ...)
            return None
    return embed


def _pack(v: list[float]) -> bytes:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array("f", (x / norm for x in v)).tobytes()


def _unpack(b: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(b)
    return a


def fts_query(text: str) -> str:
    toks = list(dict.fromkeys(t.lower() for t in TOKEN.findall(text)))[:24]
    return " OR ".join(f'"{t}"' for t in toks)


class KB:
    def __init__(self, path: Path, embed: Embedder | None = None):
        self.path, self.embed = path, embed
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS doc(id INTEGER PRIMARY KEY, kind TEXT, ref TEXT, text TEXT, hash TEXT,
                meta TEXT, weight REAL DEFAULT 1.0, updated REAL, vec BLOB, UNIQUE(kind, ref));
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content='doc', content_rowid='id', tokenize='porter unicode61');
            CREATE TABLE IF NOT EXISTS info(k TEXT PRIMARY KEY, v TEXT);
        """)

    def close(self) -> None:
        self.db.close()

    # ---- writing
    def upsert(self, kind: str, ref: str, text: str, meta: dict | None = None, weight: float = 1.0) -> bool:
        """Stores or updates one entry. Returns True if the content changed (and must be re-embedded)."""
        text = text[:MAX_TEXT]
        h, m = sha(text), json.dumps(meta or {}, ensure_ascii=False)
        row = self.db.execute("SELECT id, hash, text FROM doc WHERE kind=? AND ref=?", (kind, ref)).fetchone()
        if row and row[1] == h:
            self.db.execute("UPDATE doc SET meta=?, weight=? WHERE id=?", (m, weight, row[0]))
            return False
        if row:
            self.db.execute("INSERT INTO fts(fts, rowid, text) VALUES('delete', ?, ?)", (row[0], row[2]))
            self.db.execute("UPDATE doc SET text=?, hash=?, meta=?, weight=?, updated=?, vec=NULL WHERE id=?",
                            (text, h, m, weight, time.time(), row[0]))
            rid = row[0]
        else:
            rid = self.db.execute("INSERT INTO doc(kind, ref, text, hash, meta, weight, updated) VALUES(?,?,?,?,?,?,?)",
                                  (kind, ref, text, h, m, weight, time.time())).lastrowid
        self.db.execute("INSERT INTO fts(rowid, text) VALUES(?, ?)", (rid, text))
        return True

    def remove_missing(self, kind: str, keep: set[str]) -> int:
        """Drops entries of one kind whose source no longer exists."""
        n = 0
        for rid, ref, text in self.db.execute("SELECT id, ref, text FROM doc WHERE kind=?", (kind,)).fetchall():
            if ref not in keep:
                self.db.execute("INSERT INTO fts(fts, rowid, text) VALUES('delete', ?, ?)", (rid, text))
                self.db.execute("DELETE FROM doc WHERE id=?", (rid,))
                n += 1
        return n

    def commit(self) -> None:
        self.db.commit()

    def embed_pending(self, limit: int = 200) -> int:
        """Embeds entries that have no vector yet. Silently does nothing without an embedding model."""
        if not self.embed:
            return 0
        rows = self.db.execute("SELECT id, text FROM doc WHERE vec IS NULL LIMIT ?", (limit,)).fetchall()
        done = 0
        for i in range(0, len(rows), EMBED_BATCH):
            chunk = rows[i:i + EMBED_BATCH]
            vecs = self.embed([t for _, t in chunk])
            if not vecs:
                break
            for (rid, _), v in zip(chunk, vecs):
                self.db.execute("UPDATE doc SET vec=? WHERE id=?", (_pack(v), rid))
                done += 1
        self.db.commit()
        return done

    # ---- reading
    def search(self, query: str, kinds: tuple[str, ...] | None = None, limit: int = 8) -> list[dict]:
        """Hybrid search: BM25 ranking fused with embedding similarity (if available), scaled by entry weight."""
        scores: dict[int, float] = {}
        q = fts_query(query)
        kind_sql, kind_args = ("", [])
        if kinds:
            kind_sql, kind_args = f" AND doc.kind IN ({','.join('?' * len(kinds))})", list(kinds)
        if q:
            try:
                rows = self.db.execute(
                    "SELECT doc.id FROM fts JOIN doc ON doc.id = fts.rowid WHERE fts MATCH ?" + kind_sql +
                    " ORDER BY bm25(fts) LIMIT 40", [q, *kind_args]).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for rank, (rid,) in enumerate(rows):
                scores[rid] = scores.get(rid, 0) + 1 / (RRF_K + rank)
        qv = self.embed([query[:1000]]) if self.embed and query.strip() else None
        if qv:
            qa = _unpack(_pack(qv[0]))
            cand = self.db.execute("SELECT id, vec FROM doc WHERE vec IS NOT NULL" + kind_sql.replace("doc.", ""), kind_args).fetchall()
            sims = sorted(((sum(a * b for a, b in zip(qa, _unpack(v))), rid) for rid, v in cand), reverse=True)[:40]
            for rank, (sim, rid) in enumerate(sims):
                if sim > 0.3:
                    scores[rid] = scores.get(rid, 0) + 1 / (RRF_K + rank)
        out = []
        for rid, s in scores.items():
            kind, ref, text, meta, weight = self.db.execute("SELECT kind, ref, text, meta, weight FROM doc WHERE id=?", (rid,)).fetchone()
            out.append({"kind": kind, "ref": ref, "text": text, "meta": json.loads(meta or "{}"), "score": s * weight})
        out.sort(key=lambda r: -r["score"])
        return [r for r in out if r["score"] > 0][:limit]

    def stats(self) -> dict:
        rows = self.db.execute("SELECT kind, COUNT(*), SUM(vec IS NOT NULL) FROM doc GROUP BY kind").fetchall()
        return {k: {"entries": n, "embedded": int(e or 0)} for k, n, e in rows}


def open_readonly(path: Path) -> KB | None:
    """For the chat: use an existing index, never create one."""
    return KB(path) if path.exists() else None
