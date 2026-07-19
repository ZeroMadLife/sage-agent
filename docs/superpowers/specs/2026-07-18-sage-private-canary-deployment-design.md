# Sage 私有 Canary 部署与公网迁移设计

> 状态：部署骨架已实现，待服务器执行
>
> 日期：2026-07-18

## 1. 决策摘要

Sage 首次上云采用单台阿里云 ECS 的私有 Canary，不直接开放公网。手机通过 Tailscale
Serve 访问服务器上的 HTTPS 入口，只有同一 Tailnet 的设备可见。`sagecompanion.cn`
完成实名认证和 ICP 备案后，再把入口切换为 Caddy 的 `80/443`，内部 Compose、认证、
备份和回滚机制不变。

正式公网发布前，必须补齐 Coding 路由的生产认证门禁、生产镜像和 Container Sandbox
配置；Tailscale 只能作为网络边界，不能替代应用授权。

## 2. 目标与非目标

### 目标

- 让用户能够从手机蜂窝网络访问 Sage 私有 Canary。
- 使用 Docker Compose 编排 Web、API、Worker、PostgreSQL/pgvector 和 Redis。
- 应用服务默认只绑定服务器本机网关，数据库和 Redis 不暴露宿主机端口。
- 通过受限 `sage-deploy` 用户执行可审计的预检、部署、健康检查和回滚。
- 保留后续接入域名、ICP、GitHub OAuth 和正式 HTTPS 的升级路径。
- 让飞书 Codex 负责 PR 和受限部署执行，让 Claude 负责只读审查。

### 非目标

- 不在本阶段直接将 ECS 作为无备案公网网站长期运营。
- 不授予 Codex root、任意 SSH shell、密钥读取、数据库迁移或防火墙权限。
- 不把 `local_workspace` 或宿主机任意目录作为公网 Coding 运行时。
- 不引入 Kubernetes、多机高可用、公开注册、计费或多租户平台。
- 不自动合并基础设施 PR 到 `main`。

## 3. 现状与必须修复的缺口

当前仓库已有 FastAPI/Vue、云认证基础、Container Sandbox 和 GitHub OAuth 适配器，但没有
生产 Dockerfile、生产 Compose、反向代理、备份恢复或部署工作流。现有 `docker-compose.yml`
仅用于本地 PostgreSQL/pgvector 与 Redis。

`api/coding.py` 的 owner 依赖只拒绝访问已有 owner 的会话，ownerless session 仍可在本地
开发兼容路径创建。正式公网前必须把生产环境的所有 Coding 创建、恢复、运行、文件、
审批和 WebSocket 路由接入统一的 `require_authenticated_user` 与 workspace ownership
依赖；未登录请求必须返回 `401`。

生产配置必须使用 HTTPS、真实 `APP_SECRET_KEY`、GitHub OAuth transaction/token 加密密钥、
Provider encryption secret，并关闭 development login。生产 Coding 强制使用 `container`
sandbox，不能静默回落到 `local_workspace`。

## 4. 目标拓扑

```text
手机
  -> Tailscale Serve（私有 HTTPS，首期）
  -> 127.0.0.1:8080 网关
  -> Sage Web
  -> Sage API
  -> Worker
  -> PostgreSQL + pgvector / Redis
  -> workspace / knowledge / backup volumes

正式公网阶段：
sagecompanion.cn -> Caddy 80/443 -> 同一内部 Compose 网络
```

### 服务边界

- **Gateway**：只负责 HTTPS 终止、Web/API 路由、WebSocket 转发和健康检查；首期只监听
  `127.0.0.1:8080`，不在 ECS 公网网卡绑定应用端口。
- **Web**：构建后的 Vue 静态资源，由 Gateway 提供同源访问。
- **API**：FastAPI；生产关闭 debug，所有用户和 Coding 路由走服务端 session/ownership。
- **Worker**：持久任务和知识摄取；与 API 共用内部网络，不提供外部端口。
- **PostgreSQL/pgvector**：canonical 数据和向量；仅 Compose 内部网络访问，持久化卷和备份。
- **Redis**：队列/事件短期状态，不保存 canonical 知识；仅内部网络访问。
- **Container Sandbox**：无网络、只读 rootfs、CPU/RAM/PID 限制、受控 workspace 挂载。
  API 仅通过 `0600` Unix 代理访问独立 `sage-sandbox` rootless daemon；部署 daemon 和模型
  工具都无法访问该 socket，sandbox daemon 也看不到 API 环境或服务容器。

## 5. 身份与安全边界

### 私有 Canary

- Tailscale ACL 是网络第一层，只允许用户自己的 Mac、手机和服务器节点。
- Sage 仍使用服务端 session；不把 Tailnet 身份当作应用 user ID。
- Canary 关闭公开注册，使用绑定邮箱的一次性邀请码直接换取 30 天 HttpOnly、Secure、
  SameSite session；同一账号最多 3 台活动设备，支持单设备撤销和整账号停用。
- 该登录方式只能在显式开关开启且 `CLOUD_FRONTEND_URL` 为 `.ts.net` 私有入口时启动；正式
  公网默认关闭。GitHub OAuth 后续绑定同邮箱时复用原账号。
- 手机 smoke test 必须从蜂窝网络执行，验证 Tailnet 外无法访问。

### 正式公网

- 使用 GitHub OAuth 邀请制登录，HttpOnly、Secure、SameSite session cookie。
- GitHub OAuth callback、`CLOUD_FRONTEND_URL` 和 Caddy hostname 全部使用备案后的 HTTPS 域名。
- 所有 Coding、Knowledge、Assistant、Provider、Proposal 和 WebSocket 路由统一要求登录与
  ownership；不能只保护部分详情路由。当前私有 Canary 的 Knowledge 数据仍是单用户本地
  store，只增加 session 门禁；完成 tenant scope 前不得切换到正式公网。
- `main`、生产密钥、工作区路径、Cookie 原文、Provider token 和模型凭据不进入模型 prompt、
  timeline、PR、飞书通知或普通日志。

### 服务器基线

- root 只用于一次初始化：安装 Docker/Tailscale、创建 `sage-deploy`、写入主机基线。
- 之后使用 SSH key，关闭密码登录；`sage-deploy` 只允许调用固定 `deployctl` 子命令。
- UFW 只开放 Tailscale/SSH 所需入口；正式公网阶段才开放 80/443。
- `/etc/sage/env` 权限为 `600`，密钥由人工注入，Codex 和 Claude 不读取原文。
- Docker 日志轮转、fail2ban、unattended upgrades、磁盘阈值和备份保留策略由主机配置完成。

## 6. 角色与变更流

| 角色 | 允许 | 禁止 |
| --- | --- | --- |
| 飞书 Codex | `codex/*` 分支修改部署文件、测试、创建中文 PR；合并后调用 `deployctl` | root、任意 SSH shell、读密钥、改认证/防火墙、直接推 `main` |
| Claude Reviewer | 只读审查 diff、配置、验证证据和健康摘要 | 修改、提交、推送、合并、服务器写操作 |
| systemd/定时器 | 同版本重启、健康/磁盘检查、备份、告警 | 自动升级镜像、自动迁移、改网络策略 |
| 用户 | Tailscale/GitHub OAuth 授权、密钥注入、域名备案、公网切换和高危合并 | 不需要每小时处理普通报告 |

基础设施 PR 必须进入 `dev/sage-v7`，经过 CI 与 Claude 只读审查后人工合并。基础设施、
认证、数据库、Docker、备份和公网入口永远不属于 Loop Tier A 自动合并范围；`main` 仍需完整
发布门禁。

## 7. 一次性部署流程

1. **预检**：检查 SSH、Docker、磁盘、内存、端口、Tailscale、远程 commit、镜像 registry、
   当前运行 tag 和备份目标；失败时不写服务器。
2. **主机初始化**：人工确认 root 操作；创建部署用户、SSH key、UFW/fail2ban、Docker、
   Tailscale 和 `/opt/sage` 目录。
3. **应用准备**：CI 测试、构建和扫描镜像，推送不可变 commit tag；服务器只拉取指定 tag。
4. **私有网关**：Compose 启动内部服务，网关绑定 loopback，Tailscale Serve 指向网关。
5. **数据准备**：执行应用认可的 migration 前备份；migration 失败则停止切换并保留旧版本。
6. **健康与手机验收**：检查 API/Web/Worker/DB/Redis、未登录 `401`、Container Sandbox、
   WebSocket 和手机蜂窝网络访问。
7. **交付**：写入部署摘要、镜像 tag、健康结果、回滚点和遗留风险；不把 secret 写入摘要。

## 8. 失败处理与回滚

- 预检、构建、拉取失败：旧服务不变。
- migration 失败：恢复 migration 前备份，禁止未经验证的自动 down migration。
- 健康检查失败：切回上一镜像 tag，保留失败日志和运行 ID。
- Tailscale/网关失败：服务维持 loopback，不开放公网端口。
- 备份、磁盘、sandbox 或 secret 校验失败：发布阻断并发中文摘要。
- 同类基础设施失败连续三次：暂停自动执行，等待人工诊断。
- 回滚命令必须接受显式、已存在的旧 tag，不能从自由文本拼接 shell。

## 9. 验收矩阵

### 私有 Canary

- 手机蜂窝网络通过 Tailscale HTTPS 打开 Sage，Tailnet 外无法访问。
- 未登录 Coding 创建/恢复/运行请求返回 `401`；同一 session 不能跨 user 使用。
- PostgreSQL、Redis、API 内部端口不在公网监听。
- Coding 使用 `container` sandbox，网络为 none，rootfs 只读，资源上限生效。
- 完成一次备份恢复和一次旧 tag 回滚演练。
- Chat、WebSocket 和一次受控 Coding smoke test 在移动端通过。
- 日志、PR、飞书消息无密钥、Cookie、身份证和 Provider token。

### 正式公网前置条件

- `sagecompanion.cn` 实名完成，ICP备案通过，域名 A 记录指向服务器。
- GitHub OAuth App、邀请制 user/session、Secure cookie 和 ownership 全量门禁通过。
- Caddy 80/443 自动 HTTPS、备份恢复、镜像扫描、迁移和回滚演练完成。
- 正式公网 smoke、故障注入和安全审查通过后，才允许开放 80/443。

## 10. 实施切片与停点

### Slice A：文档与主机 runbook

- 本设计、变量清单、bootstrap/部署/回滚/备份 runbook。
- 不触碰真实服务器，不包含密码、token 或身份证资料。

### Slice B：生产认证与部署骨架

- Coding 生产认证依赖和 ownership 回归测试。
- API/Web/Worker Dockerfile、生产 Compose、loopback 网关和健康检查。
- `deployctl preflight/apply/rollback` 只接受固定参数，默认 dry-run。

实现说明：当前代码没有可独立启动的 Worker 入口，首个 Canary 不伪造第二个 API 进程；
`KNOWLEDGE_JOBS_ENABLED=false`，待任务执行器拆出稳定入口后再加入 Compose Worker。

### Slice C：服务器私有 Canary

- 需要用户完成 Tailscale 登录/ACL、GitHub OAuth/密钥注入等外部授权。
- Codex 只能使用 `sage-deploy` 执行已合并 tag；Claude 只读审查。

### Slice D：域名与正式公网

- 等 `sagecompanion.cn` 审核和 ICP 完成后单独提交公网切换 PR。
- 公网切换前重新执行完整认证、备份、回滚和安全门禁。

## 11. 未完成边界

- 域名实名审核和 ICP 备案由用户完成。
- Tailscale 账号、ACL、GitHub OAuth App 和生产密钥由用户在外部控制台授权。
- 当前设计不会声称已经部署；只有服务器 smoke、备份恢复和回滚证据齐全后才算 Canary 交付。
- Knowledge store 尚未 tenant scope；私有 Canary 仅允许唯一受邀用户，正式公网切换前必须
  完成按 owner/workspace 隔离和跨用户回归测试。
