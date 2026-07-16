# Loop Runbook

## 本机命令

```bash
sage-loopctl install
sage-loopctl status
sage-loopctl run
sage-loopctl digest
sage-loopctl pause
sage-loopctl enable --dry-run
sage-loopctl cleanup
```

默认目录：

- 状态：`~/.local/state/sage-loop/`
- worktree：`~/.local/share/sage-loop/worktrees/`
- 集成根目录：`/Users/zeromadlife/Desktop/tour-agent`

## 调度

- 小时任务：`17 * * * *`，超时 40 分钟，固定执行 `sage-loopctl run`
- 日报任务：`55 23 * * *`，固定执行 `sage-loopctl digest`
- 调度必须使用 `cc-connect cron --exec`，不能使用自由 Prompt。

## 排障

- `BLOCKED_ROOT_DIRTY`：完成或移动根目录人工改动，不得由 Harness stash/reset/clean。
- `PAUSED_POLICY_DRIFT`：人工审查策略相关 commit 后重新执行 `install --refresh-manifest`。
- `BLOCKED_CODEX`：检查受控 Codex 二进制路径和认证，不在日志输出凭据。
- 连续同类基础设施错误 3 次会自动暂停；修复后手动 `enable --dry-run`。

## 磁盘与卸载

`cleanup` 删除过期干净 worktree、30 天前日志和 90 天前已关闭状态。日志轮转上限约
35 MB。卸载先 pause，再删除 cron；默认保留 SQLite 审计数据，不自动 purge。
