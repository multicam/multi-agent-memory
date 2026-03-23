#!/usr/bin/env bash
set -euo pipefail

cd /opt/multi-agent-memory
git pull --ff-only
uv sync
sudo systemctl restart agent-memory
echo "Deployed $(git rev-parse --short HEAD)"
