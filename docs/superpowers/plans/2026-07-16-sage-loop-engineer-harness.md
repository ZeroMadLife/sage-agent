# Sage Loop Engineer Harness 实施计划

> 日期：2026-07-16
>
> 基线：`490e0d1`
>
> 本次交付：Phase 0 仓库门禁骨架 + Phase 1 只读 dry-run

## 目标

在当前 Mac 安装一个可全天调度、失败关闭、可审计且磁盘有硬上限的 Loop Controller。
首个 24 小时只允许 `NO_OP`、`REPORT`、`BLOCKED`，不改代码、不 push、不建 PR、
不自动合并。

## 实施任务

1. 更新 `AGENTS.md`，把根目录改为集成工作区，开发统一走 worktree + PR。
2. 建立 `docs/loop-harness/` 的索引、Policy、SOP、Prompt、Review 和 Runbook。
3. 在 `core/loop_harness/` 实现 SQLite state、lease/fencing、manifest、Git worktree、
   Codex dry-run、日报与保留策略。
4. 提供 `scripts/loopctl.py` 和安装脚本，支持 `install/status/run/digest/enable/pause/cleanup`。
5. 增加后端与前端 GitHub CI；远程 required checks 在 workflow 合入后单独验收。
6. 使用临时 Git 仓库和 fake Codex 覆盖并发、过期 lease、策略漂移、根目录脏、
   结构化结果、worktree 清理、日报幂等和磁盘保留。
7. 安装本机依赖并通过 `cc-connect cron --exec` 部署小时任务与每日报告。

## 启用边界

- Phase 1 可以在根目录脏时安装和启动，但每轮必须返回 `BLOCKED_ROOT_DIRTY`，不得创建
  Worker worktree。
- 只有根目录恢复干净、CI 与 GitHub 规则验收、dry-run 连续 24 小时无越权后，才进入
  Phase 2 Draft PR canary。
- Tier A 自动合并继续保持禁用，必须经过独立 Phase 3 canary。

## 验证

- 聚焦：`pytest tests/core/loop_harness tests/scripts/test_loopctl.py -q`
- 后端：`ruff check`、`mypy`、`pytest tests/ -q`
- 前端：`npm run test -- --run`、`npm run build`
- 仓库：`git diff --check`、保护路径与 secret 检查
- 本机：`loopctl status`、一次手动 dry-run、daemon/cron 状态、状态目录体积

## 回滚

先执行 `loopctl pause`，再删除两个 cc-connect cron；需要完全卸载时停止 daemon，移除
本机 launcher。SQLite 和日志默认保留用于审计，只有人工显式执行 purge 才删除。
