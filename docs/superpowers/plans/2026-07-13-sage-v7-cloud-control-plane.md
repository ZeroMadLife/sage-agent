# Sage V7 云端控制面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将 Sage 从单用户本地私测安全地演进为邀请制 Web 服务：先交付认证和工作区所有权控制面，再分阶段增加云工作区、受限执行和公开项目展示。

**架构：** 浏览器只持有 HttpOnly、SameSite 会话 Cookie，服务端以随机且只存哈希的会话记录确认用户身份。用户、OAuth 身份、邀请、项目和工作区元数据集中存于 PostgreSQL；每个实际云工作区后续使用独立 volume 和 SQLite evidence store。所有 coding API 先通过 workspace 的 opaque ID 和 ownership 查询，绝不接受或暴露服务器文件路径。

**技术栈：** FastAPI、SQLAlchemy async、PostgreSQL、httpx GitHub OAuth、HMAC 签名短期 OAuth state、Docker Compose、GitHub Actions、Caddy/Nginx（部署阶段）。

---

## 非目标与安全基线

- V7.0 不提供浏览器终端、任意 shell、写入工具、用户自带绝对路径或本地未提交代码访问。
- OAuth access token、Git 凭据、Cookie 原文、Memory、私有会话、工作区文件和内部 trace 均不得进入模型 prompt、日志、事件时间线或 HR 页面。
- `main` 只保留可发布版本；V7 每项短分支先入 `dev/sage-v6` 的后继开发分支，完整门禁后再进入 `main`。
- GitHub OAuth 仅在设置了 `SAGE_GITHUB_CLIENT_ID`、`SAGE_GITHUB_CLIENT_SECRET`、`SAGE_PUBLIC_BASE_URL` 后启用。未配置时本地开发可使用受控 dev identity，生产环境 fail closed。

## 版本与阶段边界

| 阶段 | 交付物 | 进入条件 | 不包含 |
| --- | --- | --- | --- |
| V7.0 | 用户、邀请、GitHub OAuth、服务端 session、项目/工作区 ownership API | V6.9 timeline/reconnect 门禁通过 | clone、执行、终端、公开页 |
| V7.1 | GitHub App/OAuth repository 授权、只读 clone、workspace provider、Git facts | V7.0 跨租户契约测试通过 | 写工具、shell、Monaco |
| V7.2 | 容器化 runner、CPU/内存/时长/网络配额、取消、审计、Docker/Actions 私测部署 | V7.1 workspace isolation 测试通过 | Kubernetes、公开终端 |
| V7.3 | 策展式 HR 项目页、公开资料包、只读 RAG、发布审计 | V7.2 审计/备份/回滚通过 | 任何私有数据直连 |
| V8 | Local Companion、Code RAG、AST 图谱、增量索引 | V7 invite-only beta 的安全复盘完成 | - |

## 数据与权限模型

```text
User
├── AuthIdentity(provider, provider_subject)
├── LoginSession(token_hash, expires_at, revoked_at)
├── Membership(role)
└── Project(owner_user_id)
    └── Workspace(project_id, provider, lifecycle_state)
        ├── CodingSession(workspace_id)
        ├── Run(workspace_id, session_id)
        ├── Memory(workspace_id)
        └── Evidence SQLite volume
```

`user_id`、`project_id`、`workspace_id` 都是 UUID/opaque ID。控制面用单一 repository 层查询 `workspace_id + authenticated user`；路由和 ToolExecutor 不得自行按路径或 session ID 推导授权。工作区状态机只允许 `provisioning -> ready -> suspended -> deleting -> deleted`，失败时写审计事件。

## Phase 1：V7.0 认证与控制面

### Task 1：建立可测试的认证领域模型与数据迁移

**文件：**
- 创建：`core/cloud/auth/models.py`
- 创建：`core/cloud/auth/repository.py`
- 创建：`tests/core/cloud/auth/test_repository.py`
- 修改：`db/models.py`
- 修改：`db/migrations.py`

- [ ] 先写 `User`、`AuthIdentity`、`LoginSession`、`Invite`、`Project`、`Workspace` 的 repository 测试：identity 唯一、token 仅保存 SHA-256 哈希、撤销/过期不可读取、不同 user 的 workspace 查询严格为空。
- [ ] 在 PostgreSQL schema 中增加外键、唯一索引和删除约束；为测试提供 SQLite async engine fixture，不用真实生产 DB。
- [ ] 实现 repository 的事务接口：`consume_invite`、`get_or_create_identity`、`create_session`、`authenticated_workspace`。所有写入记录 `created_at/updated_at`，会话 token 的有效期和 revoke 都由查询层强制。
- [ ] 运行：`pytest tests/core/cloud/auth/test_repository.py -q`，预期全绿。
- [ ] 提交：`feat(sage-v7): add cloud control-plane identity store`。

### Task 2：实现 GitHub OAuth 与 HttpOnly 会话边界

**文件：**
- 创建：`core/cloud/auth/service.py`
- 创建：`api/cloud_auth.py`
- 创建：`tests/api/test_cloud_auth_routes.py`
- 修改：`api/main.py`
- 修改：`api/schemas.py`
- 修改：`core/config/settings.py`

- [ ] 先写 API 测试：未经认证访问 `/api/v1/cloud/me` 为 401；有效 session cookie 返回当前用户；logout 使同一 cookie 失效；state 过期、跨浏览器 state、OAuth error、无邀请用户均 fail closed。
- [ ] `POST /api/v1/cloud/auth/dev/login` 仅由 `app_env=development` 和显式 dev user 设置启用；生产路径返回 404。
- [ ] `GET /api/v1/cloud/auth/github/start` 创建单次 state（签名、PKCE verifier、5 分钟有效）；callback 仅通过 `httpx.AsyncClient` 调 GitHub token/user API，验证 state 后建立用户 identity 和服务器 session。
- [ ] session 原文仅写入 `Secure; HttpOnly; SameSite=Lax; Path=/` Cookie；DB 存 SHA-256 hash，响应 JSON 不回传 token。为本地 HTTP 明确允许 non-secure cookie，生产强制 `Secure`。
- [ ] 为所有 OAuth/会话结果写不含 token 的结构化审计事件；不得记录 authorization code、cookie 或 access token。
- [ ] 运行：`pytest tests/api/test_cloud_auth_routes.py tests/core/cloud/auth/test_repository.py -q`，预期全绿。
- [ ] 提交：`feat(sage-v7): add invite-only github authentication`。

### Task 3：把工作区 ownership 作为统一 API 依赖

**文件：**
- 创建：`core/cloud/workspaces/service.py`
- 创建：`api/cloud_workspaces.py`
- 创建：`tests/api/test_cloud_workspace_routes.py`
- 修改：`api/main.py`

- [ ] 先写测试：用户 A 只能列出/读取自己的项目和 workspace；猜到用户 B 的 opaque ID 得到 404；同项目协作者的只读/owner 行为清楚区分；未认证得到 401。
- [ ] 实现仅控制面的 create/list/get；workspace 创建只写 `provisioning` metadata，不创建目录、不 clone、不启动 runner。
- [ ] 提供 `require_authenticated_user`、`require_workspace_access` 依赖；此依赖是后续 coding 路由接入的唯一授权入口。
- [ ] 运行：`pytest tests/api/test_cloud_workspace_routes.py -q`，预期全绿。
- [ ] 提交：`feat(sage-v7): enforce cloud workspace ownership`。

### Task 4：控制面安全门禁与运行说明

**文件：**
- 创建：`docs/runbooks/07-V7-认证与邀请制私测.md`
- 创建：`docs/runbooks/08-V7-服务器准备与最小部署.md`
- 修改：`.env.example`
- 修改：`docker-compose.yml`
- 修改：`.github/workflows/ci.yml`

- [ ] 明确 GitHub OAuth App callback URL、环境变量、Cookie 域、邀请生成流程、rotate secret、会话撤销和开发/生产差异。
- [ ] 准备单台 VPS 的最小规格：2 vCPU / 4 GB RAM / 60 GB SSD、Ubuntu 24.04、Docker Engine、域名、HTTPS；不把 SSH 22 暴露给应用容器，不将 DB 端口公开。
- [ ] CI 运行 migrations、认证/ownership tests、ruff、mypy 与前端 build；部署仍在 V7.2 以后，当前只验证镜像和配置。
- [ ] 验证：本地 compose 不使用真实 OAuth secret 也可启动；生产缺失 session/OAuth secret 时拒绝启动。
- [ ] 提交：`docs(sage-v7): document invite-only cloud control plane`。

## Phase 2：V7.1 云工作区与 GitHub 导入

### Task 5：实现 WorkspaceProvider 和只读 Git 导入

**文件：**
- 创建：`core/cloud/workspaces/provider.py`
- 创建：`core/cloud/github/service.py`
- 创建：`tests/core/cloud/workspaces/test_provider.py`
- 创建：`tests/api/test_cloud_github_routes.py`

- [ ] 用户从浏览器选择 repository 时只提交 GitHub repository node ID，服务端在授权范围中解析 clone URL。
- [ ] checkout 路径从 `workspace_id` 派生并位于受控根目录；拒绝所有客户端路径、符号链接逃逸、私有凭据文件和 Git hooks。
- [ ] 首版只读 provider 只提供 HEAD、status、文件读取和 bounded diff；每个 workspace 独立 volume/SQLite，不共享 `.git`。
- [ ] 跨用户相同 repo 必须生成不同 workspace；测试工作区 A 永远无法读 B 的 Git/evidence/memory。

## Phase 3：V7.2 Sandbox、CI/CD 与邀请私测

### Task 6：加入受限 runner 和生产部署

**文件：**
- 创建：`infra/docker/sage-api.Dockerfile`
- 创建：`infra/compose/production.yml`
- 创建：`infra/proxy/Caddyfile`
- 创建：`.github/workflows/ci.yml`
- 创建：`.github/workflows/deploy.yml`

- [ ] runner 采用每 workspace/run 独立容器、只读 root filesystem、非 root user、network allow-list、CPU/RAM/PID/时长上限、无 Docker socket、无宿主 workspace bind mount。
- [ ] 先实现只读工具，写/命令工具必须有 approval、审计、取消和 quota；xterm 只在这些门禁完成后呈现。
- [ ] GitHub Actions 依次 test/build/image scan/push/SSH deploy/health check，失败自动保持旧容器；数据库迁移必须先备份并有 rollback runbook。
- [ ] 单机私测仅 Docker Compose；Kubernetes 作为独立扩容学习项目，不是首发依赖。

## Phase 4：V7.3 HR/面试官公开窗口

### Task 7：策展式公开资料包与只读 RAG

**文件：**
- 创建：`core/public_portfolio/manifest.py`
- 创建：`api/public_portfolio.py`
- 创建：`frontend/src/views/PublicPortfolioView.vue`
- 创建：`tests/api/test_public_portfolio_routes.py`

- [ ] 公开内容由 versioned manifest 明确选择：项目摘要、架构图、版本里程碑、演示记录、脱敏学习笔记和可公开代码片段。
- [ ] HR 页只访问 published manifest 和专属公开索引；查询必须有引用，不能 fallback 到内部 session、Git、Memory、RAG、日志或对象存储。
- [ ] 发布/撤回需要 owner 审批和 audit log；每次导出执行 secret/PII 扫描，发现风险则拒绝发布。

## 验收矩阵

- [ ] 认证：cookie 不可由 JS 读取；token/hash 不出现在日志、响应、timeline、模型 prompt；失效、撤销和 OAuth state 重放全部失败。
- [ ] 租户：每条项目/工作区/session/run/memory/index 查询有 user/project/workspace scope；A 无法以任意 ID 访问 B。
- [ ] 工作区：浏览器永不提交路径；每 workspace 独立 checkout、SQLite 和资源配额；无未授权 shell/terminal。
- [ ] 运维：HTTPS、健康检查、结构化日志、错误追踪、备份/恢复、镜像扫描、回滚演练完成后才邀请真实用户。
- [ ] 公开页：公开资料与私有数据之间有物理 query boundary，误配置回归测试为零泄漏。

## 本轮执行选择

本轮只执行 Task 1 到 Task 3 的设计与实现准备，先把控制面边界做对；Task 2 的 GitHub callback adapter 可写入并用 mock 测试，但在 GitHub OAuth App 的 Client ID/Secret 和真实 HTTPS 域名就绪前，不对外启用。
