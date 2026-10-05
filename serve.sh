#!/usr/bin/env bash
# Build the frontend and serve the whole app from the backend at http://127.0.0.1:8765
# Env: COE_PORT (default 8765), COE_RPM (default 60)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
(cd "$ROOT/frontend" && npm run build)
exec uv run --project "$ROOT/backend" coe-wizard --data-dir "$ROOT/data" --port "${COE_PORT:-8765}" \
  --rpm "${COE_RPM:-60}" --static-dir "$ROOT/frontend/dist" serve
