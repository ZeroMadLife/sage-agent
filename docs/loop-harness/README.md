# Sage Loop Engineer Harness

Loop Engineer 是当前 Mac 上的受控巡检控制面。默认是 **Phase 1 dry-run**；显式进入
Phase 2 `SHADOW_WRITE` 后，可在隔离 worktree 生成未提交前端 diff，但仍不会 push、创建
PR、启动 Claude 或自动合并。显式 `PR_CANARY` 已具备中文 Draft PR 与独立 Claude 审查
链路，但不会自动合并。当前部署使用 `AUTO_MERGE_TIER_A`：只有小范围前端 Tier A 在本地
验证、独立 Claude 审查和 GitHub CI 全通过后自动 squash 合入 `dev/sage-v7`；Tier B/C
仍由用户决定或只生成报告，`main` 永不自动合并。

## 文档索引

- [POLICY.md](POLICY.md)：职责、风险等级和禁止范围
- [SOP.md](SOP.md)：每小时运行与状态机
- [PROMPT.md](PROMPT.md)：Worker/Reviewer 的版本化契约
- [REVIEW.md](REVIEW.md)：Tier A/B/C 与人工审查
- [RUNBOOK.md](RUNBOOK.md)：安装、启停、排障和卸载
- [findings/OPEN.md](findings/OPEN.md)：去重后的开放大问题
- [完整设计](../superpowers/specs/2026-07-16-sage-loop-engineer-harness-design.md)
- [Phase 2 设计](../superpowers/specs/2026-07-16-sage-loop-engineer-phase2-design.md)：已批准，shadow 已实施
- [Phase 2 实施计划](../superpowers/plans/2026-07-16-sage-loop-engineer-phase2.md)：小版本 1-6 已实现，PR canary 已启用
- [Phase 3 夜间深度开发 Lane 设计](../superpowers/specs/2026-07-16-sage-loop-engineer-night-lane-design.md)：已确认，尚未实施
- [Phase 3 实施计划](../superpowers/plans/2026-07-16-sage-loop-engineer-night-lane.md)：待 Phase 2 验收后执行

## 存储原则

本地运行数据只写 `~/.local/state/sage-loop/` 和临时 worktree，`NO_OP` 不创建
Markdown。日志单文件 5 MB、最多 7 份（当前文件加 6 个备份），约 35 MB 硬上限；
候选和日报保留 90 天，临时 worktree 在终态后清理且不复制 `node_modules`。

开放 Loop PR 合入远程规则后可通过 GitHub 查询：
`is:pr is:open head:codex/loop- base:dev/sage-v7`。

## 当前实现边界

- 已实现：`SHADOW_WRITE` 模式、Phase 1 SQLite 迁移、dirty path 避让、只读 Scanner、受控
  Fixer、真实 diff/权限判级、全量前端测试与 build、私有 quota 证据、安全清理、
  GitHub Draft PR 适配器、PR 槽位和 cc-connect Claude Reviewer；
- 尚未完成：截图、报告 Issue、审查后自动返工和跨运行 PR 状态同步；
- `SHADOW_WRITE` 只会在仓库外保存 7 天证据，不产生 commit、push、PR 或合并。
- 所有 PR 模式每天最多创建 1 个中文 Draft PR，同时最多保留 1 个 Harness 自动 PR；
  `PR_CANARY` 不自动合并，`AUTO_MERGE_TIER_A` 只自动合并满足全部门禁的 Tier A。
