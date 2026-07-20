<p align="center">
  <img src="frontend/src/assets/sage-thinking-fallback.png" width="112" alt="Sage" />
</p>

<h1 align="center">Sage</h1>

<p align="center">
  <strong>Personal AI Learning Companion</strong><br />
  把个人项目、知识来源和真实实践连接成一条可追溯、可审核、可持续积累的成长路径。
</p>

<p align="center">
  <a href="https://github.com/ZeroMadLife/sage-agent/actions/workflows/quality.yml"><img src="https://github.com/ZeroMadLife/sage-agent/actions/workflows/quality.yml/badge.svg" alt="Sage Quality" /></a>
  <a href="https://github.com/ZeroMadLife/sage-agent/actions/workflows/backend-quality.yml"><img src="https://github.com/ZeroMadLife/sage-agent/actions/workflows/backend-quality.yml/badge.svg" alt="Backend Quality" /></a>
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/Vue-3-42B883" alt="Vue 3" />
  <img src="https://img.shields.io/badge/License-MIT-111827" alt="MIT License" />
</p>

Sage 是一个本地优先的个人 AI 学习与实践工作台。它以对话为入口，把代码仓库、
Markdown/Obsidian 知识、模型 Provider、Skills 和 MCP 工具组织到同一个可恢复的
Chat Harness 中。

Coding 没有被删除，而是成为 Sage 的 **Practice Engine**：用于阅读源码、修改代码、
运行测试和验证理解。Knowledge 负责来源摄取、Wiki 提案、检索与引用；所有可能改变
长期知识、代码或外部系统的操作都保留证据和人工控制点。

> **当前状态**：项目正在快速开发，适合本机使用、架构研究和受控私测。生产服务器
> 一键部署仍在建设中；不要把 Vite/FastAPI 开发端口直接暴露到公网。

## 产品界面

| Surface | 作用 | 当前状态 |
| --- | --- | --- |
| **今天** `/#/assistant` | 个人入口、近期会话、知识状态与下一步建议 | 已交付 |
| **Practice** `/#/coding/session/:id` | 代码阅读、编辑、测试、审批、Diff 与运行证据 | 已交付，Harness 2.0 默认启用 |
| **Knowledge** `/#/knowledge` | 来源摄取、知识图谱、Wiki 提案、检索与引用 | 已交付本地工作流 |
| **成长记录** `/#/evolution` | 汇总 Learning Evidence 与受控进化提案 | 阶段入口，继续建设 |
| **公开主页** `/#/public` | 展示经过筛选的项目、成长轨迹与公开资料问答 | 已交付公开展示 MVP；发布与公网 Agent 尚未接入 |
| **设置** `/#/settings/:section` | Provider、Skills、MCP、Memory、Context 与运行配置 | 已交付 |

## 现在能做什么

### 一个可恢复的 Chat Harness

- WebSocket 流式输出与 durable timeline；刷新或断线后可继续回放和恢复。
- 统一呈现 planning、reasoning、tool、approval、reply 和 terminal 阶段。
- Context budget、自动压缩、checkpoint、run trace 和长工具结果 artifact。
- 同一套 Harness 复用于 Assistant、Knowledge 和 Coding，不为每个页面重写 Agent loop。

### 受控的 Practice Engine

- `list_files`、`read_file`、`search`、`run_shell`、`write_file`、`patch_file` 等工作区工具。
- 路径 containment、fresh-read、权限模式、危险命令审批和写入 Diff。
- Plan Mode、Todo、Skills、MCP、子 Agent 和可中断运行。
- Harness 2.0 新会话默认启用；production/staging 只有配置 Container Sandbox 才允许启用。
- Container Sandbox 支持只读 rootfs、禁网、资源上限、受控 workspace mount 和退出清理。

### 可追溯的 Knowledge 工作流

- 摄取 Markdown、Obsidian 与受控文件来源，保留 immutable source snapshot。
- 异步任务、失败恢复、Wiki proposal、批准/拒绝/回滚和版本证据。
- Sparse/Dense hybrid retrieval、RRF、稳定 citation 与上下文预算。
- 对话可以提出 Note、Wiki 或 Memory Proposal，但不会静默改写已验证知识。

### 面向长期运行的工程闭环

- Provider 配置、密钥加密存储、能力探测和用量记录。
- 私有 Canary 可直接使用一次性邀请码登录；GitHub OAuth 保留为正式身份入口，并共享
  session/workspace ownership 边界。
- Loop Engineer 在独立 worktree 中扫描低风险问题；只有严格 Tier A 前端小修允许自动合并。
- 飞书 cc-connect 作为外部开发通道，继续遵守仓库内 Git、测试和部署门禁。

## 架构

```text
Browser / Mobile Web
  -> Vue 3 + Pinia
  -> REST + WebSocket
  -> FastAPI
       ├─ Assistant Home
       ├─ Chat Harness
       │    ├─ durable timeline / checkpoint / context compaction
       │    ├─ middleware / skills / MCP / subagents
       │    └─ approval / evidence / artifact / usage
       ├─ Practice Engine
       │    ├─ workspace + Git + file tools
       │    └─ local workspace | container sandbox
       ├─ Knowledge Engine
       │    ├─ source snapshots / ingest jobs / Wiki proposals
       │    └─ sparse + dense retrieval / RRF / citations
       └─ Cloud Control Plane
            ├─ GitHub OAuth / invite / ownership
            └─ encrypted model provider settings

State and infrastructure
  ├─ PostgreSQL + pgvector
  ├─ Redis Streams
  ├─ SQLite durable runtime stores
  └─ Git-backed workspace and knowledge artifacts
```

核心 Harness 以独立 Python package 维护在 `packages/sage_harness/`。Sage 产品层负责把
用户身份、工作区、Knowledge、Sandbox 和前端时间线适配到 Harness 的稳定端口。

## 快速开始

### 环境要求

- Python 3.12+
- uv
- Node.js 20+
- Docker Engine + Docker Compose v2
- macOS、Linux，或 Windows WSL

### 1. 获取代码

```bash
git clone https://github.com/ZeroMadLife/sage-agent.git
cd sage-agent
```

### 2. 安装依赖

```bash
bash scripts/bootstrap-dev-env.sh
source .venv/bin/activate

cd frontend
npm install
cd ..
```

### 3. 配置本地环境

```bash
cp .env.example .env
```

至少配置一个可用模型 Provider。比如：

```dotenv
DEEPSEEK_API_KEY=your_key
```

`.env`、Provider key、OAuth secret 和运行凭据都不得提交到 Git。

### 4. 启动

```bash
bash scripts/dev.sh
```

IDE 统一使用 `${workspaceFolder}/.venv/bin/python`。不要继续使用旧的
`tour-agent-phase1` Conda 环境；该环境是 Python 3.11，无法运行 Harness 2.0。

脚本会启动 PostgreSQL/pgvector、Redis、FastAPI 和 Vite：

- Sage Web：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:8000`
- Health：`http://127.0.0.1:8000/health`

开发环境默认会在 FastAPI 启动前执行幂等数据库迁移，IDE 直接运行和
`scripts/dev.sh` 都不再需要手动创建新增表。需要手动修复本地 schema，或执行
生产发布迁移时，使用：

```bash
python -m db.migrations
```

可用 `SAGE_AUTO_MIGRATE=false` 关闭开发环境自动迁移。生产环境始终禁用启动时
自动迁移，由部署流程在启动新版本前显式执行并验证。

更完整的环境变量、worktree 联调和 Knowledge Source 配置见
[Getting Started](docs/GETTING-STARTED.md)。

## 常用验证

```bash
# 后端、类型和基础质量门禁
bash scripts/check.sh

# Coding/Harness 定向回归
pytest tests/core/coding tests/core/harness tests/harness tests/api/test_coding_routes.py -q

# 前端测试与生产构建
cd frontend
npm run test -- --run
npm run build
```

GitHub Actions 会再次执行后端、前端、Ruff、mypy 和生产构建门禁。

## 仓库结构

```text
sage-agent/
├── api/                         # FastAPI routes、WebSocket 与云端控制面
├── core/
│   ├── coding/                  # Practice Engine、持久化、工具与运行协调
│   ├── harness/                 # Sage 到通用 Harness 的适配层
│   ├── knowledge/               # 摄取、图谱、检索、Wiki 与学习证据
│   ├── cloud/                   # 身份、Workspace 与 Provider 设置
│   └── loop_harness/            # Loop Engineer 控制器
├── packages/sage_harness/       # 可复用 Chat Harness 2.0 package
├── frontend/                    # Vue 3 产品界面
├── agents/                      # travel-planning 领域 Agent 与 LangGraph
├── mcp_servers/                 # 高德、天气、景点 MCP servers
├── evals/                       # Harness、Knowledge 与领域评测
├── tests/                       # 后端、API、契约与集成测试
├── docs/                        # 产品设计、实施计划、Review 与运维文档
└── docker-compose.yml           # 本地 PostgreSQL/pgvector + Redis
```

## 真实边界

- 当前 `docker-compose.yml` 只编排本地 PostgreSQL/pgvector 与 Redis，不是生产部署栈。
- Evolution 仍在建设；Public Profile 已提供静态展示与限定资料问答，但不能描述为已经接入公网 Harness 或完整发布系统。
- GitHub OAuth、ownership 和 Provider encryption 已有后端基础，但公网开放仍需要 HTTPS、
  production secrets、邀请制配置、限流、备份和恢复演练。
- 本地 `local_workspace` 允许工具作用于指定仓库；公网环境必须使用 Container Sandbox，
  不能把宿主机工作区直接暴露给浏览器任务。
- 原 TourSwarm 旅游规划能力作为第一个领域 Skill 和多约束 benchmark 保留，不再是主产品入口。

## 项目文档

- [Sage V7 产品设计](docs/superpowers/specs/2026-07-15-sage-v7-personal-assistant-knowledge-evolution-design.md)
- [Chat Harness 2.0 设计](docs/superpowers/specs/2026-07-16-sage-chat-harness-v2-design.md)
- [Loop Engineer](docs/loop-harness/README.md)
- [开发环境指南](docs/GETTING-STARTED.md)
- [开发协作约定](AGENTS.md)

## 分支与贡献

- `main`：通过完整发布门禁的版本。
- `dev/sage-v7`：当前集成分支。
- 功能与修复：在独立 worktree 的 `codex/*` 分支完成，通过 PR 合入开发分支。

提交前请保持职责单一，附中文 PR 说明，并提供与改动匹配的测试、构建和
`git diff --check` 证据。

## License

MIT
