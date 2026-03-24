#!/usr/bin/env bash
# memory-stats.sh — Monitoring baseline for multi-agent-memory
# Usage: ./scripts/memory-stats.sh
# Requires: PGPASSWORD set or ~/.pgpass configured

set -euo pipefail

PG_HOST="${PG_HOST:-localhost}"
PG_USER="${PG_USER:-memory_user}"
PG_DB="${PG_DB:-agent_memory}"

q() { PGPASSWORD="${PGPASSWORD:-***REDACTED***}" psql -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" --no-align --tuples-only -c "$1" 2>/dev/null; }
qf() { PGPASSWORD="${PGPASSWORD:-***REDACTED***}" psql -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" -c "$1" 2>/dev/null; }

echo "=== Memory System Status — $(date '+%Y-%m-%d %H:%M %Z') ==="
echo ""

echo "--- Memory Counts ---"
qf "SELECT agent_id, memory_type,
       count(*) FILTER (WHERE NOT shared) as private,
       count(*) FILTER (WHERE shared) as shared,
       count(*) as total
     FROM memories
     GROUP BY agent_id, memory_type
     ORDER BY agent_id, memory_type;"

echo "--- Extraction Stats ---"
qf "SELECT agent_id,
       count(*) as total,
       count(*) FILTER (WHERE provenance->>'extraction_status' = 'success') as extracted,
       count(*) FILTER (WHERE provenance->>'extraction_status' = 'skipped') as skipped,
       count(*) FILTER (WHERE provenance->>'extraction_status' IS NULL) as no_provenance
     FROM memories
     WHERE memory_type = 'episodic'
     GROUP BY agent_id
     ORDER BY agent_id;"

echo "--- Recent Activity (last 24h) ---"
qf "SELECT agent_id,
       count(*) as new_memories,
       min(created_at)::timestamp(0) as earliest,
       max(created_at)::timestamp(0) as latest
     FROM memories
     WHERE created_at > now() - interval '24 hours'
     GROUP BY agent_id
     ORDER BY agent_id;"

echo "--- Shared Namespace ---"
qf "SELECT shared_by as source_agent,
       count(*) as shared_count,
       max(created_at)::timestamp(0) as latest_share
     FROM memories
     WHERE shared = true
     GROUP BY shared_by
     ORDER BY shared_by;"

echo "--- Storage Health ---"
MCP_STATUS=$(curl -sD /tmp/mcp-stat-h.txt http://192.168.10.24:8888/mcp -X POST \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"stats","version":"1.0"}}}' 2>/dev/null)
MCP_SID=$(grep -oP 'mcp-session-id: \K[^\r]+' /tmp/mcp-stat-h.txt 2>/dev/null || echo "")

if [ -n "$MCP_SID" ]; then
  STATUS=$(curl -s http://192.168.10.24:8888/mcp -X POST \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "Mcp-Session-Id: $MCP_SID" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_status","arguments":{}}}' 2>/dev/null)
  echo "$STATUS" | python3 -c "
import sys, json
raw = sys.stdin.read()
if 'data: ' in raw:
    data = json.loads(raw.split('data: ')[1])
    content = data.get('result', {}).get('content', [{}])[0].get('text', '')
    status = json.loads(content) if content.startswith('{') else content
    if isinstance(status, dict):
        for k, v in status.items():
            print(f'  {k}: {v}')
    else:
        print(f'  {status}')
" 2>/dev/null || echo "  (could not parse status)"
else
  echo "  MCP server: unreachable"
fi

echo ""
echo "--- NAS Files ---"
ssh tgds@192.168.10.24 'find /mnt/memory -name "*.jsonl" -type f | wc -l' 2>/dev/null | xargs -I{} echo "  JSONL files: {}"
ssh tgds@192.168.10.24 'du -sh /mnt/memory 2>/dev/null' | xargs -I{} echo "  Total size: {}"

echo ""
echo "Done."
