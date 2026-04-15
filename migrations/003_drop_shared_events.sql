-- Migration: 003_drop_shared_events.sql
-- Remove unused shared_events table.
--
-- Grepped full repo (2026-04-15): zero code readers or writers. Added in
-- 001_initial.sql as a speculative inter-agent coordination channel; no
-- consumer materialised. Cross-agent sharing happens via `memories.shared`
-- instead. Confirmed unused by JM before dropping.

DROP TABLE IF EXISTS shared_events CASCADE;

INSERT INTO schema_migrations (filename) VALUES ('003_drop_shared_events.sql')
ON CONFLICT (filename) DO NOTHING;
