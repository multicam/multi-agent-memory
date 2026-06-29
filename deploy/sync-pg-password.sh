#!/usr/bin/env bash
# sync-pg-password.sh — propagate the canonical memory_user PG password to vm-services.
#
# WHY: the MAM .env is hand-maintained in three places (this repo's local .env,
# dark-as-fac/.env, and vm-services:/opt/multi-agent-memory/.env). The vm-services
# copy has silently drifted from the workstation PG password 3 times, each time
# killing the whole pipeline (the psycopg pool masks the auth failure as a 30s
# PoolTimeout / crash-loop). This script makes the LOCAL repo .env the single
# source of truth for that password and pushes it — verified — to vm-services,
# so the file is never hand-edited again.
#
# WHAT IT DOES (idempotent, non-destructive):
#   1. read memory_user password from the canonical local .env PG_URL
#   2. verify it actually authenticates against the workstation PG (fail fast)
#   3. rewrite ONLY the PG_URL password field in the remote .env (every other
#      var — ANTHROPIC_API_KEY, HF_TOKEN, NAS_PATH, ports, the .215 host — is
#      preserved); a timestamped backup is left on the remote
#   4. restart agent-memory and poll :8888 until it serves (200)
#
# USAGE:  deploy/sync-pg-password.sh
# Run from the workstation. Override targets via env if topology changes:
#   VM_SSH (default tgds@192.168.10.24)  REMOTE_ENV (default /opt/multi-agent-memory/.env)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_ENV="${LOCAL_ENV:-$REPO_DIR/.env}"
VM_SSH="${VM_SSH:-tgds@192.168.10.24}"
REMOTE_ENV="${REMOTE_ENV:-/opt/multi-agent-memory/.env}"
PG_VERIFY_HOST="${PG_VERIFY_HOST:-127.0.0.1}"

die() { echo "✗ $*" >&2; exit 1; }

[ -f "$LOCAL_ENV" ] || die "canonical .env not found: $LOCAL_ENV"

# 1. extract password (between 'memory_user:' and '@') from the canonical PG_URL
DSN="$(grep -E '^PG_URL=' "$LOCAL_ENV" | head -1 | cut -d= -f2-)"
[ -n "$DSN" ] || die "no PG_URL in $LOCAL_ENV"
PW="$(printf '%s' "$DSN" | sed -E 's#^[a-z]+://[^:]+:(.*)@[^@]+$#\1#')"
USER="$(printf '%s' "$DSN" | sed -E 's#^[a-z]+://([^:]+):.*#\1#')"
DB="$(printf '%s' "$DSN" | sed -E 's#.*/([^/?]+)(\?.*)?$#\1#')"
[ -n "$PW" ] && [ "$PW" != "$DSN" ] || die "could not parse password from PG_URL"

# 2. verify against the workstation PG before touching anything remote
echo "→ verifying $USER@$PG_VERIFY_HOST/$DB ..."
PGPASSWORD="$PW" psql -h "$PG_VERIFY_HOST" -U "$USER" -d "$DB" -tAc 'select 1' >/dev/null 2>&1 \
  || die "canonical password does NOT authenticate against workstation PG — fix $LOCAL_ENV (or the PG role) before syncing"
echo "✓ canonical password authenticates"

# 3. surgically rewrite ONLY the PG_URL password on the remote (preserve all else)
echo "→ pushing password to $VM_SSH:$REMOTE_ENV (other vars preserved) ..."
printf '%s\n' "$PW" | ssh "$VM_SSH" '
  set -e
  IFS= read -r PW
  ENV="'"$REMOTE_ENV"'"
  [ -f "$ENV" ] || { echo "remote .env missing: $ENV" >&2; exit 3; }
  cp -a "$ENV" "${ENV}.bak-$(date +%Y%m%d-%H%M%S)"
  PW="$PW" python3 - "$ENV" <<"PY"
import os, re, sys, pathlib
pw, p = os.environ["PW"], pathlib.Path(sys.argv[1])
out=[]
for ln in p.read_text().splitlines():
    if ln.startswith("PG_URL="):
        ln=re.sub(r"(://[^:]+:)[^@]+(@)", lambda m: m.group(1)+pw+m.group(2), ln)
    out.append(ln)
p.write_text("\n".join(out)+"\n")
PY
  chmod 600 "$ENV"
'

# 4. restart + poll until the MCP server serves
echo "→ restarting agent-memory and waiting for :8888 ..."
ssh "$VM_SSH" '
  sudo systemctl reset-failed agent-memory.service 2>/dev/null || true
  sudo systemctl restart agent-memory.service
  for i in $(seq 1 12); do
    sleep 5
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8888/mcp \
      -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
      -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"sync\",\"version\":\"1\"}}}" 2>/dev/null)
    [ "$code" = "200" ] && { echo "✓ MCP server up (http $code, NRestarts=$(systemctl show agent-memory -p NRestarts --value))"; exit 0; }
  done
  echo "✗ MCP server did not reach 200 within 60s — check: journalctl -u agent-memory" >&2
  exit 4
'
echo "✓ done — vm-services PG password in sync with $LOCAL_ENV"
