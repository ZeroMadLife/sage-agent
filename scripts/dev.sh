#!/usr/bin/env bash
# Start Sage local development servers from the repository root.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SAGE_ENV_FILE:-${ROOT_DIR}/.env}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
API_TARGET="${VITE_API_PROXY_TARGET:-http://${BACKEND_HOST}:${BACKEND_PORT}}"
PYTHON_BIN="${SAGE_PYTHON:-${ROOT_DIR}/.venv/bin/python}"

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

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Sage Python interpreter not found: ${PYTHON_BIN}"
  echo "Run: bash scripts/bootstrap-dev-env.sh"
  exit 1
fi

if ! "${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit("Sage requires Python 3.12 or newer")

from langchain.agents import create_agent
import sage_harness

assert create_agent is not None
assert sage_harness.__name__ == "sage_harness"
PY
then
  echo "Sage Python dependencies are incomplete or incompatible: ${PYTHON_BIN}"
  echo "Run: bash scripts/bootstrap-dev-env.sh"
  exit 1
fi

if [[ "${ENV_FILE}" != /* ]]; then
  ENV_FILE="${ROOT_DIR}/${ENV_FILE}"
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing environment file: ${ENV_FILE}"
  echo "Create one with: cp .env.example .env"
  exit 1
fi

env_file_has_value() {
  local key="$1"
  awk -F= -v key="${key}" '
    /^[[:space:]]*#/ { next }
    {
      candidate = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", candidate)
      if (candidate != key) { next }
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (length(value) > 0) { found = 1 }
    }
    END { exit found ? 0 : 1 }
  ' "${ENV_FILE}"
}

configured_providers=()
for provider_key in \
  DEEPSEEK_API_KEY \
  DOUBAO_API_KEY \
  OPENAI_PROXY_API_KEY \
  OPENAI_API_KEY \
  ANTHROPIC_API_KEY; do
  if [[ -n "${!provider_key:-}" ]] || env_file_has_value "${provider_key}"; then
    configured_providers+=("${provider_key%_API_KEY}")
  fi
done

if [[ ${#configured_providers[@]} -eq 0 ]]; then
  echo "Warning: no model provider API key is configured; coding sessions cannot call a real model."
else
  echo "Configured model providers: ${configured_providers[*]}"
fi

if [[ "${SAGE_DEV_CHECK_ONLY:-0}" == "1" ]]; then
  echo "Local development configuration is valid."
  exit 0
fi

if [[ "${SAGE_SKIP_DOCKER:-0}" != "1" ]]; then
  if command -v docker >/dev/null 2>&1; then
    docker compose up -d
  else
    echo "Docker not found; skipping docker compose. Set SAGE_SKIP_DOCKER=1 to silence this."
  fi
fi

echo "Starting backend: http://${BACKEND_HOST}:${BACKEND_PORT}"
"${PYTHON_BIN}" -m uvicorn api.main:app \
  --host "${BACKEND_HOST}" \
  --port "${BACKEND_PORT}" \
  --reload \
  --env-file "${ENV_FILE}" &
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
