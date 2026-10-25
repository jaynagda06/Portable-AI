"""Local document library: extraction, chunking, SQLite FTS5 + optional vectors.

Search is hybrid:
  * BM25 full-text (always available, zero extra model)
  * cosine similarity over embeddings (only if the bundled embedding server runs)
Results are fused with Reciprocal Rank Fusion, so either signal alone still works.
"""
from __future__ import annotations

import array
import math
import re
import sqlite3
import threading
from pathlib import Path

from . import extract, paths, util

_STOP = set("""a an and are as at be by for from has have how in is it its of on or that the
to was were what when where which who why will with you your this these those i we they""".split())


class Library:
    def __init__(self, cfg: dict, embedder=None):
        self.cfg = cfg
        self.embedder = embedder  # callable(list[str]) -> list[list[float]] | None
        self.db_path = paths.INDEX_DIR / "library.db"
        self._local = threading.local()
        paths.INDEX_DIR.mkdir(parents=True, exist_ok=True)
        self._init()

    # ---------- connection ----------
    def _db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init(self) -> None:
        with self._db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS docs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                name TEXT, ext TEXT, size INTEGER, mtime REAL, sha256 TEXT,
                chars INTEGER DEFAULT 0, chunks INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ok', error TEXT, indexed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL,
                seq INTEGER, page INTEGER, text TEXT, emb BLOB
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2');
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            """)

    # ---------- chunking ----------
    def _chunk(self, text: str) -> list[str]:
        size = int(self.cfg["rag"]["chunk_chars"])
        overlap = int(self.cfg["rag"]["overlap"])
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks, cur = [], ""
        for p in paras:
            if len(cur) + len(p) + 2 <= size:
                cur = f"{cur}\n\n{p}" if cur else p
                continue
            if cur:
                chunks.append(cur)
                cur = (cur[-overlap:] + "\n\n" + p) if overlap and len(cur) > overlap else p
            else:
                # a single huge paragraph: hard-split on sentence boundaries
                while len(p) > size:
                    cut = p.rfind(". ", 0, size)
                    cut = cut + 1 if cut > size * 0.5 else size
                    chunks.append(p[:cut].strip())
                    p = p[max(0, cut - overlap):]
                cur = p
        if cur.strip():
            chunks.append(cur.strip())
        return [c for c in chunks if len(c.strip()) >= 20]

    # ---------- indexing ----------
    def add_file(self, path: str | Path, force: bool = False) -> dict:
        path = Path(path)
        if not path.is_file():
            return {"ok": False, "path": str(path), "error": "not a file"}
        if not extract.is_supported(path):
            return {"ok": False, "path": str(path), "error": f"unsupported type {path.suffix or '(none)'}"}
        st = path.stat()
        if st.st_size > int(self.cfg["rag"]["max_file_mb"]) * 1024 * 1024:
            return {"ok": False, "path": str(path),
                    "error": f"larger than {self.cfg['rag']['max_file_mb']} MB limit"}
        sha = util.sha256_file(path)
        db = self._db()
        row = db.execute("SELECT * FROM docs WHERE path=?", (str(path),)).fetchone()
        if row and not force and row["sha256"] == sha and row["status"] == "ok":
            return {"ok": True, "skipped": True, "path": str(path), "name": path.name,
                    "chunks": row["chunks"], "doc_id": row["id"]}

        try:
            blocks = extract.extract(path)
        except Exception as exc:
            self._record_error(str(path), path, sha, exc)
            return {"ok": False, "path": str(path), "error": f"{exc.__class__.__name__}: {exc}"}

        if not blocks:
            self._record_error(str(path), path, sha, ValueError("no extractable text (scanned PDF?)"))
            return {"ok": False, "path": str(path),
                    "error": "no extractable text (scanned/image-only PDF needs OCR, which is not bundled)"}

        payload = []  # (seq, page, text)
        seq = 0
        for b in blocks:
            for ch in self._chunk(b["text"]):
                payload.append((seq, b.get("page"), ch))
                seq += 1
        if not payload:
            self._record_error(str(path), path, sha, ValueError("text too short"))
            return {"ok": False, "path": str(path), "error": "text too short to index"}

        emb = None
        if self.embedder:
            try:
                emb = self.embedder([p[2] for p in payload])
            except Exception as exc:
                print(f"[rag] embedding failed, indexing text only: {exc}")
                emb = None

        with db:
            if row:
                db.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id=?)", (row["id"],))
                db.execute("DELETE FROM chunks WHERE doc_id=?", (row["id"],))
                db.execute("""UPDATE docs SET name=?,ext=?,size=?,mtime=?,sha256=?,chars=?,chunks=?,
                              status='ok',error=NULL,indexed_at=? WHERE id=?""",
                           (path.name, path.suffix.lower(), st.st_size, st.st_mtime, sha,
                            sum(len(p[2]) for p in payload), len(payload), util.now_iso(), row["id"]))
                doc_id = row["id"]
            else:
                cur = db.execute("""INSERT INTO docs(path,name,ext,size,mtime,sha256,chars,chunks,status,indexed_at)
                                    VALUES(?,?,?,?,?,?,?,?, 'ok', ?)""",
                                 (str(path), path.name, path.suffix.lower(), st.st_size, st.st_mtime, sha,
                                  sum(len(p[2]) for p in payload), len(payload), util.now_iso()))
                doc_id = cur.lastrowid
            for i, (sq, page, text) in enumerate(payload):
                blob = None
                if emb and i < len(emb) and emb[i]:
                    blob = _pack_vec(emb[i])
                cur = db.execute("INSERT INTO chunks(doc_id,seq,page,text,emb) VALUES(?,?,?,?,?)",
                                 (doc_id, sq, page, text, blob))
                db.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)", (cur.lastrowid, text))
        return {"ok": True, "path": str(path), "name": path.name, "chunks": len(payload),
                "doc_id": doc_id, "embedded": bool(emb)}

    def _record_error(self, key: str, path: Path, sha: str, exc: Exception) -> None:
        db = self._db()
        with db:
            row = db.execute("SELECT id FROM docs WHERE path=?", (key,)).fetchone()
            if row:
                db.execute("UPDATE docs SET status='error',error=?,indexed_at=? WHERE id=?",
                           (f"{exc.__class__.__name__}: {exc}", util.now_iso(), row["id"]))
            else:
                db.execute("""INSERT INTO docs(path,name,ext,size,mtime,sha256,status,error,indexed_at)
                              VALUES(?,?,?,?,?,?,'error',?,?)""",
                           (key, path.name, path.suffix.lower(), path.stat().st_size if path.exists() else 0,
                            0, sha, f"{exc.__class__.__name__}: {exc}", util.now_iso()))

    def index_folder(self, folder: str, progress=None, max_files: int = 5000) -> dict:
        root = Path(folder)
        if not root.is_dir():
            return {"ok": False, "error": "not a folder"}
        added = skipped = failed = 0
        errors = []
        count = 0
        for p in root.rglob("*"):
            if count >= max_files:
                break
            try:
                if not p.is_file() or extract.should_skip_dir(p.parent.name):
                    continue
                if any(extract.should_skip_dir(part) for part in p.relative_to(root).parts[:-1]):
                    continue
                if not extract.is_supported(p):
                    continue
            except (OSError, ValueError):
                continue
            count += 1
            if progress:
                progress(count, str(p))
            r = self.add_file(p)
            if r.get("ok") and r.get("skipped"):
                skipped += 1
            elif r.get("ok"):
                added += 1
            else:
                failed += 1
                if len(errors) < 20:
                    errors.append({"path": str(p), "error": r.get("error")})
        return {"ok": True, "added": added, "skipped": skipped, "failed": failed,
                "seen": count, "errors": errors}

    def remove(self, doc_id: int) -> dict:
        db = self._db()
        with db:
            db.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
            db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            db.execute("DELETE FROM docs WHERE id=?", (doc_id,))
        return {"ok": True}

    def list_docs(self) -> list[dict]:
        rows = self._db().execute(
            "SELECT id,name,path,ext,size,chunks,status,error,indexed_at FROM docs ORDER BY indexed_at DESC"
        ).fetchall()
        return [{**dict(r), "size_h": util.human(r["size"]),
                 "size": r["size"]} for r in rows]

    # ---------- search ----------
    def _fts_query(self, q: str) -> str:
        toks = [t for t in re.findall(r"[\w\u00c0-\u024f]{2,}", q.lower()) if t not in _STOP][:12]
        toks = [t.replace('"', "") for t in toks]
        return " OR ".join(f'"{t}"' for t in toks) if toks else '""'

    def search(self, q: str, k: int | None = None) -> dict:
        k = int(k or self.cfg["rag"]["top_k"])
        q = (q or "").strip()
        if not q:
            return {"ok": True, "query": q, "results": [], "mode": "none"}
        db = self._db()
        fts = self._fts_query(q)
        bm25_rows = []
        try:
            bm25_rows = db.execute(
                """SELECT c.id, c.doc_id, c.page, c.text, d.name, d.path, bm25(chunks_fts) AS score
                   FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                   JOIN docs d ON d.id = c.doc_id
                   WHERE chunks_fts MATCH ? ORDER BY score LIMIT 80""", (fts,)).fetchall()
        except sqlite3.OperationalError:
            bm25_rows = []

        vec_scores: dict[int, float] = {}
        vec_ok = False
        if self.embedder:
            try:
                qv = self.embedder([q], query=True)
                if qv:
                    qvec = qv[0]
                    qn = math.sqrt(sum(x * x for x in qvec)) or 1.0
                    qn_vec = [x / qn for x in qvec]
                    for r in db.execute("SELECT id, emb FROM chunks WHERE emb IS NOT NULL"):
                        v = _unpack_vec(r["emb"])
                        vec_scores[r["id"]] = sum(a * b for a, b in zip(qn_vec, v))
                    vec_ok = bool(vec_scores)
            except Exception as exc:
                print(f"[rag] vector search unavailable: {exc}")

        ranks: dict[int, float] = {}
        for i, r in enumerate(bm25_rows):
            ranks[r["id"]] = ranks.get(r["id"], 0) + 1.0 / (60 + i + 1)
        if vec_ok:
            for i, (cid, _s) in enumerate(sorted(vec_scores.items(), key=lambda x: -x[1])[:80]):
                ranks[cid] = ranks.get(cid, 0) + 1.0 / (60 + i + 1)

        ids = [cid for cid, _ in sorted(ranks.items(), key=lambda x: -x[1])[:k]]
        if not ids:
            return {"ok": True, "query": q, "results": [], "mode": "hybrid" if vec_ok else "keyword"}
        out = []
        for cid in ids:
            r = db.execute("""SELECT c.id, c.doc_id, c.page, c.text, d.name, d.path
                              FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE c.id=?""", (cid,)).fetchone()
            if not r:
                continue
            out.append({
                "id": r["id"], "doc_id": r["doc_id"], "name": r["name"], "path": r["path"],
                "page": r["page"], "score": round(ranks[cid], 5),
                "snippet": _snippet(r["text"], q),
                "text": r["text"][:2200],
            })
        return {"ok": True, "query": q, "results": out, "mode": "hybrid" if vec_ok else "keyword"}

    def stats(self) -> dict:
        db = self._db()
        d = db.execute("SELECT COUNT(*) n, COALESCE(SUM(chunks),0) c, COALESCE(SUM(size),0) s FROM docs").fetchone()
        emb = db.execute("SELECT COUNT(*) n FROM chunks WHERE emb IS NOT NULL").fetchone()["n"]
        errs = db.execute("SELECT COUNT(*) n FROM docs WHERE status='error'").fetchone()["n"]
        return {"documents": d["n"], "chunks": d["c"], "bytes": d["s"],
                "bytes_h": util.human(d["s"]), "embedded_chunks": emb,
                "error_documents": errs,
                "db_size_h": util.human(self.db_path.stat().st_size) if self.db_path.exists() else "0 B",
                "embedding_enabled": bool(self.embedder)}

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()


def _pack_vec(vec: list[float]) -> bytes:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array.array("f", [x / n for x in vec]).tobytes()


def _unpack_vec(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def _snippet(text: str, q: str, width: int = 260) -> str:
    low = text.lower()
    pos = -1
    for tok in re.findall(r"[\w\u00c0-\u024f]{3,}", q.lower()):
        pos = low.find(tok)
        if pos >= 0:
            break
    if pos < 0:
        return text[:width].replace("\n", " ").strip()
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    s = text[start:end].replace("\n", " ").strip()
    return ("… " if start else "") + s + (" …" if end < len(text) else "")
