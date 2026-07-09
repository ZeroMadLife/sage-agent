#!/usr/bin/env bash
# Start Sage local development servers from the repository root.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
API_TARGET="${VITE_API_PROXY_TARGET:-http://${BACKEND_HOST}:${BACKEND_PORT}}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  if [[ -n "${FRONTEND_PID}" ]]; then
    kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${BACKEND_PID}" ]]; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

cd "${ROOT_DIR}"

if [[ ! -f ".env" ]]; then
  echo "Missing .env. Create one with: cp .env.example .env"
  exit 1
fi

if [[ "${SAGE_SKIP_DOCKER:-0}" != "1" ]]; then
  if command -v docker >/dev/null 2>&1; then
    docker compose up -d
  else
    echo "Docker not found; skipping docker compose. Set SAGE_SKIP_DOCKER=1 to silence this."
  fi
fi

echo "Starting backend: http://${BACKEND_HOST}:${BACKEND_PORT}"
python -m uvicorn api.main:app \
  --host "${BACKEND_HOST}" \
  --port "${BACKEND_PORT}" \
  --reload \
  --env-file ".env" &
BACKEND_PID="$!"

echo "Starting frontend: http://${FRONTEND_HOST}:${FRONTEND_PORT}"
(
  cd "${ROOT_DIR}/frontend"
  VITE_API_PROXY_TARGET="${API_TARGET}" npm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}"
) &
FRONTEND_PID="$!"

echo ""
echo "Sage dev servers are starting."
echo "Frontend: http://${FRONTEND_HOST}:${FRONTEND_PORT}"
echo "Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "Stop both with Ctrl-C."

wait "${BACKEND_PID}" "${FRONTEND_PID}"
