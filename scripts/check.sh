#!/bin/bash
# Sage 代码质量检查 — 在 commit 前运行
# 用法：bash scripts/check.sh
# 等效于CI会跑的检查，本地先跑一遍避免提交后才发现问题

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${SAGE_PYTHON:-${ROOT_DIR}/.venv/bin/python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Sage Python interpreter not found: ${PYTHON_BIN}"
  echo "Run: bash scripts/bootstrap-dev-env.sh"
  exit 1
fi

echo "===== ruff lint ====="
"${PYTHON_BIN}" -m ruff check api/ core/ db/ evals/ tests/
echo "===== ruff format --check ====="
"${PYTHON_BIN}" -m ruff format --check api/ core/ db/ evals/ tests/
echo "===== mypy type check ====="
"${PYTHON_BIN}" -m mypy core/ db/ api/ evals/
echo "===== pytest ====="
"${PYTHON_BIN}" -m pytest tests/ -v --tb=short
echo ""
echo "✅ 全部检查通过"
