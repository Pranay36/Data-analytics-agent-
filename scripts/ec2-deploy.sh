#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/insightflow}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$SCRIPT_DIR/ec2-setup.sh"

COMPOSE="docker compose --profile app"

cd "$APP_DIR"

if docker info >/dev/null 2>&1; then
  DC=(docker compose)
else
  DC=(sudo docker compose)
fi

echo "== pulling latest code"
git fetch origin "${GIT_BRANCH:-main}"
git reset --hard "origin/${GIT_BRANCH:-main}"

echo "== starting databases (if not already running)"
"${DC[@]}" up -d appdb demo-analytics

echo "== waiting for databases"
for _ in $(seq 1 30); do
  if "${DC[@]}" ps appdb demo-analytics 2>/dev/null | grep -q "(healthy)"; then
    break
  fi
  sleep 2
done

echo "== running seed (migrations + demo data; safe to repeat)"
"${DC[@]}" --profile app run --rm seed

echo "== building and restarting API"
"${DC[@]}" --profile app build api
"${DC[@]}" --profile app up -d api

echo "== health check"
for _ in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null; then
    echo "API is healthy"
    curl -s http://127.0.0.1:8000/health/ready | head -c 200
    echo
    exit 0
  fi
  sleep 3
done

echo "ERROR: API did not become healthy in time"
exit 1
