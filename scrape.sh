#!/usr/bin/env bash
# Sync ClickUp Docs from the configured space into data/corpus/ (gitignored).
#
# The OAuth token only ever lives in the backend's memory, so this script drives the backend:
# it starts one if none is running, opens the login page if you aren't logged in, then triggers
# the sync and streams its progress. Requests are rate limited (default 60/min; COE_RPM to change).
#
# Env: COE_PORT (default 8765), COE_RPM (default 60)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${COE_PORT:-8765}"
BASE="http://127.0.0.1:$PORT"

json() { # read a field from JSON on stdin
  python3 -c "import json,sys; v=json.load(sys.stdin)$1; print('' if v is None else v)"
}

STARTED_PID=""
cleanup() { [[ -n "$STARTED_PID" ]] && kill "$STARTED_PID" 2>/dev/null || true; }
trap cleanup EXIT

mkdir -p "$ROOT/data"
if ! curl -sf "$BASE/api/auth/status" >/dev/null; then
  if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
    echo "Building the frontend for the login page…"
    (cd "$ROOT/frontend" && npm run build >/dev/null)
  fi
  echo "Starting the backend on $BASE"
  uv run --project "$ROOT/backend" coe-wizard --data-dir "$ROOT/data" --port "$PORT" \
    --rpm "${COE_RPM:-60}" --static-dir "$ROOT/frontend/dist" serve \
    >"$ROOT/data/scrape-server.log" 2>&1 &
  STARTED_PID=$!
  for _ in $(seq 1 60); do
    curl -sf "$BASE/api/auth/status" >/dev/null && break
    sleep 0.5
  done
  curl -sf "$BASE/api/auth/status" >/dev/null || { echo "Backend failed to start; see data/scrape-server.log" >&2; exit 1; }
fi

if [[ "$(curl -sf "$BASE/api/auth/status" | json "['logged_in']")" != "True" ]]; then
  # The browser-facing origin differs from $BASE when ./dev.sh is running (Vite on :5173).
  REDIRECT="$(curl -sf "$BASE/api/auth/status" | json "['redirect_uri']")"
  ORIGIN_LOGIN="${REDIRECT%/api/auth/callback}/login?next=sync"
  echo "Log in to ClickUp in your browser: $ORIGIN_LOGIN"
  (xdg-open "$ORIGIN_LOGIN" || open "$ORIGIN_LOGIN") >/dev/null 2>&1 || true
  for _ in $(seq 1 600); do
    [[ "$(curl -sf "$BASE/api/auth/status" | json "['logged_in']")" == "True" ]] && break
    sleep 1
  done
  [[ "$(curl -sf "$BASE/api/auth/status" | json "['logged_in']")" == "True" ]] || { echo "Timed out waiting for login" >&2; exit 1; }
fi

code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/sync")"
if [[ "$code" != "202" && "$code" != "409" ]]; then
  echo "Could not start sync (HTTP $code)" >&2
  exit 1
fi

since=0
while true; do
  status="$(curl -sf "$BASE/api/sync?since=$since")"
  echo "$status" | python3 -c "import json,sys; [print(m) for m in json.load(sys.stdin)['messages']]"
  since="$(echo "$status" | json "['next']")"
  state="$(echo "$status" | json "['state']")"
  case "$state" in
    running) sleep 2 ;;
    succeeded) echo "Sync complete."; exit 0 ;;
    *) echo "Sync $state: $(echo "$status" | json "['error']")" >&2; exit 1 ;;
  esac
done
