#!/usr/bin/env bash
set -euo pipefail

cd /opt/multi-agent-memory
git pull --ff-only
uv sync

# Verify an actual read from the deployed environment file. No DSN on argv.
# For credential-only recovery use sync-pg-password.sh --apply from the
# workstation; that command verifies the target, backs up .env and does not restart.
.venv/bin/python -m deploy.pg_credentials --local-only || {
    echo "✗ PostgreSQL preflight failed (credential rejected, host unreachable or timed out)." >&2
    echo "  Nothing was deployed or restarted. From the workstation run:" >&2
    echo "    VM_SSH=user@host deploy/sync-pg-password.sh          # check" >&2
    echo "    VM_SSH=user@host deploy/sync-pg-password.sh --apply  # repair, protected backup" >&2
    exit 1
}

# Deploy curation timer
sudo cp deploy/agent-memory-curate.service /etc/systemd/system/
sudo cp deploy/agent-memory-curate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-memory-curate.timer

sudo systemctl restart agent-memory
echo "Deployed $(git rev-parse --short HEAD)"
