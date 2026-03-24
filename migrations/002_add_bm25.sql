-- Migration: 002_add_bm25.sql
-- Add BM25 full-text search capability (keyword search alongside semantic)

ALTER TABLE memories
  ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX idx_memories_search_vector ON memories USING gin(search_vector);

INSERT INTO schema_migrations (filename) VALUES ('002_add_bm25.sql')
ON CONFLICT (filename) DO NOTHING;
