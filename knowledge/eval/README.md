# Sage Official Agent/Fullstack Eval v1

本数据集用于固定 RAG 优化的比较尺子。它不与 worktree 一一对应，也不把 test split 当调参集。

## 分层

- `dev=40`：实现与失败分析可见。
- `calibration=20`：只用于 Gate、阈值或受控参数选择。
- `test=20`：冻结，只在阶段验收时运行；`dataset.json` 记录 `frozen_test=true`。

同一问题的改写通过 `leakage_group` 绑定，整个 group 必须位于同一 split。运行时还拒绝重复
query、未知 corpus ID、answerable/evidence 矛盾和 hash 漂移。

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python scripts/validate_knowledge_dataset.py
```

该命令只做合同、路径、hash、anchor 和 split 校验，输出机器可读摘要，不联网也不运行检索。

## 当前边界

- 80 条 case 是首批人工策划资产，不代表真实线上分布。
- PR-1 只建立数据契约；SQLite baseline 从 PR-2 开始生成。
- 当前 case 以 text/code/table 为主；`gold_page/gold_bbox` 是 PR-7 多模态证据链的前置合同，
  本 revision 均为 `null`。
- required/forbidden claims 是确定性 generation 断言的输入，不等同于 LLM judge 分数。
