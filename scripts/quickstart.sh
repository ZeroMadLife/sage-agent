#!/usr/bin/env bash
# Sage local product quickstart.
#
# This script owns first-run preparation only. scripts/dev.sh remains the
# single process launcher for Docker Compose, FastAPI, and Vue.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SAGE_ENV_FILE:-${ROOT_DIR}/.env}"
VENV_DIR="${SAGE_VENV_DIR:-${ROOT_DIR}/.venv}"
PYTHON_BIN="${SAGE_PYTHON:-${VENV_DIR}/bin/python}"
PYTHON_BIN_EXPLICIT=0
if [[ -n "${SAGE_PYTHON:-}" ]]; then
  PYTHON_BIN_EXPLICIT=1
fi

usage() {
  cat <<'EOF'
Sage local product quickstart

Usage:
  bash scripts/quickstart.sh [--start]
  bash scripts/quickstart.sh --check
  bash scripts/quickstart.sh --help

Commands:
  --start  prepare the local environment, then start the existing Web stack (default)
  --check  prepare the local environment and run configuration checks only
EOF
}

fail() {
  echo "Sage quickstart error: $*" >&2
  exit 1
}

if [[ "${ENV_FILE}" != /* ]]; then
  ENV_FILE="${ROOT_DIR}/${ENV_FILE}"
fi

ensure_env_file() {
  local parent
  parent="$(dirname "${ENV_FILE}")"

  if [[ -L "${ENV_FILE}" ]]; then
    fail "environment file cannot be a symbolic link: ${ENV_FILE}"
  fi
  if [[ -f "${ENV_FILE}" ]]; then
    return
  fi
  if [[ -e "${ENV_FILE}" ]]; then
    fail "environment path is not a regular file: ${ENV_FILE}"
  fi
  [[ -f "${ROOT_DIR}/.env.example" ]] || fail "missing .env.example"

  mkdir -p "${parent}"
  cp "${ROOT_DIR}/.env.example" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created local environment file: ${ENV_FILE}"
  echo "Fill in a model Provider key before starting a real Agent run."
}

require_command() {
  local name="$1"
  command -v "${name}" >/dev/null 2>&1 || fail "${name} is required; install it and rerun"
}

check_node_version() {
  local major
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [[ "${major}" =~ ^[0-9]+$ ]] || fail "unable to read Node.js version"
  (( major >= 24 )) || fail "Node.js 24 or newer is required (found ${major})"
}

python_ready() {
  [[ -x "${PYTHON_BIN}" ]] || return 1
  "${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit(1)
from langchain.agents import create_agent
import sage_harness

assert create_agent is not None
assert sage_harness.__name__ == "sage_harness"
PY
}

prepare_python() {
  require_command uv
  if python_ready; then
    echo "Python environment: ready"
    return
  fi

  if [[ "${PYTHON_BIN_EXPLICIT}" == "1" ]]; then
    fail "SAGE_PYTHON is missing Sage dependencies: ${PYTHON_BIN}; unset SAGE_PYTHON or bootstrap that environment"
  fi

  echo "Preparing Python environment..."
  SAGE_VENV_DIR="${VENV_DIR}" bash "${ROOT_DIR}/scripts/bootstrap-dev-env.sh"
  python_ready || fail "Python dependencies are incomplete after bootstrap"
  echo "Python environment: ready"
}

prepare_frontend() {
  require_command node
  require_command npm
  check_node_version

  if [[ -d "${ROOT_DIR}/frontend/node_modules" ]]; then
    echo "Frontend dependencies: ready"
    return
  fi

  echo "Installing frontend dependencies..."
  npm --prefix "${ROOT_DIR}/frontend" ci
  echo "Frontend dependencies: ready"
}

run_dev_check() {
  SAGE_DEV_CHECK_ONLY=1 \
    SAGE_ENV_FILE="${ENV_FILE}" \
    SAGE_PYTHON="${PYTHON_BIN}" \
    bash "${ROOT_DIR}/scripts/dev.sh"
}

check_local_product() {
  echo "Checking Sage local product environment..."
  ensure_env_file
  prepare_python
  prepare_frontend
  run_dev_check
  echo "Sage local product checks passed."
}

mode="start"
case "${1:-}" in
  ""|--start) mode="start" ;;
  --check) mode="check" ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

cd "${ROOT_DIR}"
check_local_product

if [[ "${mode}" == "start" ]]; then
  exec env \
    SAGE_ENV_FILE="${ENV_FILE}" \
    SAGE_PYTHON="${PYTHON_BIN}" \
    SAGE_DEV_RELOAD=0 \
    bash "${ROOT_DIR}/scripts/dev.sh"
fi
