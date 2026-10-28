-- Durable knowledge-workspace documents (uploads for retrieval).
-- Mirrors the DDL in backend/rag/store.py, which also applies it idempotently
-- at first use, so applying this by hand is optional:
--   turso db shell <db-name> < migrations/002_knowledge.sql
-- Chunks and the retrieval index are rebuilt in memory from `text`.

CREATE TABLE IF NOT EXISTS knowledge_docs (
  workspace_id TEXT NOT NULL,
  doc_id       TEXT NOT NULL,
  name         TEXT NOT NULL,
  text         TEXT NOT NULL,
  chars        INTEGER NOT NULL,
  chunks       INTEGER NOT NULL,
  added_at     TEXT NOT NULL,
  PRIMARY KEY (workspace_id, doc_id)
);
CREATE INDEX IF NOT EXISTS knowledge_docs_ws ON knowledge_docs (workspace_id, added_at);
