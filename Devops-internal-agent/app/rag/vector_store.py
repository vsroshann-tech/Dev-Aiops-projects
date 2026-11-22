"""Persistent SQLite-backed vector store for internal documentation RAG."""

import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.rag.chunker import chunk_document, extract_text_from_bytes
from app.rag.embeddings import embed_text, expand_query_tokens, tokenize


@dataclass
class RetrievedChunk:
    """A RAG search hit with citation metadata."""

    doc_id: str
    filename: str
    classification: str
    heading: str
    chunk_index: int
    content: str
    score: float


class LocalVectorStore:
    """SQLite persistent vector store with hybrid semantic + keyword retrieval."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    classification TEXT NOT NULL DEFAULT 'internal',
                    uploaded_by TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    heading TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
                """
            )

    def ingest_bytes(
        self,
        filename: str,
        raw_bytes: bytes,
        uploaded_by: str,
        classification: str = "internal",
        redact_before_index: bool = True,
    ) -> Dict[str, Any]:
        """Parse, redact, chunk, embed, and persist an uploaded internal document."""
        text = extract_text_from_bytes(filename, raw_bytes)
        chunks, redactions = chunk_document(text, redact=redact_before_index)
        if not chunks:
            raise ValueError(f"Document '{filename}' yielded no indexable text.")

        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        doc_id = str(uuid.uuid4())[:12]
        uploaded_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            # Replace existing document with the same filename so re-uploads stay clean
            existing = conn.execute(
                "SELECT doc_id FROM documents WHERE filename = ?", (filename,)
            ).fetchall()
            for row in existing:
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (row["doc_id"],))
                conn.execute("DELETE FROM documents WHERE doc_id = ?", (row["doc_id"],))

            conn.execute(
                """
                INSERT INTO documents (doc_id, filename, classification, uploaded_by, uploaded_at, sha256, chunk_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, filename, classification, uploaded_by, uploaded_at, sha256, len(chunks)),
            )

            for ch in chunks:
                emb = embed_text(f"{ch.heading}\n{ch.content}")
                conn.execute(
                    """
                    INSERT INTO chunks (chunk_id, doc_id, chunk_index, heading, content, embedding_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{doc_id}-{ch.chunk_index}",
                        doc_id,
                        ch.chunk_index,
                        ch.heading,
                        ch.content,
                        json.dumps(emb),
                    ),
                )

        return {
            "doc_id": doc_id,
            "filename": filename,
            "classification": classification,
            "chunk_count": len(chunks),
            "sha256": sha256,
            "redactions_applied": redactions,
            "uploaded_at": uploaded_at,
        }

    def search(
        self,
        query: str,
        top_k: int = 4,
        classification: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        """Retrieve top-k relevant document chunks using hybrid cosine + keyword scoring."""
        q_emb = embed_text(query)
        core_tokens = set(tokenize(query, remove_stopwords=True))
        expanded_tokens = set(expand_query_tokens(query))

        sql = """
            SELECT c.doc_id, d.filename, d.classification, c.heading, c.chunk_index, c.content, c.embedding_json
            FROM chunks c
            JOIN documents d ON c.doc_id = d.doc_id
        """
        params: List[Any] = []
        if classification:
            sql += " WHERE d.classification = ?"
            params.append(classification)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        scored: List[RetrievedChunk] = []
        for row in rows:
            c_emb = json.loads(row["embedding_json"])
            cos = _cosine_similarity(q_emb, c_emb)
            heading_tokens = set(tokenize(row["heading"], remove_stopwords=True))
            content_tokens = set(tokenize(row["content"], remove_stopwords=True))
            all_tokens = heading_tokens | content_tokens

            core_overlap = (
                len(core_tokens & all_tokens) / max(1, len(core_tokens))
                if core_tokens
                else 0.0
            )
            syn_overlap = (
                len(expanded_tokens & all_tokens) / max(1, len(expanded_tokens))
                if expanded_tokens
                else 0.0
            )
            heading_boost = 0.15 if (core_tokens & heading_tokens) else 0.0
            hybrid = 0.45 * cos + 0.40 * core_overlap + 0.15 * syn_overlap + heading_boost
            scored.append(
                RetrievedChunk(
                    doc_id=row["doc_id"],
                    filename=row["filename"],
                    classification=row["classification"],
                    heading=row["heading"],
                    chunk_index=row["chunk_index"],
                    content=row["content"],
                    score=round(hybrid, 4),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def list_documents(self) -> List[Dict[str, Any]]:
        """List all indexed internal documents with metadata."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, filename, classification, uploaded_by, uploaded_at, sha256, chunk_count
                FROM documents
                ORDER BY uploaded_at DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document and its indexed chunks by doc_id."""
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            cur = conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        return cur.rowcount > 0

    def clear_all_documents(self) -> int:
        """Delete all documents and chunks from the RAG store, returning deleted count."""
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks")
            cur = conn.execute("DELETE FROM documents")
        return cur.rowcount


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
