#!/usr/bin/env bash
# Local test drive: dev Postgres + migrations + demo data + API with the browser console.
# DEV ONLY. Usage: scripts/test-drive.sh            (Ctrl+C stops the API; Postgres keeps running)
#                  scripts/test-drive.sh --reset    (wipe the dev database first)
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f deploy/dev/docker-compose.yml"
PORT="${GNOSIS_PORT:-58080}"

if [[ "${1:-}" == "--reset" ]]; then
  echo "Resetting the dev database…"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
fi

echo "Starting Postgres (localhost:55432)…"
$COMPOSE up -d postgres >/dev/null
for _ in $(seq 1 60); do
  $COMPOSE exec -T postgres pg_isready -U gnosis_owner -d gnosis >/dev/null 2>&1 && break
  sleep 1
done

echo "Applying migrations…"
GNOSIS_MIGRATION_DATABASE_URL=postgresql+psycopg://gnosis_owner:dev-only-owner@localhost:55432/gnosis \
  uv run --quiet alembic upgrade head

echo "Loading demo data…"
uv run --quiet python scripts/demo.py seed | head -1

export GNOSIS_DEV_MODE=true
export GNOSIS_JWT_HS256_SECRET="${GNOSIS_JWT_HS256_SECRET:-$(openssl rand -hex 32)}"
echo
echo "  Studio preview:  http://127.0.0.1:${PORT}/studio"
echo "  API docs:        http://127.0.0.1:${PORT}/docs"
echo "  Ctrl+C to stop."
echo
exec uv run --quiet uvicorn gnosis_api.main:app --host 127.0.0.1 --port "$PORT"
