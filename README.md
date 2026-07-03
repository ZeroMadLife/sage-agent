# TourSwarm — 两段式智能旅游 Agent

> 面向学生穷游 / 周末周边游的个人旅游助手。当前阶段不是上线运营，而是把本地 demo 打磨成一份能讲清楚架构、能真实联调、能继续量化评测的产品原型。

## 当前定位

| 维度 | 说明 |
|------|------|
| 产品方向 | 个人旅游 Agent：日常问答、附近查询、天气查询、复杂行程规划 |
| 核心差异化 | ReAct 主Agent + `generate_itinerary` 工具包装多Agent图 + 自研 MCP 工具 + 预算约束 + 记忆/验证器 |
| 当前阶段 | Phase 4.5：两段式 Agent 体验重构，本地可联调，不是生产上线 |
| 技术栈 | FastAPI + WebSocket + LangChain ReAct + LangGraph + MCP + Mem0/Qdrant + Redis + Vue3 |
| 语言 | Python 3.11+ / TypeScript |

## 核心架构

Phase 4 之前是“用户输入 -> 多Agent图 -> 一次性行程”。Phase 4.5 改成两段式个人助手：

```text
Vue3 Chat UI
  -> POST /api/v1/chat 创建 session
  -> WebSocket /api/v1/chat/{session_id}/stream 长连接多轮对话
  -> FastAPI
  -> TourAgent 主Agent（ReAct, DeepSeek）
       ├─ 普通聊天：直接回复
       ├─ 附近查询：search_nearby / get_poi_detail / geocode / get_route
       ├─ 天气查询：get_weather / get_forecast
       ├─ 景点查询：search_attractions / search_scenic_spots / get_scenic_detail
       └─ 复杂规划：generate_itinerary 工具
              -> LangGraph 多Agent图
                   -> Info Agent + Recommend Agent
                   -> Planning Agent
                   -> Budget Agent
              -> 结构化 Itinerary
```

主Agent只知道自己调用了一个叫 `generate_itinerary` 的工具；工具内部才启动 Phase 2 的多Agent协作。这让产品体验更像“个人助手”，而不是每次都强行跑完整规划流。

## Phase 4.5 本轮变动

| 任务 | 模块 | 提交 |
|------|------|------|
| 高德周边搜索工具 | `mcp_servers/amap/client.py`, `server.py` 新增 `search_nearby` / `get_poi_detail` | `e84d662` |
| LLM 意图理解 | `core/intent.py`，替代旧的硬编码 `parse_input` 思路 | `be66de4` |
| 行程生成工具 | `agents/itinerary_tool.py`，把 LangGraph 多Agent图包装成 `generate_itinerary` | `7f013b4` |
| ReAct 主Agent | `agents/react_agent.py`，负责日常对话、工具选择和多轮上下文 | `d51aa4b` |
| API 两段式接入 | `api/main.py`, `api/ws.py`, `api/services/chat_runner.py`，长连接多轮 WebSocket | `55f00de` |
| 前端聊天界面 | `frontend/src/views/ChatView.vue` 与消息/工具/行程组件 | `5b56f5e` |
| 验收适配 | API/集成测试适配新 Agent API | `380b232`, `226416f` |

详细计划见 `docs/plans/04.5-PHASE4.5-AGENT-EXPERIENCE.md`。

## 仓库结构

```text
tour-agent/
├── agents/
│   ├── react_agent.py          # ReAct 主Agent
│   ├── itinerary_tool.py       # generate_itinerary 工具
│   └── graph.py                # Phase 2 多Agent图
├── api/                        # FastAPI + WebSocket
├── core/
│   ├── intent.py               # LLM 意图解析
│   ├── memory/                 # Redis 短期记忆 + Mem0 长期记忆
│   └── verifier.py             # 行程确定性验证器
├── mcp_servers/                # 高德 / 天气 / 景点 MCP Server
├── frontend/                   # Vue3 聊天界面
├── evals/                      # 旅行 case 与评测脚本
├── tests/                      # 单元 / 集成 / 性能测试
├── docker-compose.yml          # 本地 PostgreSQL + Redis + Qdrant
├── requirements.txt
└── .env.example
```

## 本地启动文档

### 1. 第一次准备环境

```bash
cd /Users/zeromadlife/Desktop/tour-agent

conda create -n tourswarm python=3.11 -y
conda activate tourswarm

python -m pip install --upgrade pip
pip install -r requirements.txt

cd frontend
npm install
cd ..

cp .env.example .env
```

### 2. 配置 `.env`

完整联调至少需要主Agent和规划Agent的 LLM Key：

```bash
# 主Agent目前固定用 DeepSeek
DEEPSEEK_API_KEY=你的deepseek_key

# 复杂行程规划默认用 LLM_MODEL 指向的模型；默认是豆包
LLM_MODEL=doubao:Doubao-Seed-2.0-pro
DOUBAO_API_KEY=你的豆包_key
```

如果你暂时只想用 DeepSeek 跑通，可以把规划模型也切到 DeepSeek：

```bash
LLM_MODEL=deepseek:deepseek-chat
DEEPSEEK_API_KEY=你的deepseek_key
```

附近搜索需要高德 Key：

```bash
AMAP_API_KEY=你的高德_web服务_key
```

天气查询需要和风天气 Key；如果不配，行程规划会按天气失败降级继续跑：

```bash
QWEATHER_API_KEY=你的和风_key
QWEATHER_BASE_URL=https://你的host.re.qweatherapi.com/v7
QWEATHER_GEO_URL=https://你的host.re.qweatherapi.com/geoapi/v2
```

### 3. 每次启动中间件

```bash
cd /Users/zeromadlife/Desktop/tour-agent
docker compose up -d
docker compose ps
```

确认 `tourswarm-postgres`、`tourswarm-redis`、`tourswarm-qdrant` 都是 `Up` 或 `healthy`。

### 4. 启动后端

```bash
cd /Users/zeromadlife/Desktop/tour-agent
conda activate tourswarm
uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

自检：

```bash
curl http://127.0.0.1:8000/health
```

返回 `{"status":"ok"}` 只代表 FastAPI 活着。若 WebSocket 返回 `Agent is not configured`，优先检查 `DEEPSEEK_API_KEY` 和 `LLM_MODEL` 对应的 Key 是否被 `--env-file .env` 加载进进程。

如果 8000 被占用：

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

可以停掉旧进程，或把 TourSwarm 改到 8010：

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8010 --reload --env-file .env
```

### 5. 启动前端

```bash
cd /Users/zeromadlife/Desktop/tour-agent/frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

打开 Vite 输出地址，通常是：

```text
http://127.0.0.1:5173/
```

本地开发推荐只设置 `VITE_API_PROXY_TARGET`，让 Vite 代理 `/api` 和 WebSocket 到后端。不要优先设置 `VITE_API_BASE_URL=http://127.0.0.1:8000`，否则容易遇到浏览器 CORS 问题。

如果后端换成 8010，前端对应改成：

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev
```

### 6. 推荐联调输入

```text
你好
```

```text
帮我规划杭州2日游预算500元，喜欢美食和自然风光
```

```text
杭州明天天气怎么样
```

```text
附近有什么好吃的
```

注意：“附近有什么好吃的”要真正返回高德周边结果，需要主Agent拿到经纬度。当前前端还没有浏览器定位能力，所以这是下一步产品化要补的点；现在可通过更明确的位置/地址类输入辅助 Agent 调 `geocode`。

## PyCharm 启动

### 后端 Run Configuration

| 配置项 | 值 |
|--------|-----|
| Type | Python |
| Module name | `uvicorn` |
| Parameters | `api.main:app --host 127.0.0.1 --port 8000 --reload --env-file .env` |
| Working directory | `/Users/zeromadlife/Desktop/tour-agent` |
| Interpreter | conda env `tourswarm` |

PyCharm 启动前仍然要先执行：

```bash
docker compose up -d
```

### 前端 Run Configuration

推荐直接用 PyCharm Terminal：

```bash
cd /Users/zeromadlife/Desktop/tour-agent/frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

也可以建 npm 配置：

| 配置项 | 值 |
|--------|-----|
| package.json | `/Users/zeromadlife/Desktop/tour-agent/frontend/package.json` |
| Command | `run` |
| Scripts | `dev` |
| Environment variables | `VITE_API_PROXY_TARGET=http://127.0.0.1:8000` |

## 质量检查

```bash
cd /Users/zeromadlife/Desktop/tour-agent
bash scripts/check.sh

cd frontend
npm run test -- --run
npm run build
```

测试使用 Mock，不消耗真实 API 额度；本地联调才需要真实 Key。

## 常见问题

| 现象 | 优先检查 |
|------|----------|
| `Address already in use` | `lsof -nP -iTCP:8000 -sTCP:LISTEN` 查占用，换端口或杀旧进程 |
| `/health` 正常但聊天失败 | 后端 Agent 构建失败，检查 `DEEPSEEK_API_KEY`、`LLM_MODEL`、`DOUBAO_API_KEY` |
| WebSocket 返回 `Agent is not configured` | `.env` 没加载或 LLM Key 缺失 |
| 前端页面能开但发消息失败 | 后端端口和 `VITE_API_PROXY_TARGET` 不一致 |
| 天气失败但仍生成行程 | 正常降级；补和风 Key 和 Host 可恢复真实天气 |
| 附近搜索结果不稳定 | 高德 Key、经纬度、当前位置输入是否明确 |

## 当前边界

- 前端是聊天 demo，不是最终 UI。
- 会话状态目前以内存为主，刷新/重启会丢。
- 没有登录、地图、分享、UniApp、线上部署。
- `generate_itinerary` 已经能包装多Agent图，但工具级流式进度还比较粗。
- 下一阶段重点不是堆 UI，而是补定位输入、评测指标、错误降级和可观测性。

## License

MIT
