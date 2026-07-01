# TourSwarm 总开发路线图

> **本文档是总纲。** 每个 Phase 对应一份独立的 TDD 实施计划（`docs/plans/0N-PHASE*.md`），
> 实施时按 Phase 顺序推进，完成一个 Phase 的里程碑验收后再进入下一个。

**Goal:** 10周内构建并上线一个基于多Agent协作的智能旅游助手，覆盖"规划/推荐/预算/信息"四大能力。

**Architecture:** LangGraph StateGraph 编排 4-Agent Supervisor 架构；自研 3 个 MCP Server 接入高德地图/和风天气/景点数据；Redis(短期) + Mem0/Qdrant(长期) 分层记忆；FastAPI 后端 + Vue3 Web前端 + UniApp Android端；Docker Compose 部署到阿里云学生 ECS。

**Tech Stack:** Python 3.11+ / LangGraph / MCP / Mem0 / FastAPI / Vue3 / UniApp / PostgreSQL+pgvector / Redis / Qdrant / Docker

---

## 工程原则

1. **TDD（测试驱动）** — 每个 MCP Server 和 Agent 核心逻辑先写失败测试，再写实现。
2. **前置风险** — 核心架构在早期完成，为后期预留缓冲。
3. **质量优先于数量** — 3个完整功能优于10个平庸功能。多Agent协作复杂度随Agent数超线性增长。
4. **Mock 优先** — 开发阶段所有外部 API 用 Mock/Respx，避免耗尽免费额度。
5. **频繁提交** — 每个步骤独立 commit，保持可回滚。

## 关键里程碑

| 里程碑 | 时间 | 验收标准 | 阻塞风险 |
|:---:|:---:|:---|:---|
| **M1** MCP工具链打通 | Week 2 | 3个MCP Server可独立调用，单测通过率≥90%，延迟<2s | 工具注册失败、Schema不匹配 |
| **M2** 多Agent协作跑通 | Week 4 | Supervisor路由准确率≥85%，端到端成功率≥80% | 死循环、路由错误、状态丢失 |
| **M3** 记忆系统工作 | Week 6 | 跨会话偏好记忆准确率≥80%，检索延迟<500ms | 记忆丢失、检索延迟过高 |
| **M4** 完整功能可用 | Week 8 | 功能覆盖率100%，P95延迟<5s，可全流程演示 | 前端兼容性、性能不达标 |
| **M5** 已上线运营 | Week 10 | 公网可访问，监控运行，有实际访问记录 | 部署失败、安全漏洞 |

---

## Phase 1：环境搭建与 MCP Server 开发（Week 1-2）

**目标：** 建立标准化开发环境，3个MCP Server可独立调用。
**详细计划：** [`01-PHASE1-MCP-SERVERS.md`](./01-PHASE1-MCP-SERVERS.md)

| 任务 | 交付物 |
|------|--------|
| 项目脚手架 + 目录结构 + Docker Compose | 可 `docker compose up -d` 启动基础设施 |
| 配置管理（pydantic-settings 读 .env） | `core/config/settings.py` |
| 高德地图 MCP Server | `mcp_servers/amap/server.py` + 单测 |
| 天气 MCP Server | `mcp_servers/weather/server.py` + 单测 |
| 景点 MCP Server | `mcp_servers/scenic/server.py` + 单测 |
| Mock 数据集 | `data/mock/*.json` |
| MCP Inspector 验证 | 3个Server在 Inspector 中可 tools/list + tools/call |

**验收（M1）：** `pytest tests/mcp_servers -v` 全绿；Inspector 中3个Server可独立调用。

---

## Phase 2：Agent 编排核心开发（Week 3-4）

**目标：** LangGraph Supervisor + 4个专业Agent跑通端到端协作。
**详细计划：** `02-PHASE2-AGENT-ORCHESTRATION.md`（M1达成后编写）

| 任务 | 交付物 |
|------|--------|
| TravelState 定义（TypedDict + reducer） | `core/state.py` |
| Supervisor 节点（意图识别 + 任务分解 + 路由） | `agents/supervisor.py` |
| 规划Agent（CSP行程生成 + TSP路线优化） | `agents/planning.py` |
| 推荐Agent（向量检索 + 混合排序） | `agents/recommend.py` |
| 预算Agent（预算分配 + 性价比计算 + 追踪） | `agents/budget.py` |
| 信息Agent（并行查询天气/交通/景点） | `agents/info.py` |
| MCP Client 集成（MultiServerMCPClient） | `core/mcp_client.py` |
| 端到端集成测试 | `tests/agents/test_e2e.py` |

**验收（M2）：** 输入"周末去杭州2日游预算500元喜欢美食"→输出含时间轴/路线/预算的结构化行程。Agent间有真实任务委托与状态共享，非固定路由。

**关键决策点：**
- ⚠️ A2A协议在MVP阶段**降级为可选**。Supervisor→Worker的协作先用LangGraph原生条件边实现，A2A作为Phase 6扩展。理由：MCP单独已形成充分区分度，A2A增加复杂度但不增加MVP价值。

---

## Phase 3：记忆管理集成（Week 5-6）

**目标：** Redis短期记忆 + Mem0长期记忆 + 上下文压缩三重策略。
**详细计划：** `03-PHASE3-MEMORY.md`（M2达成后编写）

| 任务 | 交付物 |
|------|--------|
| Redis 短期记忆（会话状态 + 滑动窗口） | `core/memory/short_term.py` |
| LangGraph Checkpointer（Redis后端） | `core/memory/checkpoint.py` |
| Mem0 长期记忆集成（Qdrant后端） | `core/memory/long_term.py` |
| 用户偏好自动提取 | `core/memory/extractor.py` |
| 上下文压缩（锚定摘要 + 结构化蒸馏） | `core/memory/compressor.py` |
| 跨会话记忆测试 | `tests/core/test_memory.py` |

**验收（M3）：** 会话A提到"喜欢海鲜"→新会话B推荐餐厅时体现该偏好。记忆检索延迟<500ms。

---

## Phase 4：前端与集成测试（Week 7-8）

**目标：** Vue3 Web前端 + UniApp Android端 + FastAPI API + 全流程测试。
**详细计划：** `04-PHASE4-FRONTEND-TESTING.md`（M3达成后编写）

| 任务 | 交付物 |
|------|--------|
| FastAPI REST API（聊天/会话/行程） | `api/routes.py` |
| WebSocket 流式输出 | `api/ws.py` |
| 确定性验证器（时间/预算/空间/约束4重检查） | `core/verifier.py` |
| Vue3 Web 前端（聊天 + 行程可视化 + 预算仪表盘） | `frontend/` |
| UniApp Android 端（聊天 + 行程查看 + 预算） | `mobile/` |
| 三层测试（单元/集成/性能） | `tests/integration/` `tests/perf/` |
| 5分钟演示脚本 | `docs/demo-script.md` |

**验收（M4）：** 全流程可演示，P95延迟<5s。Web端和Android端均可运行核心流程。

**亮点强化：** 确定性验证器是秋招强区分度话题（纯LLM 78% → +验证器 94%，贡献16pp）。务必做消融实验记录数据。

---

## Phase 5：部署上线与监控（Week 9-10）

**目标：** 零成本部署到阿里云学生ECS，接入监控。
**详细计划：** `05-PHASE5-DEPLOYMENT.md`（M4达成后编写）

| 任务 | 交付物 |
|------|--------|
| 生产 Docker Compose + Nginx + SSL | `docker-compose.prod.yml` |
| 阿里云ECS部署 | 公网可访问实例 |
| LangSmith 链路追踪 | Trace Dashboard |
| Prometheus + Grafana 指标监控 | 监控Dashboard |
| 4级错误处理（工具/Agent/服务/系统） | `core/resilience.py` |
| README完善（架构图+截图+部署指南） | 更新 `README.md` |
| 1篇技术博客（"从零构建MCP Server"） | 外部发布 |

**验收（M5）：** 公网可访问，有实际访问记录，文档完整。

---

## Phase 6（可选扩展）：A2A协议 + 开源贡献

> MVP上线后再考虑，不影响秋招核心展示。

- A2A协议实现Agent间标准化通信（Agent Card + Task Lifecycle）
- 为 Hello Agents / Dify / Awesome LLM Apps 提交 merged PR
- V1.0：酒店比价、路线优化、天气预警、行程分享
- V2.0：离线缓存、语音交互、多人协作

---

## 风险应对速查

| 风险 | 应对 |
|------|------|
| API额度耗尽 | Mock数据 + Redis缓存 + 多源备份（和风/彩云/心知） |
| 10周做不完 | MVP优先：3个MCP Server + 4个Agent + Vue3 Web端优先；UniApp端/A2A/高级功能裁剪 |
| LLM费用失控 | 语义缓存 + 模型路由（简单意图用mini） + Ollama本地降级 |
| Agent死循环 | 迭代上限(25) + 超时控制 + 状态哈希重复检测 + 熔断器 |
| 多Agent变Workflow | 确保Agent间有任务委托/状态共享/错误传递，非固定路由 |

## 当前进度

- [x] 项目脚手架（README / .gitignore / .env.example / requirements / docker-compose）
- [x] Phase 1 — MCP Server 开发（M1：3个 MCP Server + 40 tests + 83% coverage）
  - 备注：Docker Compose 基础设施镜像拉取在本机网络下未完成，需重试 `docker compose up -d` 复验。
- [x] Phase 2 — Agent 编排（M2：4 Agent + LangGraph 两阶段图 + 90 tests + 93% coverage）
  - 备注：真实 CLI 演示已输出结构化行程；和风天气真实调用返回非 JSON，已按天气降级路径继续生成行程。
- [ ] Phase 3 — 记忆系统
- [ ] Phase 4 — 前端与测试
- [ ] Phase 5 — 部署上线
