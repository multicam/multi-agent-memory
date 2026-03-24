#!/usr/bin/env bash
set -euo pipefail

cd /opt/multi-agent-memory
git pull --ff-only
uv sync

# Deploy curation timer
sudo cp deploy/agent-memory-curate.service /etc/systemd/system/
sudo cp deploy/agent-memory-curate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-memory-curate.timer

sudo systemctl restart agent-memory
echo "Deployed $(git rev-parse --short HEAD)"
