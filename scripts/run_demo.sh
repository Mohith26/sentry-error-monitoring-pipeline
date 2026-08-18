#!/usr/bin/env bash
# End-to-end demo WITHOUT Docker: start the ingest API on a real socket, run the
# TypeScript SDK demo (which throws a mix of errors), then show the grouped issues.
#
#   ./scripts/run_demo.sh
#
# Requires: the Python venv (.venv) and a built SDK (cd sdk && npm install && npm run build).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
DB="$(mktemp -d)/demo.sqlite"

export SENTINEL_DB="$DB"
export SENTINEL_ALERT_THRESHOLD="10"
export SENTINEL_ALERT_WINDOW="60"

echo "[demo] starting ingest API on :$PORT (db=$DB)"
.venv/bin/uvicorn sentinel.api:app --host 127.0.0.1 --port "$PORT" --log-level warning &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

# wait for health
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 0.25
done

echo "[demo] running the SDK demo (throws + captures + posts)"
SENTINEL_URL="http://127.0.0.1:$PORT" node sdk/dist/demo/demo.js

echo
echo "[demo] /alerts fired:"
curl -fsS "http://127.0.0.1:$PORT/alerts" | python3 -m json.tool || true
