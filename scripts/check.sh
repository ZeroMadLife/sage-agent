#!/bin/bash
# TourSwarm 代码质量检查 — 在commit前运行
# 用法：bash scripts/check.sh
# 等效于CI会跑的检查，本地先跑一遍避免提交后才发现问题

set -e
echo "===== ruff lint ====="
ruff check core/ mcp_servers/ agents/ tests/
echo "===== ruff format --check ====="
ruff format --check core/ mcp_servers/ agents/ tests/
echo "===== mypy type check ====="
mypy core/ mcp_servers/ agents/
echo "===== pytest ====="
pytest tests/ -v --tb=short
echo ""
echo "✅ 全部检查通过"
