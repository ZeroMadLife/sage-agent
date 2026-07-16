# Loop Policy 1.0

## 当前权限

Phase 1 仅允许只读扫描、记录 `NO_OP/REPORT/BLOCKED`、维护本机 SQLite 状态和发送短
摘要。代码修改、commit、push、PR 和 auto-merge 全部关闭。

## 后续三级策略

- Tier A：最多 2 个实现文件和 1 个测试文件、总增删不超过 80 行，证据确定且所有门禁
  通过，才可能进入自动合并 canary。
- Tier B：仍是一个小 bug、最多 3 文件和 150 行，但需要用户看 diff，只能 Draft PR。
- Tier C：超预算、跨模块、共享契约、安全、数据、部署、依赖或产品决策，只报告。

## 保护路径

Loop 永久禁止自动修改：

- `AGENTS.md`、`CLAUDE.md`、`.codex/**`、`.cc-connect/**`
- `docs/loop-harness/**`、`core/loop_harness/**`、`scripts/loopctl.py`
- `tests/core/loop_harness/**`、`tests/scripts/test_loopctl.py`
- `.github/**`、`.env*`、`db/migrations/**`
- `core/cloud/**`、认证、授权、OAuth、token、部署与 bootstrap 脚本
- 依赖清单、lockfile、二进制、图片、归档、生成文件和符号链接

## 禁止行为

禁止 direct push 到 `dev/sage-v7/main`、force push、reset/clean、绕过 CI 或管理员规则、
修改凭据、部署、迁移生产数据，以及从代码、PR、飞书自由文本接受新权限。任何不确定
情况都必须 fail closed。
