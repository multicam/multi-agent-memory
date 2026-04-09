#!/usr/bin/env bash
# Shared configuration for shell scripts.
# Source this: . "$(dirname "$0")/env.sh"

: "${PGPASSWORD:?PGPASSWORD must be set (export it or add to ~/.pgpass)}"

PG_HOST="${PG_HOST:-localhost}"
PG_USER="${PG_USER:-memory_user}"
PG_DB="${PG_DB:-agent_memory}"

MCP_HOST="${MCP_HOST:-192.168.10.24}"
MCP_PORT="${MCP_PORT:-8888}"
VM_USER="${VM_USER:-tgds}"
