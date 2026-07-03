# TourSwarm — 智能旅游多Agent协作系统

> 基于 LangGraph StateGraph + MCP 协议 + Mem0 分层记忆的智能旅游助手。
> 聚焦学生穷游/周末周边游，以"预算作为输入"为差异化核心。

## 项目定位

| 维度 | 说明 |
|------|------|
| 产品方向 | 智能旅游助手（学生穷游 / 周末周边游） |
| 差异化 | 预算前置约束 + 自研MCP Server + 分层记忆 + 确定性验证器 |
| 技术栈 | LangGraph + MCP + Mem0 + Redis + FastAPI + Vue3 + UniApp |
| 语言 | Python 3.11+（后端AI层） / TypeScript（前端双端） |
| 架构 | 4-Agent Supervisor：规划 / 推荐 / 预算 / 信息 |
| 部署 | Docker Compose + 阿里云学生免费 ECS |

## 核心架构

```
用户请求 → FastAPI → LangGraph StateGraph → Supervisor
                                                    ↓ 扇出
            ┌──────────┬──────────┬──────────┐
            ↓          ↓          ↓          ↓
        规划Agent   推荐Agent  预算Agent  信息Agent
            └──────────┴────┬─────┴──────────┘
                            ↓
                  MultiServerMCPClient
                  ↙        ↓        ↘
            高德MCP     天气MCP     景点MCP
                            ↓
            PostgreSQL + Redis + Mem0(Qdrant)
```

## 仓库结构

```
tour-agent/
├── docs/                  # 文档（研究、架构、计划）
│   ├── plans/             # 开发路线图与 TDD 计划
│   ├── architecture/      # 架构设计文档
│   └── research/          # 调研笔记
├── mcp_servers/           # 自研 MCP Server（高德/天气/景点）
├── agents/                # 4 个专业 Agent 实现
├── core/                  # 编排、记忆、配置核心
│   ├── memory/            # 短期/长期记忆管理
│   └── config/            # 全局配置
├── frontend/              # Vue3 Web 前端
├── mobile/                # UniApp Android 端
├── tests/                 # 单元/集成/性能测试
├── scripts/               # 数据初始化、部署脚本
├── data/                  # 种子数据、Mock 数据
├── docker-compose.yml     # 开发环境编排
├── requirements.txt       # Python 依赖
└── .env.example           # 环境变量模板
```

## 开发路线（10周 / 5里程碑）

| 阶段 | 周次 | 里程碑 | 核心交付 |
|------|------|--------|----------|
| Phase 1 | Week 1-2 | M1 MCP工具链打通 | 3个MCP Server + Docker开发环境 |
| Phase 2 | Week 3-4 | M2 多Agent协作跑通 | Supervisor + 4 Agent |
| Phase 3 | Week 5-6 | M3 记忆系统工作 | Mem0 + Redis 分层记忆 |
| Phase 4 | Week 7-8 | M4 完整功能可用 | Vue3前端 + UniApp端 + 测试 |
| Phase 5 | Week 9-10 | M5 已上线运营 | 阿里云部署 + 监控 |

详见 `docs/plans/00-MASTER-ROADMAP.md`。

## 快速开始

```bash
# 1. 复制环境变量并填入 API Key
cp .env.example .env

# 2. 启动开发环境（PostgreSQL + Redis + Qdrant）
docker compose up -d

# 3. 安装依赖
pip install -r requirements.txt

# 4. 代码质量检查（lint + 类型检查 + 测试）
bash scripts/check.sh

# 5. 运行测试
pytest tests/ -v
```

## Phase 4 Web 演示

```bash
# 启动 FastAPI 后端
uvicorn api.main:app --reload --port 8000

# 启动 Vue3 工作台
cd frontend
npm install
npm run dev
```

打开 Vite 输出的本地地址后，输入 `周末去杭州2日游预算500元喜欢美食`，前端会通过 `/api/v1/chat` 创建会话，并连接 `/api/v1/chat/{session_id}/stream` 接收 Agent 进度与行程结果。5 分钟演示流程见 `docs/demo-script.md`。

## 类型安全工具链

本项目用工程工具解决Python动态类型的短板，而非换语言：

| 工具 | 作用 | 配置 |
|------|------|------|
| **Pydantic v2** | 数据模型运行时+静态校验 | 所有模型继承 `BaseModel` |
| **Type hints** | 函数签名强制类型标注 | `disallow_untyped_defs` |
| **mypy** | 静态类型检查，编译期发现类型错误 | `pyproject.toml` strict模式 |
| **ruff** | lint + format（替代flake8+black+isort） | `ruff.toml` |

提交前运行 `bash scripts/check.sh` 确保 lint + 类型检查 + 测试全绿。

## 必需的 API Key

| 服务 | 用途 | 免费额度 | 申请地址 |
|------|------|----------|----------|
| 高德地图 | POI搜索/路线规划 | 30万次/日 | https://lbs.amap.com/ |
| 和风天气 | 天气预报/预警 | 5万次/月 | https://dev.qweather.com/ |
| LLM | Agent推理 | 按供应商 | 见 .env.example |

## 研究来源

本项目基于 KimiSwarm 输出的多Agent协作平台研究报告（7章 + 执行摘要），
原文存放于 `Kimi_Agent_多智能体协作平台/` 目录。核心结论见 `docs/research/report-summary.md`。

## License

MIT
