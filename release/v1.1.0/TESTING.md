# v1.1.0 Testing

> Candidate base: `dev/sage-v7@40c2a17`
> Final ref: `v1.1.0`（由通过门禁的 `main` 合并提交创建）

## 自动化门禁

完整仓库质量检查：

```bash
bash scripts/check.sh
git diff --check
```

RAG 定向回归：

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python -m pytest -q \
  tests/core/knowledge \
  tests/scripts/test_evaluate_knowledge_semantic.py \
  tests/scripts/test_migrate_knowledge_index_postgres.py
```

Eval 契约：

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python scripts/validate_knowledge_dataset.py
```

## 已有证据

- PR #125 与 #126 的 `backend-quality`、`frontend-quality`、`public-release`、`python` 均通过。
- RAG 定向回归：`204 passed, 11 skipped`。
- 完整仓库门禁：Ruff lint、Ruff format、mypy `210` 个 source files 通过；pytest
  `1732 passed, 11 skipped`。
- PostgreSQL exact、Provider selection/final、Gate、recovery、消融、多模态合同与 HNSW scale
  报告固定在 `evals/reports/`，并由文档记录数据 Hash 与配置身份。
- 真实 PostgreSQL 集成由 CI 的 pgvector service 验证；本次收口未使用用户本机 DSN 重跑。

## 发布判定

只有以下条件同时满足，才可将 `dev/sage-v7` 合入 `main` 并创建 `v1.1.0` tag：

1. 收口 PR 与 `dev/sage-v7 -> main` PR 的必需检查均为绿色。
2. `git diff --check`、数据集校验和与改动匹配的自动化测试通过。
3. 未提交用户文件未被纳入提交。
4. 发布说明保持“本地自用源码版本”，不声称部署、线上指标或真实生成质量。
