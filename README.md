# Sage — 个人 Web Coding Agent

> Sage 是一个本地优先的网页端 Coding Agent，用来在代码仓库里读代码、搜代码、改代码、跑命令、沉淀可复用 Skills，并通过 WebSocket 流式展示 agent 的工作过程。原 TourSwarm 旅游规划能力保留为 Sage 的第一个领域 Skill / benchmark 场景，不再作为主产品叙事。

## 当前定位

| 维度 | 说明 |
|------|------|
| 产品定位 | 个人 Web Coding Agent + 可扩展 Skills 框架 |
| 主界面 | Vue 3 三栏开发者控制台：Skills/MCP/模型、聊天与工具活动、文件树与预览 |
| Agent Runtime | 参考 Pico v3：workspace、tools、policy gates、engine、context、todo、plan mode、worker、trace |
| 领域示例 | `travel-planning`：ReAct 主 Agent + `generate_itinerary` LangGraph 工具 + 高德/和风/景点 MCP + 行程验证器 |
| 技术栈 | FastAPI、WebSocket、Vue 3、TypeScript、Pinia、LangChain、LangGraph、MCP、Redis、PostgreSQL、Qdrant |
| 语言 | Python 3.11+ / TypeScript |

## 为什么做 Sage

这个项目不是单纯的线上产品，而是一个面向作品集和工程沉淀的 Agent 项目。目标是把“能跑的 demo”逐步打磨成能讲清楚架构、能联调、能评测、能继续扩展的个人 coding agent。

核心亮点：

- **Agent loop**：模型输出被解析为工具调用或最终回答，再通过 WebSocket 流式推给前端。
- **工具治理**：路径安全、风险工具策略、plan mode 限制、patch 前 fresh read，避免盲改和越权。
- **开发者体验**：参考 Hermes Web UI 的 calm developer console，工具调用作为 metadata 折叠展示。
- **Skills 扩展**：`/review`、`/test`、`/commit`、`/travel-planning` 都通过 `SKILL.md` 沉淀为可复用 workflow。
- **评测路径**：后续可量化 coding task success rate、tool success rate、one-pass rate、P95 latency；旅游场景继续提供多约束验证数据。

## 核心架构

```text
Vue 3 Sage Console
  -> POST /api/v1/coding/session
  -> WS /api/v1/coding/{session_id}/stream
  -> FastAPI api/coding.py
  -> CodingRuntime
       ├─ WorkspaceContext：工作目录、路径安全、输出截断
       ├─ Tool Registry：list_files / read_file / search / run_shell / write_file / patch_file
       ├─ PermissionChecker + ToolPolicyChecker：写权限、plan mode、fresh read 策略
       ├─ Engine：model -> parse <tool>/<final> -> execute tool -> stream event
       ├─ ContextManager + CompactManager：prompt 组装和历史压缩
       ├─ Skills：bundled / user / project SKILL.md 发现
       ├─ Todo / Plan Mode / Worker：任务账本、只读规划、子 agent hook
       └─ .coding/：session events 与 run trace 本地持久化
```

旅游系统作为领域能力保留：

```text
Sage domain skill: travel-planning
  -> AgentRuntime（ReAct, DeepSeek）
       ├─ search_nearby / get_poi_detail / geocode / get_route
       ├─ get_weather / get_forecast
       ├─ search_attractions / search_scenic_spots / get_scenic_detail
       └─ generate_itinerary
            -> LangGraph: info -> recommend -> planning -> budget
            -> ItineraryVerifier: 时间 / 预算 / 空间 / 约束检查
```

这样项目有两条互补叙事：coding 任务体现线性读改测 agent loop，旅游任务体现复杂多约束任务里的多 Agent 编排和确定性验证。

## 仓库结构

```text
tour-agent/
├── api/                         # FastAPI app、Sage coding routes、保留的 travel routes
├── core/
│   ├── coding/                  # Sage coding runtime、tools、engine、skills、session traces
│   ├── memory/                  # Redis 短期记忆 + Mem0/Qdrant 长期记忆
│   ├── skill.py                 # travel-planning 领域 Skill 抽象
│   └── verifier.py              # 行程验证器接口与实现
├── agents/                      # 旅游领域 ReAct runtime 和 LangGraph 行程工具
├── mcp_servers/                 # 高德 / 天气 / 景点 MCP servers
├── frontend/                    # Vue 3 Sage console
├── evals/                       # 旅游 eval cases 和脚本
├── scripts/dev.sh               # 本地一键启动后端 + 前端
├── tests/                       # 后端、前端、agent、API、coding runtime 测试
├── docs/                        # plans、reviews、specs、落地记录
├── .vscode/                     # VS Code 共享任务与 FastAPI debug 配置
├── docker-compose.yml           # 本地 PostgreSQL + Redis + Qdrant
├── requirements.txt
└── .env.example
```

## 获取代码与远程 Git

当前远程仓库：

```text
git@github.com:ZeroMadLife/sage-agent.git
```

首次拉取：

```bash
git clone git@github.com:ZeroMadLife/sage-agent.git
cd sage-agent
```

如果你的机器没有配置 GitHub SSH key，可以先用 HTTPS：

```bash
git clone https://github.com/ZeroMadLife/sage-agent.git
cd sage-agent
```

常用协作命令：

```bash
git status
git pull --rebase origin main
git checkout -b codex/your-feature-name
git push -u origin codex/your-feature-name
```

## 本地启动

### 1. 创建环境

```bash
cd /Users/zeromadlife/Desktop/tour-agent

conda create -n sage-agent python=3.11 -y
conda activate sage-agent

python -m pip install --upgrade pip
pip install -r requirements.txt

cd frontend
npm install
cd ..

cp .env.example .env
```

如果你已经有 `tour-agent-phase1` 之类的 conda 环境，也可以继续用，只要依赖完整即可。

### 2. 配置 `.env`

Sage coding runtime 当前默认使用 DeepSeek 兼容接口：

```bash
DEEPSEEK_API_KEY=你的_deepseek_key
```

旅游领域 Skill 会用到额外模型和服务：

```bash
# 多 Agent 行程规划；也可以临时切到 deepseek:deepseek-chat
LLM_MODEL=doubao:Doubao-Seed-2.0-pro
DOUBAO_API_KEY=你的_豆包_key

# 高德 Web 服务 API
AMAP_API_KEY=你的_高德_key

# 和风天气 API
QWEATHER_API_KEY=你的_和风_key
QWEATHER_BASE_URL=https://你的-host.re.qweatherapi.com/v7
QWEATHER_GEO_URL=https://你的-host.re.qweatherapi.com/geoapi/v2
```

单元测试使用 mock，不消耗真实 API 额度。

### 3. 一键启动本地开发环境

依赖安装和 `.env` 准备好以后，推荐直接用一键脚本：

```bash
cd /Users/zeromadlife/Desktop/tour-agent
conda activate sage-agent
bash scripts/dev.sh
```

脚本会：

- 自动执行 `docker compose up -d` 启动 PostgreSQL、Redis、Qdrant。
- 启动 FastAPI：`http://127.0.0.1:8000`
- 启动 Vite：`http://127.0.0.1:5173`
- 自动设置 `VITE_API_PROXY_TARGET=http://127.0.0.1:8000`，让 REST 和 WebSocket 都走 Vite proxy。

常用覆盖项：

```bash
# 后端端口被占用时
BACKEND_PORT=8010 bash scripts/dev.sh

# 已经手动启动 docker compose 时
SAGE_SKIP_DOCKER=1 bash scripts/dev.sh
```

Windows 建议用 Git Bash 或 WSL 运行 `bash scripts/dev.sh`。如果必须用 CMD，请分别开两个窗口：

```bat
cd /d C:\path\to\sage-agent
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

```bat
cd /d C:\path\to\sage-agent\frontend
set VITE_API_PROXY_TARGET=http://127.0.0.1:8000
npm run dev -- --host 127.0.0.1 --port 5173
```

### 4. 手动启动中间件

```bash
cd /Users/zeromadlife/Desktop/tour-agent
docker compose up -d
docker compose ps
```

确认 PostgreSQL、Redis、Qdrant 都是 `Up` 或 `healthy`。

### 5. 手动启动后端

```bash
cd /Users/zeromadlife/Desktop/tour-agent
conda activate sage-agent
uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

如果 8000 被占用：

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
uvicorn api.main:app --host 127.0.0.1 --port 8010 --reload --env-file .env
```

### 6. 手动启动前端

```bash
cd /Users/zeromadlife/Desktop/tour-agent/frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

打开 Vite 输出的地址，通常是：

```text
http://127.0.0.1:5173/
```

如果后端使用 8010：

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev
```

本地开发推荐使用 `VITE_API_PROXY_TARGET`，让 Vite 代理 REST 和 WebSocket 到 FastAPI。不要优先设置 `VITE_API_BASE_URL=http://127.0.0.1:8000`，否则容易遇到浏览器 CORS 问题。

## IDE Debug 启动

### VS Code

仓库已包含共享配置：

- `.vscode/extensions.json`：推荐 Python / debugpy / Volar。
- `.vscode/tasks.json`：`docker: compose up`、`dev: backend`、`dev: frontend`、`dev: all`。
- `.vscode/launch.json`：`Debug FastAPI (uvicorn)`。

推荐流程：

1. 打开仓库根目录 `/Users/zeromadlife/Desktop/tour-agent`。
2. 选择项目 Python 解释器，例如 conda env `sage-agent`。
3. 先运行 VS Code Task：`docker: compose up`。
4. 在 Run and Debug 面板选择 `Debug FastAPI (uvicorn)` 启动后端断点调试。
5. 另开 VS Code Task：`dev: frontend` 启动前端。

如果只想一键启动，不需要断点：

```text
Terminal -> Run Task -> dev: all
```

### PyCharm

后端 Run Configuration：

| 配置项 | 值 |
|--------|-----|
| Type | Python |
| Module name | `uvicorn` |
| Parameters | `api.main:app --host 127.0.0.1 --port 8000 --reload --env-file .env` |
| Working directory | `/Users/zeromadlife/Desktop/tour-agent` |
| Interpreter | conda env `sage-agent` 或你当前项目环境 |

PyCharm 启动后端前先运行：

```bash
docker compose up -d
```

前端建议直接用 PyCharm Terminal：

```bash
cd /Users/zeromadlife/Desktop/tour-agent/frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

## 推荐联调输入

Coding agent：

```text
读 README.md 告诉我项目叫什么
```

```text
搜索 core/coding 里哪里定义了 patch_file
```

```text
/review
```

```text
/test
```

旅游领域 Skill：

```text
/travel-planning 帮我规划杭州2日游预算500元，喜欢美食和自然风光
```

`/travel-planning` 目前是 Sage 侧的 prompt 入口。成熟的旅游后端和 API 仍保留在 `/api/v1/chat` 与 `agents/` 下，用于回归测试和后续统一 runtime 集成。

## Runtime 产物

Coding session 和 run trace 会写到 `.coding/`：

```text
.coding/sessions/<session_id>.json
.coding/sessions/<session_id>.events.jsonl
.coding/runs/<run_id>/trace.jsonl
```

这些是本地运行产物，不应提交到 git。

## 质量检查

后端全量回归：

```bash
cd /Users/zeromadlife/Desktop/tour-agent
bash scripts/check.sh
```

Coding 后端快速检查：

```bash
pytest tests/core/coding tests/api/test_coding_routes.py -q
```

前端检查：

```bash
cd /Users/zeromadlife/Desktop/tour-agent/frontend
npm run test -- --run
npm run build
```

## GitHub 仓库改名

目标仓库名：`sage-agent`。

当前本机没有 `gh` CLI，所以 GitHub 网页侧需要手动改名：

```text
GitHub -> ZeroMadLife/poor-travel-agent -> Settings -> Repository name -> sage-agent
```

网页改名后，本地更新 remote：

```bash
cd /Users/zeromadlife/Desktop/tour-agent
git remote set-url origin git@github.com:ZeroMadLife/sage-agent.git
git remote -v
```

## 当前边界

- Sage UI 已成为主产品入口，旧旅游 chatbot UI 被隐藏。
- 旅游能力保留为 domain skill 和 regression asset，但还没有完全并入 `core/coding/` 的统一工具注册表。
- risky coding tools 还没有人工 approval UI。
- 文件预览是只读的，diff preview 和 inline editing 是后续工作。
- MCP 状态目前是配置可见性，不是 live health probe。
- benchmark 还未落地。

## Roadmap

- 高风险工具 approval UI：pending / allow / deny。
- `patch_file` 和 `write_file` 的 diff preview。
- active run 的 stop / cancel。
- 左栏 session list 和 run history。
- coding 与 domain skills 的统一 Skill registry。
- Coding benchmark：任务成功率、工具成功率、一次通过率、P95 latency。
- 把 travel-planning 进一步接入 Sage runtime，成为真正可调用的领域工具集。

## License

MIT
