# Sage 本地 CI/CD 与 Canary 可用性

> 适用范围：`dev/sage-v7` 私有 Canary。`main` 仍然只保留可发布版本，不由本流程自动合入或发布。

## 1. 运行模型

```text
PR -> GitHub Actions 全量 CI -> 合入 dev/sage-v7
                                  |
                                  v
Mac LaunchAgent（每 15 分钟）
  -> 读取 origin/dev/sage-v7 完整 SHA
  -> 检查 python/backend-quality/frontend-quality 全部成功
  -> SSH 到 sage-agent-canary
  -> deployctl preflight -> fetch/checkout SHA -> deployctl apply --execute
  -> 备份、迁移、健康检查、失败回退
```

本机控制器是 `scripts/canaryctl.py`，服务器控制器是
`scripts/deployctl.py`。前者不读取 `/etc/sage/env`，不构建镜像，也不拥有数据库或
Docker socket；后者只在 `sage-deploy` 的 rootless Docker 环境中执行固定部署流程。

## 2. 自动化边界

- 只部署完整的 `dev/sage-v7` SHA；不接受 `main`、分支名、短 SHA 或自由 shell 文本。
- GitHub 必须同时存在并通过 `python`、`backend-quality`、`frontend-quality` 三项检查。
- Canary 代码目录必须干净；部署前先执行服务器 `deployctl preflight`。
- 服务器 `deployctl apply` 负责不可变镜像、PostgreSQL 备份、幂等 migration、健康检查和
  应用失败回退。数据库 down migration 和数据库恢复仍然是人工高危操作。
- 可用性巡检只报告异常和恢复，不自动重启服务、不自动回滚、不开放公网入口。
- 部署控制面通过公网 IP 使用专用 SSH key、固定 host key 和 keepalive；应用 HTTPS、API、
  数据库与 Redis 仍不开放公网端口，只能从 Tailnet 访问。
- API 镜像使用独立 pip 缓存挂载，首次依赖下载最多允许 60 分钟；超时只中止构建，不切换
  当前运行服务。
- ECS 默认使用实测可达的阿里云 PyPI 镜像；可通过服务器 0600 环境文件中的
  `SAGE_PIP_INDEX_URL` 覆盖，不在仓库保存凭据。
- ECS 拉取固定开发分支遇到瞬时 TLS/网络错误时最多重试 3 次，每次间隔 10 秒；仍失败才计入
  连续部署失败并保留当前服务。
- HTTPS 探针显式绕过本机 HTTP 代理，避免 VPN/代理变量把 Tailnet 内地址误判为不可用。
- 自动部署连续失败 3 次后暂停，只发送一次中文告警；人工修复后重新启用。
- 状态写入 `~/.local/state/sage-canary/` 的 0600 JSON，LaunchAgent 日志不生成按小时的
  Markdown 报告，因此不会因为巡检次数导致磁盘线性增长。

## 3. 首次安装（Mac）

先确认 Tailscale 已连接、`gh auth status` 可访问仓库，并确认现有 Loop 小时 cron 仍然
存在。安装命令从该 cron 读取飞书通知目标，写入本机 0600 配置，不把 session 写进仓库：

```bash
cd /Users/zeromadlife/Desktop/tour-agent
python3 scripts/canaryctl.py install \
  --source-cron-id <Loop 小时任务 ID> \
  --interval-seconds 900
```

安装结果：

- LaunchAgent：`com.sage.canaryctl`
- 控制器入口：`~/.local/bin/sage-canaryctl`
- 私有配置和状态：`~/.local/state/sage-canary/`
- 配置权限：`0600`；状态目录权限：`0700`

安装后检查：

```bash
sage-canaryctl doctor
sage-canaryctl status
sage-canaryctl check
launchctl print "gui/$(id -u)/com.sage.canaryctl"
```

`check` 只做一次 HTTP 健康检查和服务器服务摘要；`run` 才会在健康时继续检查 CI 并同步
最新开发分支。LaunchAgent 默认每 15 分钟执行一次 `run`。

## 4. 日常查看

正常情况下飞书没有心跳刷屏，只在以下事件发送中文消息：

1. HTTP 或服务器服务从健康变为异常；
2. 服务从异常恢复；
3. 自动部署连续失败 3 次并暂停。

本机查看最近状态：

```bash
sage-canaryctl status
```

服务器查看脱敏服务摘要（不会输出 `/etc/sage/env`）：

```bash
ssh -i ~/.ssh/id_ed25519_sage_deploy sage-deploy@121.40.185.188 \
  'export DOCKER_HOST=unix:///run/user/1002/docker.sock; \
   python3 /opt/sage/app/scripts/deployctl.py --env-file /etc/sage/env status'
```

## 5. 故障处理

### CI 未完成或认证不可用

控制器保持旧版本不动，状态会记录为 CI/认证门禁失败。先在 Mac 运行：

```bash
gh auth status
gh run list --branch dev/sage-v7 --limit 5
```

确认三项检查全绿后，下一轮 `run` 会自动重试。不要通过短 SHA 或手工改状态绕过门禁。

### Tailscale、SSH 或健康检查失败

应用健康探针仍通过 Tailnet HTTPS；部署 SSH 独立使用公网 IP 的专用 key。先判断是 Tailnet
访问异常还是 SSH 控制面异常，再运行：

```bash
sage-canaryctl check
sage-canaryctl run
```

### 连续失败后自动暂停

先查看 `sage-canaryctl status` 和服务器 `deployctl status/preflight`，确认磁盘、rootless
Docker、备份和迁移状态。修复根因后显式恢复：

```bash
sage-canaryctl resume
sage-canaryctl run
```

`resume` 只清除连续部署失败暂停，不删除其他审计状态。卸载默认保留状态文件，便于复盘；
不要使用 `--purge-state` 清除证据。

### 紧急停用自动部署

```bash
sage-canaryctl uninstall
```

这只停止 Mac LaunchAgent，不会停止服务器上的 Sage。服务器停机、回滚和数据库恢复必须
由人工按 [私有 Canary 部署 runbook](09-Sage私有Canary部署.md) 执行。

## 6. 变更门禁

- 飞书 Codex 只在 `codex/*` worktree 修改并提交中文 PR；Loop 不直接修改服务器。
- 基础设施、认证、数据库、Docker、备份和公网入口 PR 不属于 Loop Tier A 自动合并范围。
- 所有小版本必须跑匹配的测试、生产构建和 `git diff --check`，并把 source commit、验证
  证据、关闭风险和遗留问题写入 Obsidian `sage-learning`。
- Canary 只是私有体验环境；域名、ICP、公网 80/443 和正式发布另走独立发布门禁。
