#!/usr/bin/env bash
set -euo pipefail

cd /opt/multi-agent-memory
git pull --ff-only
uv sync

# Fail loudly if the PG password has drifted, instead of letting the service
# crash-loop on a pool timeout (the auth error is otherwise masked). The
# canonical password lives in the workstation repo .env; propagate it with
# `deploy/sync-pg-password.sh` (run from the workstation), NOT by hand-editing
# this .env. See the recurring "MAM PG auth" drift incidents.
DSN="$(grep -E '^PG_URL=' .env | head -1 | cut -d= -f2-)"
.venv/bin/python - "$DSN" <<'PY' || { echo "✗ PG auth failed — run deploy/sync-pg-password.sh from the workstation, then re-deploy" >&2; exit 1; }
import sys, psycopg
psycopg.connect(sys.argv[1], connect_timeout=10).close()
print("✓ PG auth OK")
PY

# Deploy curation timer
sudo cp deploy/agent-memory-curate.service /etc/systemd/system/
sudo cp deploy/agent-memory-curate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-memory-curate.timer

sudo systemctl restart agent-memory
echo "Deployed $(git rev-parse --short HEAD)"
