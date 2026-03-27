#!/usr/bin/env bash
# pg-backup.sh — Nightly pg_dump of agent_memory to local backup dir
# Preserves embeddings, tsvectors, indexes — faster recovery than JSONL rebuild.
# Run via cron on workstation: 0 4 * * * /home/jean-marc/Code/multi-agent-memory/scripts/pg-backup.sh

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/agent-memory}"
PG_DB="agent_memory"
PG_USER="memory_user"
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

DUMP_FILE="$BACKUP_DIR/${PG_DB}_$(date +%Y%m%d_%H%M%S).dump"

PGPASSWORD=***REDACTED*** pg_dump -Fc -U "$PG_USER" -h localhost "$PG_DB" > "$DUMP_FILE"

SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "$(date -Iseconds) Backup complete: $DUMP_FILE ($SIZE)"

# Rotate: delete backups older than KEEP_DAYS
find "$BACKUP_DIR" -name "*.dump" -mtime +$KEEP_DAYS -delete

REMAINING=$(find "$BACKUP_DIR" -name "*.dump" | wc -l)
echo "$(date -Iseconds) Retention: $REMAINING backups kept (${KEEP_DAYS}d policy)"
