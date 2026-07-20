# Loop Policy 2.2 Tier A 自动合并

## 当前权限

默认 Phase 1 仅允许只读扫描、记录 `NO_OP/REPORT/BLOCKED`、维护本机 SQLite 状态和发送
短摘要。显式启用 Phase 2 `SHADOW_WRITE` 后，才允许受控前端 Fixer 在临时 worktree 生成
未提交 diff。人工显式启用 `PR_CANARY` 后，Controller 可以提交受控 diff、push
`codex/loop-frontend-*` 分支、创建中文 Draft PR 并触发独立 cc-connect Claude 审查，但不
自动合并。人工显式启用 `AUTO_MERGE_TIER_A` 后，只有确定性判级为 Tier A、独立 Claude
审查 `PASS` 且 GitHub checks 全绿的 PR 才会 squash 合入 `dev/sage-v7`。

Phase 2 `SHADOW_WRITE` 只允许局部前端候选。Controller 根据真实 diff 判定 Tier A/B/C；
Tier A/B 在 `SHADOW_WRITE` 中只记录本机证据；`PR_CANARY` 中一律保持 Draft；
`AUTO_MERGE_TIER_A` 中 Tier B 仍保持 Draft，Tier C 仍只报告。
后端、共享契约和大范围问题只生成中文报告。

## 对外输出语言

Loop 面向用户和团队的输出统一使用简体中文，包括 finding、日报、飞书通知、PR 标题、
PR 正文、审查说明和验证摘要。代码标识符、路径、命令、错误码及 Conventional Commit
前缀可以保留英文。中文表达是创建 PR 的硬门禁，不满足时 Controller 必须拒绝发布。

## 后续三级策略

- Tier A：最多 2 个实现文件和 1 个测试文件、总增删不超过 80 行；纯视觉小修可以不改
  测试，组件内行为修复必须同时包含对应回归测试。证据、独立审查、本地验证和 GitHub CI
  全部通过后自动 squash 合并。
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

自动合并永久只面向 `dev/sage-v7`，不得自动合入 `main`。PR head、target base、风险标签、
人工 `changes requested` 或 checks 任一项变化时，Controller 停止自动合并并保留 PR。
