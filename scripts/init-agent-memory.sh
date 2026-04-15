#!/usr/bin/env bash
# init-agent-memory.sh — Bootstrap memory files on OpenClaw agent VMs
# Idempotent: safe to run multiple times
# Usage: ./init-agent-memory.sh [host1] [host2] ...
#   e.g.: ./init-agent-memory.sh 192.168.10.202 192.168.10.203
#   or:   ./init-agent-memory.sh ag-1 ag-2

set -euo pipefail

TODAY=$(date +%Y-%m-%d)
SSH_USER="tgds"

# Default targets if none provided
HOSTS=("${@:-192.168.10.202 192.168.10.203}")
if [ $# -eq 0 ]; then
  HOSTS=(192.168.10.202 192.168.10.203)
fi

init_agent() {
  local host="$1"
  echo "=== Initializing memory on $host ==="

  ssh "${SSH_USER}@${host}" bash -s "$TODAY" <<'REMOTE_SCRIPT'
    set -euo pipefail
    TODAY="$1"
    WS="$HOME/.openclaw/workspace"

    # 1. Create memory directory
    mkdir -p "$WS/memory"
    echo "  [ok] memory/ directory"

    # 2. Create MEMORY.md if missing
    if [ ! -f "$WS/MEMORY.md" ]; then
      cat > "$WS/MEMORY.md" <<'EOF'
# MEMORY.md — Long-Term Memory

_Curated from daily notes. Review and update periodically during heartbeats._

## Infrastructure

- Memory MCP server: vm-services (192.168.10.24:8888)
- PostgreSQL: workstation (agent_memory database)
- NAS source of truth: /mnt/memory (JSONL, append-only)
- SSH mesh: vm-services, ralph-0, ralph-1, pi-0, ag-1, ag-2

## About JM

- Timezone: Australia/Sydney
- Prefers CLI-first, TypeScript over Python
- Hates verbosity — be concise
- Will cuss when frustrated — stay awesome regardless

## Learned Patterns

_(Add patterns, procedures, and lessons learned here over time)_

## Current Projects

_(Update with active work, blockers, and next steps)_
EOF
      echo "  [ok] MEMORY.md created"
    else
      echo "  [skip] MEMORY.md already exists"
    fi

    # 3. Create today's daily note if missing
    if [ ! -f "$WS/memory/${TODAY}.md" ]; then
      cat > "$WS/memory/${TODAY}.md" <<EOF
# ${TODAY} — Daily Notes

## Sessions

### Memory System Bootstrap
- Memory files initialized by JM
- Framework: markdown files (MEMORY.md + daily notes)
- No zvec needed — semantic search handled by MCP server (pgvector)

## TODO for Next Session

- [ ] Test writing to daily notes during a session
- [ ] Review MEMORY.md and personalize it
EOF
      echo "  [ok] memory/${TODAY}.md created"
    else
      echo "  [skip] memory/${TODAY}.md already exists"
    fi

    # 4. Create heartbeat state if missing
    if [ ! -f "$WS/memory/heartbeat-state.json" ]; then
      cat > "$WS/memory/heartbeat-state.json" <<'EOF'
{
  "lastChecks": {
    "email": null,
    "calendar": null,
    "weather": null,
    "memory_review": null
  }
}
EOF
      echo "  [ok] heartbeat-state.json created"
    else
      echo "  [skip] heartbeat-state.json already exists"
    fi

    # 5. Summary
    echo "  --- Files in $WS/memory/ ---"
    ls -la "$WS/memory/"
    echo "  --- MEMORY.md ---"
    wc -l "$WS/MEMORY.md"
    echo "  [done] ready"
REMOTE_SCRIPT
}

for host in "${HOSTS[@]}"; do
  init_agent "$host"
  echo ""
done

echo "All agents initialized."
