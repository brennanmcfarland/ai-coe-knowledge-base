#!/usr/bin/env bash
# Backend + Vite dev server with hot reload. Open http://127.0.0.1:5173
# The browser-facing origin is Vite, so OAuth redirects go through Vite's /api proxy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
uv run --project "$ROOT/backend" coe-wizard --data-dir "$ROOT/data" --port 8765 \
  --public-url http://127.0.0.1:5173 serve &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT
cd "$ROOT/frontend" && npm run dev
