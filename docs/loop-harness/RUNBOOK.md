# Loop Runbook

## 本机命令

```bash
sage-loopctl install
sage-loopctl status
sage-loopctl run
sage-loopctl digest
sage-loopctl pause
sage-loopctl enable --dry-run

# Phase 2 shadow：允许隔离 worktree 生成未提交前端 diff，不会 push/提 PR/合并
sage-loopctl enable --shadow-write

# 人工 PR canary：允许中文 Draft PR + Claude 审查，仍不会自动合并
sage-loopctl enable --pr-canary
sage-loopctl cleanup
```

Scanner/Fixer 固定使用 Controller 声明的 `gpt-5.6-luna`、低推理强度和 Honglin
Codex 网关。运行时继续使用 `--ignore-user-config`，并关闭插件、远程插件、浏览器和生图
能力；只复用 `$CODEX_HOME/auth.json` 的现有登录状态，不读取个人配置来扩大权限。
`PR_CANARY` 还要求 `gh auth status` 能访问私有仓库，并要求 cc-connect 内部项目
`sage-loop-review` 可用；凭据只由各 CLI 自己读取，不进入模型 Prompt、日志或 SQLite。

`sage-loop-review` 不绑定主飞书会话：使用 `plan` 模式，work_dir 固定为
`~/.local/state/sage-loop/reports`，只允许 `Read/Grep/Glob`，显式禁止 Shell、写文件、联网和
GitHub。每轮使用独立 synthetic relay session，`relay.visibility=none`。本机 cc-connect 1.4.1
要求每个项目至少有一个 platform，因此该项目只挂无外部注册/轮询的本机随机回调适配器；
不得复用现有 `sage-review` 的飞书应用或 YOLO 权限。

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
- `BLOCKED_GITHUB_AUTH`：完成 `gh` 最小权限认证；未认证时保持 `SHADOW_WRITE`。
- `BLOCKED_REVIEWER`：检查 cc-connect daemon、`sage-loop-review` 和 synthetic relay binding。
- 连续同类基础设施错误 3 次会自动暂停；修复后手动选择 `enable --dry-run` 或
  `enable --shadow-write`。`enable --pr-canary` 必须由人工显式执行。

## 磁盘与卸载

`cleanup` 删除过期干净 worktree、30 天前日志和 90 天前已关闭状态。日志轮转上限约
35 MB。卸载先 pause，再删除 cron；默认保留 SQLite 审计数据，不自动 purge。
