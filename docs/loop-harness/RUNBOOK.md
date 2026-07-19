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

# Tier A 自动合并：仅低风险前端小修，内测、Claude 审查和 GitHub CI 全通过后合入开发分支
sage-loopctl enable --auto-merge-tier-a
sage-loopctl cleanup
```

Scanner/Fixer 固定使用 Controller 声明的 `gpt-5.6-luna`、低推理强度和 Honglin
Codex 网关。运行时继续使用 `--ignore-user-config`，并关闭插件、远程插件、浏览器和生图
能力；只复用 `$CODEX_HOME/auth.json` 的现有登录状态，不读取个人配置来扩大权限。
`PR_CANARY` 与 `AUTO_MERGE_TIER_A` 还要求 `gh auth status` 能访问私有仓库，并要求 cc-connect 内部项目
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

- 小时任务：`17 * * * *`，超时 40 分钟，固定执行 `sage-loopctl run --notify-session ...`
- 日报任务：`55 23 * * *`，固定执行 `sage-loopctl digest --notify-session ...`
- 调度必须使用 `cc-connect cron --exec`，不能使用自由 Prompt。
- 两个 cron 均设置 `mute=true`，防止 shell 的 `(no output)` 成功回执刷屏；Harness 只在
  finding、Draft PR、首次阻断或日报存在时通过 `cc-connect send --stdin` 主动发送中文摘要。
  通知正文走 stdin，不进入进程参数；目标 session 只保存在本机 cc-connect cron 配置中。

## 飞书网关 watchdog

watchdog 独立于 cc-connect 内部 cron，由 macOS `launchd` 每 5 分钟运行一次。安装时从
现有 Loop cron 读取通知目标，不需要把飞书 session 写进命令历史：

```bash
python scripts/cc_connect_watchdog.py install \
  --source-cron-id <小时任务 ID> \
  --expected-cron-id <小时任务 ID> \
  --expected-cron-id <日报任务 ID>

sage-cc-connect-watchdog doctor
sage-cc-connect-watchdog status
```

`doctor` 只检查不重启；`status` 读取最近一次状态。正常检查静默，daemon/socket 异常时
限频重启，cron 缺失时只发一次告警，恢复后只发一条中文消息。若需卸载，执行
`sage-cc-connect-watchdog uninstall`；默认保留
`~/.local/state/sage-cc-connect-watchdog/` 中的审计状态。

## 排障

- `BLOCKED_ROOT_DIRTY`：完成或移动根目录人工改动，不得由 Harness stash/reset/clean。
- `PAUSED_POLICY_DRIFT`：人工审查策略相关 commit 后重新执行 `install --refresh-manifest`。
- `BLOCKED_CODEX`：检查受控 Codex 二进制路径和认证，不在日志输出凭据。
- `BLOCKED_GITHUB_AUTH`：先确认交互终端的 `gh auth status` 正常；若仅 cc-connect cron
  失败，重新执行 `sage-loopctl install --refresh-manifest`，确保 launcher 显式传递 `HOME`，
  再人工执行 `sage-loopctl enable --pr-canary`。
- `BLOCKED_REVIEWER`：检查 cc-connect daemon、`sage-loop-review` 和 synthetic relay binding。
- watchdog 显示 `cron_jobs=false`：核对 `cc-connect cron list` 中的 Loop 小时任务与日报
  任务 ID；任务重建后重新运行 install，刷新本机私有配置。
- watchdog 反复 `UNHEALTHY`：先执行 `sage-cc-connect-watchdog doctor` 定位 daemon、
  `api_socket` 或 `cron_jobs`，再检查 `~/.cc-connect/logs/cc-connect.log`。15 分钟冷却期内
  不会重复重启。
- `BLOCKED_GITHUB_CHECKS`：CI 未全绿，保留 PR，不得人工绕过后让 Loop 继续合并。
- `BLOCKED_BASE_DRIFT` / `BLOCKED_PR_HEAD_DRIFT`：开发分支或 PR head 已变化，原验证失效，
  PR 转人工处理。
- 连续同类基础设施错误 3 次会自动暂停；修复后手动选择 `enable --dry-run` 或
  `enable --shadow-write`。`enable --pr-canary` 与 `enable --auto-merge-tier-a` 必须由人工显式执行。

## 磁盘与卸载

`cleanup` 删除过期干净 worktree、30 天前日志和 90 天前已关闭状态。日志轮转上限约
35 MB。卸载先 pause，再删除 cron；默认保留 SQLite 审计数据，不自动 purge。
