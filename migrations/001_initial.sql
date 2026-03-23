-- Migration: 001_initial.sql
-- Agent memory system schema

CREATE EXTENSION IF NOT EXISTS vector;

-- Track applied migrations
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    VARCHAR(255) PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Core memories table
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        VARCHAR(64) NOT NULL,
    memory_type     VARCHAR(32) NOT NULL DEFAULT 'episodic',
    content         TEXT NOT NULL,
    embedding       vector(768),
    importance      FLOAT DEFAULT 0.5,
    tags            TEXT[],
    source_session  VARCHAR(128),
    shared          BOOLEAN DEFAULT FALSE,
    shared_by       VARCHAR(64),
    provenance      JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_memories_agent_time ON memories(agent_id, created_at DESC);
CREATE INDEX idx_memories_type ON memories(agent_id, memory_type);
CREATE INDEX idx_memories_tags ON memories USING gin(tags);
CREATE INDEX idx_memories_shared ON memories(shared) WHERE shared = TRUE;

-- HNSW index for vector similarity search (created empty, populated later)
CREATE INDEX idx_memories_embedding ON memories
    USING hnsw(embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Shared event bus (append-only, inter-agent coordination)
CREATE TABLE shared_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  VARCHAR(64) NOT NULL,
    payload     JSONB,
    created_by  VARCHAR(64),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_events_type_time ON shared_events(event_type, created_at DESC);

-- Record this migration
INSERT INTO schema_migrations (filename) VALUES ('001_initial.sql')
ON CONFLICT (filename) DO NOTHING;
