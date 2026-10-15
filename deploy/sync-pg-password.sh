#!/usr/bin/env bash
# Read-only verification by default; --apply repairs only a stale consumer secret.
# Checks both authority and exact consumer endpoint. Leaves a mode-600 rollback
# backup, does not rotate the PG role password and never restarts any service.
# VM_SSH (user@host) is required; LOCAL_ENV and REMOTE_ENV override defaults.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
exec "$REPO_DIR/.venv/bin/python" -m deploy.pg_credentials "$@"
