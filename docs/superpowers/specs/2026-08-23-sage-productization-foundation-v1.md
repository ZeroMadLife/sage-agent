# Sage 产品化基础 V1 规格

## 1. 目标

Sage 从“可运行的 Agent 工程项目”进入“用户可以稳定启动并完成第一次学习任务的产品”。V1 优先解决首次启动、环境预检、服务编排和产品入口，不在这一阶段扩展新的 RAG 算法。

目标用户是希望在本机使用 Sage 进行学习、研究或编码实践的个人用户。用户不需要先理解 Python 虚拟环境、前端依赖或 Docker Compose 的细节，能够通过一个入口完成本地工作台启动。

## 2. 用户旅程

```text
首次下载
  -> 一键初始化运行环境
  -> 创建本地 .env（不覆盖已有配置）
  -> 检查 Python、Harness、Node、npm 和可选 Docker
  -> 启动 PostgreSQL/Redis/SearXNG（可选跳过）
  -> 启动 FastAPI + Vue
  -> 打开 Assistant
  -> 创建第一个学习/实践任务
```

配置检查失败时，命令必须给出可执行的修复提示，并且不能打印 API key、口令或 OAuth secret。

## 3. 交付范围

### 3.1 本阶段交付

- 新增 `scripts/quickstart.sh` 作为本地产品唯一启动入口。
- 首次运行时创建 `.env`，文件权限为 `0600`，已有 `.env` 不覆盖。
- 自动复用 `scripts/bootstrap-dev-env.sh` 创建或修复 Python 环境。
- 自动安装前端依赖；已有 `node_modules` 时不重复安装。
- 支持 `--check` 只做预检，不启动 Docker、API 或前端。
- 支持 `--start` 启动现有 `scripts/dev.sh`，保留现有端口和环境变量契约。
- 产品入口默认关闭 Uvicorn 热更新，避免虚拟环境文件变化导致服务重启；直接运行 `dev.sh` 时仍保留开发热更新。
- README 和 Getting Started 提供最短启动路径。

### 3.2 不在本阶段交付

- Passkey 或公开注册；本阶段后续已补齐受控 TUI/桌面 Bearer token 骨架，仍不提供公开注册。
- TUI、Electron、Tauri 或移动端客户端。
- 计费、邀请裂变、公开 Landing Page 和增长埋点。
- 修改 Agent Harness、RAG、Memory、Sandbox 的运行时行为。
- 把私有 Canary 发布流程改造成无审核公网一键部署。

## 4. 部署配置

| 配置 | 用途 | 入口 |
| --- | --- | --- |
| local | 个人本地开发与学习 | `scripts/quickstart.sh` |
| private-canary | 受控服务器验证 | `scripts/deployctl.py` + `infra/compose/private-canary.yml` |
| public | 未来公开产品 | 另行设计，不复用本地 `.env` 和开发登录 |

本阶段只实现 `local`。`private-canary` 继续使用 immutable SHA、生产环境校验和人工确认；`public` 必须等认证、数据隔离、备份和回滚方案完成后再设计。

## 5. 安全与兼容性约束

- 不使用 `eval`、`source .env` 或 `curl | sh`。
- 不覆盖用户已有的 `.env`、数据库、知识库或工作区。
- 不在输出中打印环境变量值，只显示已配置的 Provider 名称。
- Docker 不可用时，`--check` 仍可执行；`--start` 给出明确提示，并允许显式使用现有 `SAGE_SKIP_DOCKER=1`。
- 复用 `SAGE_ENV_FILE`、`SAGE_PYTHON`、`SAGE_SKIP_DOCKER` 和现有端口变量，不引入第二套配置命名。
- quickstart 只负责本地启动，不声明公网可用、生产 SLA 或自动部署到远程服务器。

## 6. 验收标准

1. 空的本地 checkout 执行 `bash scripts/quickstart.sh --check`，能够创建 `.env` 并完成环境预检。
2. 已存在 `.env` 时，脚本不修改文件内容和权限之外的用户配置。
3. 使用临时环境文件注入测试 Provider 时，输出只显示 Provider 名称，不出现 secret。
4. 缺少 Python、uv、Node 或 npm 时，脚本以非零状态退出，并说明安装动作。
5. `bash scripts/quickstart.sh --start` 只调用现有 `scripts/dev.sh`，不复制 API/前端启动逻辑。
6. `bash -n scripts/quickstart.sh`、quickstart 脚本契约测试、前端生产构建和 `git diff --check` 通过。
7. 真实用户可以从 Assistant 首页创建学习会话；同一会话第二轮能够读取上一轮诊断和任务台账；刷新会话 URL 后历史消息仍可恢复。

## 7. 后续产品化切片

### P1：统一认证控制面（本轮已实现最小骨架）

保留 Web 的 HttpOnly server-side session，同时为 TUI/桌面客户端增加短期 access token、refresh token rotation、设备撤销和统一 user/project/workspace scope。当前已实现 `/api/v1/cloud/auth/device/login`、`/token/refresh`、`/token/revoke`，JWT 只解决跨客户端 API 传输，不替代服务端权限和资源 ownership 校验。公开注册、Passkey、TUI 交互界面和计费仍是后续工作。

### P2：TUI 薄客户端

先按 Terminal UI 理解，提供登录、会话列表、启动学习任务、查看 Timeline、处理审批和恢复任务；Harness、RAG、Memory 和 Sandbox 仍只在服务端运行。

### P3：首次学习任务与反馈闭环

增加首次启动引导、学习目标、任务计划确认、反馈入口和记忆管理，让用户从打开产品到完成一次可复核学习任务形成完整闭环。

### P4：公开部署与增长

补充公开注册、邀请/分享、匿名体验升级、用量限制、计费边界、备份恢复、灰度发布和可观测性。该阶段必须独立 PRD，不把个人本地工作区直接暴露到公网。
