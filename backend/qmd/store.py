"""QMD Store - Core search engine for LANDrop.

Provides hybrid search (BM25 + vector + RRF fusion) with sentence-transformers
embeddings and smart document chunking.
"""

import json
import sqlite3
import numpy as np
from pathlib import Path
from typing import Optional

from backend.qmd.models import embed_text, embed_batch, get_embed_dim
from backend.qmd.chunker import chunk_document, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS


class QMDStore:
    """Search engine wrapping LANDrop's SQLite database with QMD-style retrieval."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._ensure_schema()
        self._check_embedding_dim()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self):
        """Ensure chunks table exists for smart chunking."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                chunk_pos INTEGER DEFAULT 0,
                vector BLOB,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks(item_id);
        """)
        conn.commit()
        conn.close()

    def _check_embedding_dim(self):
        """Check if stored embeddings match current model. Re-embed if model changed."""
        conn = self._get_conn()
        try:
            # 检查 embedding 模型版本（维度相同时也需要 re-embed，因为不同模型的向量空间不同）
            conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            row = conn.execute("SELECT value FROM metadata WHERE key='embed_model'").fetchone()
            current_model = row["value"] if row else ""

            # 检查维度
            vec_row = conn.execute("SELECT vector FROM embeddings LIMIT 1").fetchone()
            new_dim = get_embed_dim()
            dim_mismatch = False
            if vec_row and vec_row[0]:
                old_dim = np.frombuffer(vec_row[0], dtype=np.float32).shape[0]
                dim_mismatch = (old_dim != new_dim)

            if current_model != "MiniLM-L12-v2" or dim_mismatch:
                reason = f"model: {current_model!r} -> MiniLM-L12-v2" if current_model != "MiniLM-L12-v2" else f"dim mismatch"
                print(f"[QMD] Embedding changed ({reason}), re-embedding all items...", flush=True)
                self._re_embed_all(conn, new_dim)
                conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('embed_model','MiniLM-L12-v2')")
                conn.commit()
        finally:
            conn.close()

    def _re_embed_all(self, conn: sqlite3.Connection, dim: int):
        """Re-embed all items and chunks with the new model."""
        # Re-embed items
        items = conn.execute("SELECT id, title, content, summary FROM items").fetchall()
        if items:
            texts = []
            valid_items = []
            for item in items:
                embed_text_str = f"{item['title'] or ''}\n{item['summary'] or ''}\n{(item['content'] or '')[:2000]}"
                texts.append(embed_text_str)
                valid_items.append(item)

            print(f"[QMD] Re-embedding {len(texts)} items...", flush=True)
            embeddings = embed_batch(texts)
            for item, vec in zip(valid_items, embeddings):
                conn.execute("UPDATE embeddings SET vector = ? WHERE item_id = ?", (vec.tobytes(), item['id']))

        # Re-embed chunks
        chunks = conn.execute("SELECT id, chunk_text FROM chunks WHERE chunk_text != ''").fetchall()
        if chunks:
            chunk_texts = [c['chunk_text'] for c in chunks]
            print(f"[QMD] Re-embedding {len(chunk_texts)} chunks...", flush=True)
            chunk_embeddings = embed_batch(chunk_texts)
            for chunk, vec in zip(chunks, chunk_embeddings):
                conn.execute("UPDATE chunks SET vector = ? WHERE id = ?", (vec.tobytes(), chunk['id']))

        conn.commit()
        print(f"[QMD] Re-embedding complete ({len(items or [])} items, {len(chunks or [])} chunks).", flush=True)

    # -------------------------------------------------------------------------
    # Indexing
    # -------------------------------------------------------------------------

    def index_item(self, item_id: str, title: str, content: str, summary: str = ""):
        """Index an item: compute embedding, create chunks, store vectors."""
        conn = self._get_conn()

        # Compute embedding
        embed_text_str = f"{title or ''}\n{summary or ''}\n{(content or '')[:2000]}"
        vec = embed_text(embed_text_str)

        # Store embedding (upsert)
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (item_id, vector) VALUES (?, ?)",
            (item_id, vec.tobytes())
        )

        # Smart chunking
        conn.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
        if content:
            chunks = chunk_document(content, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS)
            for i, chunk in enumerate(chunks):
                chunk_vec = embed_text(chunk["text"])
                conn.execute(
                    "INSERT INTO chunks (item_id, chunk_index, chunk_text, chunk_pos, vector) VALUES (?, ?, ?, ?, ?)",
                    (item_id, i, chunk["text"], chunk["pos"], chunk_vec.tobytes())
                )

        conn.commit()
        conn.close()

    def remove_item(self, item_id: str):
        """Remove an item's embeddings and chunks."""
        conn = self._get_conn()
        conn.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM embeddings WHERE item_id = ?", (item_id,))
        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------

    def search(self, query: str, top_k: int = 8, rerank: bool = False) -> list:
        """Hybrid search: BM25 + vector + chunk + optional HyDE, with RRF fusion.

        This is the main search entrypoint, matching the old hybrid_search interface.
        """
        # Phase 1: Multi-path retrieval
        semantic = self.search_vec(query, top_k=20)
        keyword = self.search_fts(query, top_k=20)
        chunk_results = self.search_chunks(query, top_k=20)

        # Phase 2: RRF fusion
        rrf_score: dict[str, float] = {}
        item_map: dict[str, dict] = {}
        k_constant = 60

        for signal_name, signal_results in [
            ("vec", semantic), ("bm25", keyword), ("chunk", chunk_results)
        ]:
            for rank, item in enumerate(signal_results):
                iid = item["id"]
                if iid not in item_map:
                    item_map[iid] = item
                rrf_score.setdefault(iid, 0.0)
                rrf_score[iid] += 1.0 / (k_constant + rank + 1)

        sorted_ids = sorted(rrf_score.keys(), key=lambda x: rrf_score[x], reverse=True)
        merged = [item_map[iid] for iid in sorted_ids]

        return merged[:top_k]

    def search_vec(self, query: str, top_k: int = 20) -> list:
        """Vector similarity search using GGUF embeddings."""
        query_vec = embed_text(query)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        conn = self._get_conn()
        rows = conn.execute("""
            SELECT i.*, e.vector
            FROM items i
            JOIN embeddings e ON i.id = e.item_id
        """).fetchall()
        conn.close()

        valid_items = []
        valid_vectors = []
        dim = get_embed_dim()

        for row in rows:
            vec = np.frombuffer(row["vector"], dtype=np.float32)
            if vec.shape[0] == dim:
                valid_items.append(dict(row))
                valid_vectors.append(vec)

        if not valid_vectors:
            return []

        matrix = np.array(valid_vectors)
        matrix_norms = np.linalg.norm(matrix, axis=1)
        matrix_norms[matrix_norms == 0] = 1e-10
        sims = np.dot(matrix, query_vec) / (query_norm * matrix_norms)

        valid_indices = np.where(sims > 0.30)[0]
        sorted_indices = valid_indices[np.argsort(sims[valid_indices])[::-1]][:top_k]

        results = []
        for i in sorted_indices:
            d = valid_items[i]
            d["vector_score"] = float(sims[i])
            results.append(d)
        return results

    def search_fts(self, query: str, top_k: int = 20) -> list:
        """BM25 full-text search via SQLite FTS5 with CJK-aware tokenization."""
        import re as re_mod

        conn = self._get_conn()

        # Split CJK characters individually for FTS5 unicode61 tokenizer
        tokens = []
        for word in query.split():
            parts = re_mod.findall(r'[一-鿿]|[^一-鿿\s]+', word)
            tokens.extend(parts)

        search_term = ' OR '.join([f'"{t}"*' for t in tokens if t.strip()])
        if not search_term:
            search_term = f'"{query}"*'

        try:
            items = conn.execute("""
                SELECT i.* FROM items_fts fts
                JOIN items i ON i.id = fts.id
                WHERE items_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (search_term, top_k)).fetchall()
        except Exception as e:
            print(f"[QMD] FTS search error, falling back to LIKE: {e}", flush=True)
            items = conn.execute("""
                SELECT * FROM items
                WHERE content LIKE ? OR title LIKE ? OR tags LIKE ? OR summary LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", top_k)).fetchall()

        conn.close()
        return [dict(r) for r in items]

    def search_chunks(self, query: str, top_k: int = 20) -> list:
        """Chunk-level vector search: search document chunks, aggregate by parent item."""
        query_vec = embed_text(query)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        conn = self._get_conn()
        chunks = conn.execute("""
            SELECT c.item_id, c.chunk_text, c.vector, i.*
            FROM chunks c
            JOIN items i ON i.id = c.item_id
            WHERE c.vector IS NOT NULL
        """).fetchall()
        conn.close()

        if not chunks:
            return []

        dim = get_embed_dim()
        chunk_scores: dict[str, float] = {}
        item_map: dict[str, dict] = {}

        for chunk in chunks:
            vec = np.frombuffer(chunk["vector"], dtype=np.float32)
            if vec.shape[0] != dim:
                continue

            sim = float(np.dot(query_vec, vec) / (query_norm * np.linalg.norm(vec) + 1e-10))
            iid = chunk["item_id"]
            if iid not in item_map:
                item_map[iid] = dict(chunk)
            if sim > chunk_scores.get(iid, 0):
                chunk_scores[iid] = sim
                item_map[iid]["match_chunk"] = chunk["chunk_text"]

        sorted_items = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for iid, score in sorted_items[:top_k]:
            if score > 0.30:
                d = item_map[iid]
                d["chunk_score"] = score
                results.append(d)
        return results


# Singleton
_store: Optional[QMDStore] = None


def get_store(db_path: str | Path | None = None) -> QMDStore:
    """Get or create the singleton QMDStore."""
    global _store
    if _store is None:
        if db_path is None:
            from backend.database import DB_PATH
            db_path = DB_PATH
        _store = QMDStore(db_path)
    return _store
