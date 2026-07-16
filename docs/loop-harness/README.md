# Sage Loop Engineer Harness

Loop Engineer 是当前 Mac 上的只读巡检控制面。当前阶段为 **Phase 1 dry-run**：全天按
小时扫描，但不会改代码、push、创建 PR 或自动合并。

## 文档索引

- [POLICY.md](POLICY.md)：职责、风险等级和禁止范围
- [SOP.md](SOP.md)：每小时运行与状态机
- [PROMPT.md](PROMPT.md)：Worker/Reviewer 的版本化契约
- [REVIEW.md](REVIEW.md)：Tier A/B/C 与人工审查
- [RUNBOOK.md](RUNBOOK.md)：安装、启停、排障和卸载
- [findings/OPEN.md](findings/OPEN.md)：去重后的开放大问题
- [完整设计](../superpowers/specs/2026-07-16-sage-loop-engineer-harness-design.md)

## 存储原则

每小时运行只写 `~/.local/state/sage-loop/state.sqlite3` 和轮转日志，`NO_OP` 不创建
Markdown。日志单文件 5 MB、最多 7 份（当前文件加 6 个备份），约 35 MB 硬上限；
候选和日报保留 90 天，临时 worktree 在终态后清理且不复制 `node_modules`。

开放 Loop PR 合入远程规则后可通过 GitHub 查询：
`is:pr is:open head:codex/loop- base:dev/sage-v7`。
