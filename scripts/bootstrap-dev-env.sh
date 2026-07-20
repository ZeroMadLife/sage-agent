#!/usr/bin/env bash
# Create the repository-local Python environment used by IDEs and dev scripts.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_VERSION="${SAGE_PYTHON_VERSION:-$(tr -d '[:space:]' < "${ROOT_DIR}/.python-version")}"
VENV_DIR="${SAGE_VENV_DIR:-${ROOT_DIR}/.venv}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"

cd "${ROOT_DIR}"

if [[ -z "${UV_BIN}" ]]; then
  echo "uv is required to create the Sage development environment."
  echo "Install it from https://docs.astral.sh/uv/ and rerun this script."
  exit 1
fi

needs_recreate=0
if [[ -x "${VENV_DIR}/bin/python" ]]; then
  if ! "${VENV_DIR}/bin/python" -c \
    'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    needs_recreate=1
  fi
else
  needs_recreate=1
fi

if [[ "${needs_recreate}" == "1" ]]; then
  echo "Creating ${VENV_DIR} with Python ${PYTHON_VERSION}..."
  "${UV_BIN}" venv --clear --python "${PYTHON_VERSION}" "${VENV_DIR}"
fi

echo "Installing Sage Python dependencies..."
"${UV_BIN}" pip install \
  --no-config \
  --python "${VENV_DIR}/bin/python" \
  --requirements "${ROOT_DIR}/requirements.txt"

# Python 3.12 skips .pth files carrying the macOS hidden file flag. That makes
# editable packages disappear even though their dist-info metadata is present.
if [[ "$(uname -s)" == "Darwin" ]]; then
  chflags -R nohidden "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" - <<'PY'
import sys

from langchain.agents import create_agent
import sage_harness

assert create_agent is not None
print(f"Sage Python environment is ready: {sys.version.split()[0]}")
print(f"Harness package: {sage_harness.__file__}")
PY
