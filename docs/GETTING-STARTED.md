# Sage 开发环境启动指南

> 本指南帮你从零搭建本地开发环境，包括 API Key 申请、Docker 基础设施启动、MCP Server 验证。
> 按顺序执行即可，预计 30-60 分钟（不含 API Key 审核等待时间）。

---

## 一、前置环境检查

### 1.1 必需软件

```bash
# Python 3.12+
python3 --version
# 预期输出: Python 3.12.x 或更高

# Docker + Docker Compose
docker --version
docker compose version
# 预期: Docker 24+ / Compose v2+

# Node.js 18+（MCP Inspector 用，可选）
node --version
# 预期: v18+ （不装也行，Inspector 只用于手动验证）
```

### 1.2 安装 Python 依赖

```bash
cd /Users/zeromadlife/Desktop/tour-agent
pip install -r requirements.txt
```

### 1.3 验证代码质量工具链

```bash
# 三件套都应通过
ruff check core/ mcp_servers/ agents/ tests/             # lint
ruff format --check core/ mcp_servers/ agents/ tests/    # format check
mypy core/ mcp_servers/ agents/                          # 类型检查
pytest tests/ -v                             # 单元测试
```

如果全部通过，说明环境就绪。

---

## 二、API Key 申请

单元测试不需要真实 Key（全部用 Mock），但手动验证 MCP Server 和运行 CLI demo 时需要。
建议现在就申请，后续 Phase 3-5 也会继续用到。

### 2.1 高德地图 API Key（必需）

| 项 | 说明 |
|----|------|
| **用途** | POI搜索、路线规划、地理编码 |
| **免费额度** | 30万次/日（个人认证） |
| **申请地址** | https://lbs.amap.com/ |
| **审核时间** | 即时（个人认证） |

**申请步骤：**

1. 访问 https://lbs.amap.com/ → 注册/登录
2. 控制台 → 应用管理 → 创建新应用
3. 应用类型选"Web端(JS API)"或"Web服务"
4. 创建 Key，记下 **Key 值**（一串32位字母数字）
5. **重要：** 我们用的是 **Web服务 API**（REST 接口），不是 JS API

**验证 Key 是否可用：**

```bash
# 替换 YOUR_AMAP_KEY 为你的真实 Key
curl "https://restapi.amap.com/v3/place/text?key=YOUR_AMAP_KEY&keywords=西湖&city=杭州&types=110000&offset=5&page=1&extensions=all" | python -m json.tool
```

如果返回 `"status": "1"` 且 `pois` 数组有数据，说明 Key 可用。

### 2.2 和风天气 API Key（必需）

| 项 | 说明 |
|----|------|
| **用途** | 实时天气、天气预报、灾害预警 |
| **免费额度** | 5万次/月（非商业用户） |
| **申请地址** | https://dev.qweather.com/ |
| **审核时间** | 即时 |

**申请步骤：**

1. 访问 https://dev.qweather.com/ → 注册/登录
2. 控制台 → 应用管理 → 创建应用
3. 选择"免费开发版"（免费额度足够）
4. 创建 Key，记下 **Key 值**
5. 控制台 → 设置中获取你的 **API Host**，填入 `.env` 的 `QWEATHER_BASE_URL` 和 `QWEATHER_GEO_URL`
6. **注意：** 和风天气还需要一个 **Location ID**，通过城市查询接口获取

**验证 Key 是否可用：**

```bash
export QWEATHER_GEO_URL="https://你的host.re.qweatherapi.com/geoapi/v2"
export QWEATHER_BASE_URL="https://你的host.re.qweatherapi.com/v7"

# 第一步：城市查询获取 location_id
curl "$QWEATHER_GEO_URL/city/lookup?key=YOUR_QWEATHER_KEY&location=杭州" | python -m json.tool
# 记下返回的 location.id（如 "101210101"）

# 第二步：查实时天气
curl "$QWEATHER_BASE_URL/weather/now?key=YOUR_QWEATHER_KEY&location=101210101" | python -m json.tool
```

如果返回 `"code": "200"` 且 `now` 对象有数据，说明 Key 可用。

### 2.3 LLM API Key（Phase 2 演示需要）

| 供应商 | 免费额度 | 申请地址 |
|--------|----------|----------|
| DeepSeek | 按量计费，价格低 | https://platform.deepseek.com/ |
| 火山引擎豆包 | 按量计费 | https://console.volcengine.com/ark/ |
| OpenAI 兼容中转 | 取决于服务商 | 见你的中转站控制台 |
| OpenAI 官方 | 按量计费 | https://platform.openai.com/ |

> **建议：** 默认用 `LLM_MODEL=doubao:Doubao-Seed-2.0-pro`，轻量模型用 `LLM_LIGHT_MODEL=deepseek:deepseek-chat`。

### 2.4 Key 汇总表

申请完后，你应该有这些值：

| 环境变量 | 值 | 来源 | 是否必需 |
|----------|-----|------|:---:|
| `AMAP_API_KEY` | 32位字母数字 | 高德地图 | 手动验证时需要 |
| `QWEATHER_API_KEY` | 32位字母数字 | 和风天气 | 手动验证时需要 |
| `QWEATHER_BASE_URL` | `https://.../v7` | 和风天气控制台 | 手动验证时需要 |
| `QWEATHER_GEO_URL` | `https://.../geoapi/v2` | 和风天气控制台 | 手动验证时需要 |
| `DOUBAO_API_KEY` | 火山引擎 Ark Key | 火山引擎 | Phase 2 CLI 需要 |
| `DEEPSEEK_API_KEY` | DeepSeek Key | DeepSeek | Phase 2 CLI 需要 |

---

## 三、配置 .env 文件

```bash
# 从模板复制
cp .env.example .env

# 编辑 .env，填入你的真实 Key
# 如果你还没有 Key，先不填，单元测试用 Mock 不需要
```

`.env` 文件关键配置：

```bash
# 高德地图（手动验证 MCP 时需要）
AMAP_API_KEY=你的高德Key

# 和风天气（手动验证 MCP 时需要）
QWEATHER_API_KEY=你的和风Key
QWEATHER_BASE_URL=https://你的host.re.qweatherapi.com/v7
QWEATHER_GEO_URL=https://你的host.re.qweatherapi.com/geoapi/v2

# LLM（运行 Phase 2 CLI demo 时需要）
DOUBAO_API_KEY=你的豆包Key
DEEPSEEK_API_KEY=你的DeepSeekKey
LLM_MODEL=doubao:Doubao-Seed-2.0-pro
LLM_LIGHT_MODEL=deepseek:deepseek-chat

# 数据库（Docker 默认值，不用改）
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=tourswarm
POSTGRES_PASSWORD=tourswarm_dev
POSTGRES_DB=tourswarm

# Redis（Docker 默认值，不用改）
REDIS_HOST=localhost
REDIS_PORT=6379

```

> ⚠️ `.env` 已在 `.gitignore` 中，不会被提交到 Git。**永远不要把真实 Key 提交到代码仓库。**

---

## 四、启动 Docker 基础设施

```bash
# 启动 PostgreSQL/pgvector + Redis
docker compose up -d

# 查看服务状态（等待变为 healthy）
docker compose ps
```

预期输出：

```
NAME                  IMAGE                       STATUS                   PORTS
tourswarm-postgres    pgvector/pgvector:pg16      Up (healthy)             0.0.0.0:5432->5432/tcp
tourswarm-redis       redis:7.2-alpine            Up (healthy)             0.0.0.0:6379->6379/tcp
```

**验证各服务可访问：**

```bash
# PostgreSQL
docker exec tourswarm-postgres psql -U tourswarm -d tourswarm -c "SELECT version();"

# Redis
docker exec tourswarm-redis redis-cli ping
# 预期: PONG

```

**停止服务：**

```bash
docker compose down          # 停止容器，保留数据
docker compose down -v       # 停止并删除数据（慎用！会清空数据库）
```

---

## 五、运行单元测试

```bash
# 全部测试
pytest tests/ -v

# 带覆盖率
pytest tests/ --cov=mcp_servers --cov=core --cov-report=term-missing

# 只跑某个模块
pytest tests/mcp_servers/test_amap_client.py -v
```

预期：全部测试通过，覆盖率达到当前阶段门槛。

> 单元测试使用 respx 拦截所有 HTTP 请求，**不需要真实 API Key**，也不需要 Docker 服务运行。

---

## 六、手动验证 MCP Server

> 需要真实 API Key。如果还没有，跳过本节，Phase 2 再验证。

### 6.1 启动单个 MCP Server（stdio 模式）

```bash
# 景点 MCP Server（不需要 API Key，用本地数据）
SCENIC_DATA_PATH=data/mock/scenic_spots.json python -m mcp_servers.scenic.server

# 高德 MCP Server（需要 AMAP_API_KEY）
AMAP_API_KEY=你的高德Key python -m mcp_servers.amap.server

# 天气 MCP Server（需要 QWEATHER_API_KEY）
QWEATHER_API_KEY=你的和风Key python -m mcp_servers.weather.server
```

Server 启动后会等待 stdio 输入（正常行为，不是卡死）。

### 6.2 使用 MCP Inspector 交互式验证

```bash
# 安装并启动 Inspector（需要 Node.js）
npx @modelcontextprotocol/inspector

# Inspector 会打开浏览器界面，在界面中配置：
# Command: python
# Args: -m mcp_servers.scenic.server
# Env: SCENIC_DATA_PATH=data/mock/scenic_spots.json
```

在 Inspector 界面中：
1. 点击 **Connect** 连接 Server
2. 点击 **List Tools** — 应看到 4 个工具（search_scenic_spots / get_scenic_detail / get_opening_hours / get_ticket_price）
3. 点击 **Call Tool** — 填入参数测试：
   - `search_scenic_spots`：`{"city": "杭州"}` → 应返回西湖、灵隐寺、河坊街
   - `get_scenic_detail`：`{"spot_id": "hangzhou-xihu"}` → 应返回西湖完整信息

### 6.3 验证高德 Server（需真实 Key）

在 Inspector 中配置：
- Command: `python`
- Args: `-m mcp_servers.amap.server`
- Env: `AMAP_API_KEY=你的真实Key`

测试：
- `search_attractions`：`{"city": "杭州", "keywords": "西湖"}` → 应返回高德真实 POI 数据
- `geocode`：`{"address": "西湖", "city": "杭州"}` → 应返回经纬度

### 6.4 验证天气 Server（需真实 Key）

在 Inspector 中配置：
- Command: `python`
- Args: `-m mcp_servers.weather.server`
- Env:
  - `QWEATHER_API_KEY=你的真实Key`
  - `QWEATHER_BASE_URL=https://你的host.re.qweatherapi.com/v7`
  - `QWEATHER_GEO_URL=https://你的host.re.qweatherapi.com/geoapi/v2`

测试：
- `get_weather`：`{"city": "杭州"}` → 应返回杭州实时天气
- `get_forecast`：`{"city": "杭州", "days": 7}` → 应返回7日预报

---

## 七、一键检查脚本

```bash
# 提交代码前运行，确保 lint + 类型检查 + 测试全绿
bash scripts/check.sh
```

这个脚本会依次运行：
1. `ruff check` — lint 检查
2. `ruff format --check` — 格式检查
3. `mypy` — 类型检查
4. `pytest` — 单元测试

全绿才能提交。

---

## 八、常见问题

### Q: `pip install` 报错怎么办？
```bash
# 确保 Python 版本 >= 3.12
python3 --version

# 如果有多个 Python 版本，用虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Q: `docker compose up` 报端口占用？
```bash
# 检查谁占用了端口
lsof -i :5432  # PostgreSQL
lsof -i :6379  # Redis

# 方案1：停掉占用端口的服务
# 方案2：修改 docker-compose.yml 中的端口映射
```

### Q: mypy 第一次运行很慢？
mypy 第一次会分析所有依赖包的类型存根，缓存后后续运行会很快。正常现象，等它跑完即可。

### Q: MCP Server 启动后没反应？
stdio 模式下 Server 会等待输入，这是正常的。用 MCP Inspector 或 MCP Client 连接它才会响应。直接在终端看不到输出是正常的。

### Q: 测试用 Mock 还是用真实 API？
**开发阶段全部用 Mock**（respx 拦截），不消耗真实额度。
**手动验证阶段**才用真实 API Key 通过 MCP Inspector 测试。
**Phase 2 集成测试**时会加一个 `@pytest.mark.integration` 标记的测试集，用真实 API 跑。

---

## 九、当前阶段完成情况

- [x] Sage Coding、Knowledge、durable timeline、审批与 Vue 主界面
- [x] Python 3.12 + LangChain/LangGraph 1.x 依赖基线
- [ ] DeerFlow 方向的 `sage_harness` 分波迁移
- [ ] 服务器 Container Sandbox 与发布门禁

**下一步：** 按 `docs/superpowers/plans/2026-07-16-sage-deerflow-harness-migration.md` 执行 Harness 迁移。
