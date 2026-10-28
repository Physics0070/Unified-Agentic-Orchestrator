"""Durable storage for knowledge-workspace documents.

Uses the same database as the EventStore: Turso (`turso_serverless`) when
TURSO_DATABASE_URL is a remote URL, otherwise the stdlib sqlite3 fallback
(blank or `file:` URL). Only documents (name + text) are stored; chunks and the
retrieval index are rebuilt in memory per workspace on first access, which is
deterministic and keeps the schema trivial.

The table is created idempotently at first use (CREATE TABLE IF NOT EXISTS), so
Turso users do not have to apply migrations/002_knowledge.sql by hand - that file
mirrors `_DDL` below for anyone who prefers to.

Each operation opens a short-lived connection: serverless instances come and go,
and the Turso driver is plain HTTP with nothing worth pooling.
"""

import logging
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from backend.events.store import _sqlite_path

log = logging.getLogger("aco.rag")

_DDL = (
    "CREATE TABLE IF NOT EXISTS knowledge_docs ("
    " workspace_id TEXT NOT NULL,"
    " doc_id TEXT NOT NULL,"
    " name TEXT NOT NULL,"
    " text TEXT NOT NULL,"
    " chars INTEGER NOT NULL,"
    " chunks INTEGER NOT NULL,"
    " added_at TEXT NOT NULL,"
    " PRIMARY KEY (workspace_id, doc_id))",
    "CREATE INDEX IF NOT EXISTS knowledge_docs_ws ON knowledge_docs (workspace_id, added_at)",
)

_MIGRATED: set[str] = set()
_LOCK = threading.Lock()


class KnowledgeStoreError(RuntimeError):
    """Persistence failed. Message is safe to log."""


class KnowledgeStore:
    def __init__(self, url: str, token: str = "") -> None:
        self._url = url
        self._token = token
        self._sqlite = not url or url.startswith("file:")
        self._path = _sqlite_path(url or "") if self._sqlite else ""
        if not self._sqlite and not token:
            raise KnowledgeStoreError("TURSO_AUTH_TOKEN must be set")

    def _connect(self):
        if self._sqlite:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(self._path, timeout=30)
            c.execute("PRAGMA journal_mode = WAL")
            return c
        import turso_serverless

        return turso_serverless.connect(self._url, auth_token=self._token)

    def _with_conn(self, fn):
        key = self._path or self._url
        try:
            with closing(self._connect()) as c:
                if key not in _MIGRATED:
                    with _LOCK:
                        for stmt in _DDL:
                            c.execute(stmt)
                        c.commit()
                        _MIGRATED.add(key)
                return fn(c)
        except KnowledgeStoreError:
            raise
        except Exception as e:  # sqlite3.Error, turso dbapi errors, OSError
            log.error("knowledge store error: %s: %s", type(e).__name__, e)
            raise KnowledgeStoreError(f"knowledge storage failed ({type(e).__name__})") from e

    def load(self, workspace_id: str) -> list[dict]:
        def _do(c):
            rows = list(c.execute(
                "SELECT doc_id, name, text, chars, chunks, added_at FROM knowledge_docs "
                "WHERE workspace_id = ? ORDER BY added_at, doc_id", [workspace_id]))
            c.commit()
            return [{"doc_id": r[0], "name": r[1], "text": r[2], "chars": r[3],
                     "chunks": r[4], "added_at": r[5]} for r in rows]

        return self._with_conn(_do)

    def replace(self, workspace_id: str, remove_ids: list[str], doc: dict | None) -> None:
        """Delete `remove_ids` and insert `doc` (if any) in one transaction."""
        def _do(c):
            for rid in remove_ids:
                c.execute("DELETE FROM knowledge_docs WHERE workspace_id = ? AND doc_id = ?",
                          [workspace_id, rid])
            if doc is not None:
                c.execute(
                    "INSERT INTO knowledge_docs (workspace_id, doc_id, name, text, chars, "
                    "chunks, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [workspace_id, doc["doc_id"], doc["name"], doc["text"], doc["chars"],
                     doc["chunks"], doc["added_at"]])
            c.commit()

        self._with_conn(_do)
