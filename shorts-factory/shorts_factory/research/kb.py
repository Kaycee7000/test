"""Knowledge base for retrieval-augmented writing (RAG).

One SQLite file (data/knowledge.db) holding every piece of research the system gathers:
trend signals, Wikipedia articles, RSS items, per-topic research dossiers and our own published
scripts. Retrieval is hybrid: BM25 full-text (FTS5) + embedding cosine similarity, merged with
reciprocal-rank fusion, then filtered by niche / language / kind / age.

Scale note: vectors are scanned with numpy after SQL filtering. That is fast up to a few hundred
thousand chunks per niche, which is years of output at 50 videos/day. Swap in sqlite-vec or
pgvector behind `_vector_candidates` if you outgrow it.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .embed import Embedder, NoEmbedder

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- trend_video | trend_wiki | on_this_day | rss | wiki_article | dossier | script | topic
    niche TEXT NOT NULL,         -- channel niche slug, or '*' for niche-independent signals
    language TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    fetched_at TEXT NOT NULL,
    expires_at TEXT,
    hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS docs_filter ON docs(kind, niche, language, fetched_at);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    text TEXT NOT NULL,
    emb BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, chunk_id UNINDEXED, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS scout_runs (channel TEXT NOT NULL, day TEXT NOT NULL, at TEXT NOT NULL,
                                       PRIMARY KEY (channel, day));
"""

STOP = set("""a an and are as at be but by for from has have in into is it its of on or that the their this to
was were what when where which who why will with you your de la el en es los las un una y que del por con para
se su al lo como más""".split())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def chunk_text(text: str, size: int = 900, overlap_sentences: int = 1) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    chunks, cur = [], []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if cur and len(" ".join(cur)) + len(s) + 1 > size:
            chunks.append(" ".join(cur))
            cur = cur[-overlap_sentences:] if overlap_sentences else []
        cur.append(s[: size * 2])
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def fts_query(text: str, max_terms: int = 14) -> str:
    terms = []
    for t in re.findall(r"\w+", text.lower()):
        if len(t) > 2 and t not in STOP and t not in terms:
            terms.append(t)
    return " OR ".join(f'"{t}"' for t in terms[:max_terms])


@dataclass
class Hit:
    doc_id: int
    chunk_id: int
    kind: str
    title: str
    url: str
    text: str
    score: float
    meta: dict[str, Any]


class KnowledgeBase:
    def __init__(self, path: str | Path, embedder: Embedder | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or NoEmbedder()
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ write

    def add(self, kind: str, niche: str, language: str, title: str, body: str, *, url: str = "",
            source: str = "", platform: str = "", meta: dict[str, Any] | None = None,
            ttl_days: int | None = None, key: str | None = None, replace: bool = False) -> int:
        """Insert a document (chunked + embedded). Re-adding the same key refreshes meta/expiry only,
        unless replace=True (content we regenerate, like dossiers and scripts), which re-indexes it."""
        ident = key or url or f"{title}\n{body[:500]}"
        h = hashlib.sha1(f"{kind}|{niche}|{language}|{ident}".encode()).hexdigest()
        now = now_utc()
        expires = iso(now + timedelta(days=ttl_days)) if ttl_days else None
        meta_s = json.dumps(meta or {}, ensure_ascii=False)
        with self._lock, self.conn:
            row = self.conn.execute("SELECT id FROM docs WHERE hash=?", (h,)).fetchone()
            if row and replace:
                self.delete_docs([row["id"]])
                row = None
            if row:
                self.conn.execute("UPDATE docs SET meta=?, fetched_at=?, expires_at=?, title=? WHERE id=?",
                                  (meta_s, iso(now), expires, title, row["id"]))
                return int(row["id"])
            cur = self.conn.execute(
                "INSERT INTO docs(kind,niche,language,platform,title,url,source,body,meta,fetched_at,expires_at,hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, niche, language, platform, title, url, source, body, meta_s, iso(now), expires, h))
            doc_id = int(cur.lastrowid)
            pieces = chunk_text(body) or [title]
            vecs = self.embedder.encode([f"{title}\n{p}" for p in pieces]) if self.embedder.dim else None
            for i, p in enumerate(pieces):
                emb = vecs[i].astype(np.float16).tobytes() if vecs is not None else None
                cid = self.conn.execute("INSERT INTO chunks(doc_id,ord,text,emb) VALUES (?,?,?,?)",
                                        (doc_id, i, p, emb)).lastrowid
                self.conn.execute("INSERT INTO chunks_fts(text, chunk_id) VALUES (?,?)", (f"{title} {p}", cid))
            return doc_id

    def delete_docs(self, ids: Iterable[int]) -> int:
        ids = list(ids)
        if not ids:
            return 0
        q = ",".join("?" * len(ids))
        with self._lock, self.conn:
            cids = [r[0] for r in self.conn.execute(f"SELECT id FROM chunks WHERE doc_id IN ({q})", ids)]
            if cids:
                self.conn.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({','.join('?' * len(cids))})", cids)
            self.conn.execute(f"DELETE FROM chunks WHERE doc_id IN ({q})", ids)
            self.conn.execute(f"DELETE FROM docs WHERE id IN ({q})", ids)
        return len(ids)

    def purge_expired(self) -> int:
        rows = self.conn.execute("SELECT id FROM docs WHERE expires_at IS NOT NULL AND expires_at < ?",
                                 (iso(now_utc()),)).fetchall()
        return self.delete_docs(r[0] for r in rows)

    # ------------------------------------------------------------------ read

    def _filters(self, niche: str | None, language: str | None, kinds: list[str] | None,
                 exclude: Iterable[int], max_age_days: int | None) -> tuple[str, list[Any]]:
        where, args = ["1=1"], []
        if niche:
            where.append("(d.niche = ? OR d.niche = '*')")
            args.append(niche)
        if language:
            where.append("d.language = ?")
            args.append(language)
        if kinds:
            where.append(f"d.kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        ex = list(exclude)
        if ex:
            where.append(f"d.id NOT IN ({','.join('?' * len(ex))})")
            args.extend(ex)
        if max_age_days:
            where.append("d.fetched_at >= ?")
            args.append(iso(now_utc() - timedelta(days=max_age_days)))
        return " AND ".join(where), args

    def _fts_candidates(self, query: str, where: str, args: list[Any], n: int) -> list[int]:
        fq = fts_query(query)
        if not fq:
            return []
        sql = (f"SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id = f.chunk_id JOIN docs d ON d.id = c.doc_id "
               f"WHERE chunks_fts MATCH ? AND {where} ORDER BY bm25(chunks_fts) LIMIT ?")
        with self._lock:
            return [r[0] for r in self.conn.execute(sql, [fq, *args, n])]

    def _vector_candidates(self, query: str, where: str, args: list[Any], n: int,
                           first_chunk_only: bool = False, min_sim: float = -1.0) -> list[tuple[int, float]]:
        if not self.embedder.dim:
            return []
        extra = " AND c.ord = 0" if first_chunk_only else ""
        with self._lock:
            rows = self.conn.execute(
                f"SELECT c.id, c.emb FROM chunks c JOIN docs d ON d.id = c.doc_id "
                f"WHERE c.emb IS NOT NULL AND {where}{extra}", args).fetchall()
        if not rows:
            return []
        mat = np.stack([np.frombuffer(r[1], dtype=np.float16) for r in rows]).astype(np.float32)
        q = self.embedder.encode([query])[0]
        if mat.shape[1] != q.shape[0]:
            return []  # embeddings from a different model; re-index with `shorts kb reindex`
        sims = mat @ q
        order = np.argsort(-sims)[:n]
        return [(int(rows[i][0]), float(sims[i])) for i in order if sims[i] >= min_sim]

    def search(self, query: str, k: int = 6, niche: str | None = None, language: str | None = None,
               kinds: list[str] | None = None, exclude_docs: Iterable[int] = (), max_age_days: int | None = None,
               per_doc: int = 2, min_similarity: float = 0.25) -> list[Hit]:
        """Hybrid retrieval. Vector hits below `min_similarity` are dropped so unrelated notes never reach a
        prompt just because nothing better exists."""
        where, args = self._filters(niche, language, kinds, exclude_docs, max_age_days)
        fused: dict[int, float] = {}
        for rank, cid in enumerate(self._fts_candidates(query, where, args, 50)):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (60 + rank)
        for rank, (cid, _) in enumerate(self._vector_candidates(query, where, args, 50, min_sim=min_similarity)):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (60 + rank)
        if not fused:
            return []
        ids = sorted(fused, key=fused.get, reverse=True)  # type: ignore[arg-type]
        q = ",".join("?" * len(ids))
        with self._lock:
            rows = {r["cid"]: r for r in self.conn.execute(
                f"SELECT c.id AS cid, c.text, d.id AS did, d.kind, d.title, d.url, d.meta FROM chunks c "
                f"JOIN docs d ON d.id = c.doc_id WHERE c.id IN ({q})", ids)}
        hits, per = [], {}
        for cid in ids:
            r = rows.get(cid)
            if r is None or per.get(r["did"], 0) >= per_doc:
                continue
            per[r["did"]] = per.get(r["did"], 0) + 1
            hits.append(Hit(r["did"], cid, r["kind"], r["title"], r["url"], r["text"], fused[cid], json.loads(r["meta"])))
            if len(hits) >= k:
                break
        return hits

    def most_similar(self, text: str, kinds: list[str], niche: str | None = None,
                     language: str | None = None) -> tuple[float, str] | None:
        """Highest cosine similarity between `text` and any doc of these kinds (semantic embedders only)."""
        if not self.embedder.semantic:
            return None
        where, args = self._filters(niche, language, kinds, (), None)
        best = self._vector_candidates(text, where, args, 1, first_chunk_only=True)
        if not best:
            return None
        cid, sim = best[0]
        title = self.conn.execute("SELECT d.title FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE c.id=?",
                                  (cid,)).fetchone()[0]
        return sim, title

    def recent(self, kinds: list[str], niche: str | None = None, language: str | None = None,
               max_age_days: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
        where, args = self._filters(niche, language, kinds, (), max_age_days)
        with self._lock:
            rows = self.conn.execute(
                f"SELECT d.* FROM docs d WHERE {where} ORDER BY d.fetched_at DESC LIMIT ?", [*args, limit * 5]
            ).fetchall()
        out = [dict(r) | {"meta": json.loads(r["meta"])} for r in rows]
        out.sort(key=lambda d: d["meta"].get("score", 0), reverse=True)
        return out[:limit]

    def doc(self, doc_id: int) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
        return dict(r) | {"meta": json.loads(r["meta"])} if r else None

    # ------------------------------------------------------------------ housekeeping

    def scouted(self, channel: str, day: str) -> bool:
        return self.conn.execute("SELECT 1 FROM scout_runs WHERE channel=? AND day=?", (channel, day)).fetchone() is not None

    def mark_scouted(self, channel: str, day: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO scout_runs(channel, day, at) VALUES (?,?,?)",
                              (channel, day, iso(now_utc())))

    def stats(self) -> dict[str, Any]:
        kinds = {r[0]: r[1] for r in self.conn.execute("SELECT kind, COUNT(*) FROM docs GROUP BY kind")}
        chunks = self.conn.execute("SELECT COUNT(*), SUM(emb IS NOT NULL) FROM chunks").fetchone()
        return {"docs": kinds, "chunks": chunks[0], "embedded_chunks": chunks[1] or 0,
                "embedder": type(self.embedder).__name__, "dim": self.embedder.dim}

    def reindex(self) -> int:
        """Recompute every embedding (after switching embedding model)."""
        n = 0
        rows = self.conn.execute("SELECT c.id, c.text, d.title FROM chunks c JOIN docs d ON d.id=c.doc_id").fetchall()
        for i in range(0, len(rows), 64):
            batch = rows[i:i + 64]
            vecs = self.embedder.encode([f"{r['title']}\n{r['text']}" for r in batch]) if self.embedder.dim else None
            with self._lock, self.conn:
                for j, r in enumerate(batch):
                    emb = vecs[j].astype(np.float16).tobytes() if vecs is not None else None
                    self.conn.execute("UPDATE chunks SET emb=? WHERE id=?", (emb, r["id"]))
                    n += 1
        return n

    def backup(self, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(dst)
            self.conn.backup(target)
            target.close()
        return dst
