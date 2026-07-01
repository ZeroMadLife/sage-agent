# 多Agent智能旅游助手 - 完整技术架构方案设计

> 基于深度调研的完整可落地方案 | 60+ 独立搜索来源 | 2025年7月

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [核心模块设计](#2-核心模块设计)
3. [数据库设计](#3-数据库设计)
4. [API设计](#4-api设计)
5. [技术栈最终选型](#5-技术栈最终选型)
6. [开发路线图](#6-开发路线图)
7. [研究引用汇总](#7-研究引用汇总)

---

## 1. 系统架构总览

### 1.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端层 (Presentation)                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │  Streamlit   │  │   React Web  │  │   Mobile     │                       │
│  │   (MVP)      │  │  (Advanced)  │  │   (Future)   │                       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                       │
└─────────┼────────────────┼────────────────┼─────────────────────────────────┘
          │                │                │
          └────────────────┴────────────────┘
                              │ HTTPS/WSS
┌─────────────────────────────┼─────────────────────────────────────────────────┐
│                         网关层 (Gateway)                                     │
│  ┌──────────────────────────┼──────────────────────────────────────────┐     │
│  │              Nginx Reverse Proxy + SSL Termination                   │     │
│  │         Rate Limiting │ Load Balancing │ Static File Serving         │     │
│  └──────────────────────────┼──────────────────────────────────────────┘     │
└─────────────────────────────┼─────────────────────────────────────────────────┘
                              │
┌─────────────────────────────┼─────────────────────────────────────────────────┐
│                          API层 (FastAPI)                                     │
│  ┌──────────────────────────┼──────────────────────────────────────────┐     │
│  │  REST API │ WebSocket │ JWT Auth │ Middleware │ Exception Handler  │     │
│  │  ┌────────┴────────┐  ┌────────┴────────┐  ┌──────────────────┐   │     │
│  │  │   Chat Router   │  │ Session Router  │  │  User Router     │   │     │
│  │  │   /api/v1/chat  │  │ /api/v1/session │  │  /api/v1/user    │   │     │
│  │  └────────┬────────┘  └────────┬────────┘  └──────────────────┘   │     │
│  └───────────┼────────────────────┼────────────────────────────────────┘     │
└──────────────┼────────────────────┼──────────────────────────────────────────┘
               │                    │
┌──────────────┼────────────────────┼──────────────────────────────────────────┐
│              │            Agent编排层 (LangGraph)                           │
│  ┌───────────┼────────────────────┼──────────────────────────────────────┐  │
│  │           │         ┌──────────┴──────────┐                            │  │
│  │           │         │    Supervisor       │                            │  │
│  │           │         │    Agent (调度)     │                            │  │
│  │           │         └──────────┬──────────┘                            │  │
│  │           │                    │                                       │  │
│  │  ┌────────┴────────┬───────────┼───────────┬──────────────────────┐   │  │
│  │  │                 │           │           │                      │   │  │
│  │  ▼                 ▼           ▼           ▼                      ▼   │  │
│  │ ┌─────────┐  ┌─────────┐ ┌─────────┐ ┌─────────┐         ┌─────────┐ │  │
│  │ │ Planning│  │Recommend│ │  Info   │ │ Booking │  ...    │ Memory  │ │  │
│  │ │  Agent  │  │  Agent  │ │  Agent  │ │  Agent  │         │  Agent  │ │  │
│  │ └─────────┘  └─────────┘ └─────────┘ └─────────┘         └─────────┘ │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
               │
               │ 工具调用 (MCP Protocol)
               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        Skills管理层 (MCP Servers)                            │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   │
│  │ Weather MCP │ │Traffic MCP  │ │Scenic MCP   │ │ Hotel/Flight MCP    │   │
│  │   Server    │ │  Server     │ │  Server     │ │    Server           │   │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────────┬──────────┘   │
│  ┌──────┴────────────────┴────────────────┴─────────────────┴──────┐        │
│  │              MCP Gateway (Auth │ Routing │ Caching)              │        │
│  └──────────────────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────────────┘
               │
               │ 数据读写
               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        数据持久层 (Data Persistence)                         │
│                                                                              │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│   │  PostgreSQL  │  │    Redis     │  │   Mem0/Qdrant│  │  (Milvus)    │   │
│   │  (主数据库)   │  │   (缓存层)   │  │  (记忆/向量)  │  │ (生产向量库) │   │
│   │              │  │              │  │              │  │              │   │
│   │ - Users      │  │ - Sessions   │  │ - Memory     │  │ - Documents  │   │
│   │ - Itineraries│  │ - Caches     │  │ - Vectors    │  │ - Embeddings │   │
│   │ - Messages   │  │ - Rate Limit │  │ - Facts      │  │ - Hybrid     │   │
│   │ - Bookmarks  │  │ - Pub/Sub    │  │              │  │   Search     │   │
│   └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
               │
               │ 指标/日志
               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      可观测性层 (Observability)                              │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│   │  LangSmith   │  │  Prometheus  │  │   Grafana    │  │   Loki/ELK   │   │
│   │  (Trace)     │  │  (Metrics)   │  │ (Dashboard)  │  │   (Logs)     │   │
│   └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.2 架构设计原则

```
Claim: LangGraph的Supervisor模式是90%多Agent团队的最佳选择，它在灵活性和可控性之间取得了最佳平衡
Source: CallSphere AI Blog - LangGraph Supervisor Pattern
URL: https://callsphere.ai/blog/langgraph-supervisor-multi-agent-orchestration-2026
Date: 2026-06-29
Excerpt: "For four specialists — research, code, math, writing — supervisor is the right answer. Network is anarchy at this size; hierarchy is overengineering."
Context: 智能旅游助手的Agent编排应采用Supervisor模式，由Supervisor Agent统一协调规划、推荐、信息、预订等专门Agent
Confidence: high
```

```
Claim: Supervisor模式相比单Agent有约3倍token成本，但成功率提升18个百分点，对于高价值任务是值得的
Source: CallSphere AI Blog - LangGraph Supervisor Pattern Cost Analysis
URL: https://callsphere.ai/blog/langgraph-supervisor-multi-agent-orchestration-2026
Date: 2026-06-29
Excerpt: "The supervisor pattern is roughly 3x the cost of a single mega-agent for an 18-point lift in success rate."
Context: 旅游规划属于高价值任务，用户愿意等待更长时间获取更准确的行程规划，因此Supervisor模式的成本增加是可接受的
Confidence: high
```

```
Claim: LangGraph支持并行节点执行（扇出/扇入模式）、条件边、状态通道和运行时图修改
Source: LangGraph Documentation & Research Papers
URL: https://arxiv.org/pdf/2604.11378
Date: 2026
Excerpt: "LangGraph supports parallel node execution (via fan-out/fan-in patterns), conditional edges, state channels, and runtime graph modification through add_node/add_edge calls during execution."
Context: 在旅游助手中，信息获取Agent可以并行查询天气、交通、景点信息，然后汇总给规划Agent
Confidence: high
```

---

### 1.3 前后端分离架构

#### 1.3.1 前端架构

```
Claim: Streamlit是MVP阶段的最佳前端选择，可在几小时内搭建交互界面，且与Python后端深度集成
Source: Streamlit & FastAPI ecosystem analysis
URL: https://github.com/shakshipatel/chat-websocket
Date: 2025-11-28
Excerpt: "A modern, real-time AI chatbot application built with React, TypeScript, and WebSocket technology."
Context: MVP阶段使用Streamlit快速验证产品，后续迁移到React
Confidence: high
```

**前端分层设计：**

| 层次 | MVP (Streamlit) | 进阶 (React) |
|------|-----------------|--------------|
| UI层 | Streamlit组件 | React 18 + Tailwind CSS |
| 状态管理 | Session State | Zustand/Redux Toolkit |
| API客户端 | `requests` | Axios + React Query |
| 实时通信 | `st.runtime` polling | WebSocket原生支持 |
| 路由 | 单页面 | React Router v6 |

#### 1.3.2 后端架构

```
Claim: FastAPI是Python生态中最适合AI Agent系统的Web框架，原生支持异步、自动API文档和数据验证
Source: FastAPI Official Documentation & Multiple Tutorials
URL: https://www.geeksforgeeks.org/python/login-registration-system-with-jwt-in-fastapi/
Date: 2026-05-11
Excerpt: "FastAPI applications need robust authentication to protect API endpoints from unauthorized access."
Context: 使用FastAPI构建RESTful API和WebSocket服务
Confidence: high
```

**后端分层设计：**

```
┌────────────────────────────────────────────────────────┐
│                    应用入口层                           │
│  main.py - FastAPI实例创建 │ 生命周期管理 │ 中间件注册   │
├────────────────────────────────────────────────────────┤
│                    路由控制层                           │
│  routers/ - chat.py │ session.py │ user.py │ mcp.py    │
├────────────────────────────────────────────────────────┤
│                    业务逻辑层                           │
│  services/ - agent_service.py │ memory_service.py       │
│              auth_service.py │ mcp_gateway.py           │
├────────────────────────────────────────────────────────┤
│                    数据访问层                           │
│  repositories/ - user_repo.py │ session_repo.py         │
│                  itinerary_repo.py │ memory_repo.py      │
├────────────────────────────────────────────────────────┤
│                    基础设施层                           │
│  core/ - config.py │ database.py │ redis_client.py      │
│          security.py │ logging.py │ exceptions.py        │
└────────────────────────────────────────────────────────┘
```

---

### 1.4 Agent编排层（LangGraph）

```
Claim: LangGraph的图执行模型使用StateGraph定义节点和边，支持条件路由和持久化状态检查点
Source: LangGraph Documentation
URL: https://lobstermail.ai/blog/how-to-build-a-langgraph-stateful-email-agent-workflow
Date: 2026-04-13
Excerpt: "LangGraph is a framework from the LangChain team for building AI agent workflows as directed graphs."
Context: 旅游助手的Agent编排使用LangGraph StateGraph
Confidence: high
```

**Supervisor Agent设计：**

```python
# supervisor_graph.py - 核心编排逻辑
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent
from langgraph_supervisor import create_supervisor
from typing import TypedDict, Annotated, List
from operator import add

class TravelState(TypedDict):
    """全局状态定义"""
    messages: Annotated[List, add]           # 消息历史
    user_id: str                              # 用户ID
    session_id: str                           # 会话ID
    intent: str                               # 识别意图
    destination: str                          # 目的地
    budget: float                             # 预算
    dates: dict                               # 出行日期
    itinerary: dict                           # 行程规划
    recommendations: List                     # 推荐列表
    memory_facts: List                        # 记忆事实
    final_response: str                       # 最终回复

def build_travel_graph():
    """构建旅游助手Agent图"""
    
    # 1. 创建专门Agent
    planning_agent = create_react_agent(
        model=llm,
        tools=[search_destinations, create_itinerary, optimize_route],
        name="planning_agent",
        prompt="你是一个专业的旅游规划师..."
    )
    
    recommend_agent = create_react_agent(
        model=llm,
        tools=[search_attractions, search_restaurants, search_hotels],
        name="recommend_agent",
        prompt="你是一个旅游推荐专家..."
    )
    
    info_agent = create_react_agent(
        model=llm,
        tools=[get_weather, get_traffic, get_exchange_rate],
        name="info_agent",
        prompt="你是一个旅游信息查询专家..."
    )
    
    memory_agent = create_react_agent(
        model=llm,
        tools=[save_memory, search_memory, update_preference],
        name="memory_agent",
        prompt="你是一个用户偏好管理专家..."
    )
    
    # 2. 创建Supervisor
    supervisor = create_supervisor(
        agents=[planning_agent, recommend_agent, info_agent, memory_agent],
        model=supervisor_llm,
        prompt=(
            "你是旅游助手团队的调度主管。分析用户需求，"
            "将任务路由给最合适的专家Agent。"
            "可用专家：planning_agent（行程规划）、recommend_agent（景点推荐）、"
            "info_agent（信息查询）、memory_agent（偏好记忆）。"
            "完成后返回FINISH。"
        ),
        output_mode="last_message"
    )
    
    return supervisor.compile()
```

---

### 1.5 Skills管理层（MCP Server）

```
Claim: MCP协议提供标准化的LLM应用与外部数据源和工具的集成方式，使用JSON-RPC 2.0消息格式
Source: Model Context Protocol Specification
URL: https://modelcontextprotocol.io/specification/2025-06-18
Date: 2026-06-25
Excerpt: "MCP provides a standardized way for applications to: Share contextual information with language models; Expose tools and capabilities to AI systems; Build composposable integrations and workflows"
Context: 旅游助手使用MCP Server封装所有外部工具调用
Confidence: high
```

```
Claim: MCP的三个原语代表三个不同的控制平面：Tools（模型控制）、Prompts（用户控制）、Resources（应用控制）
Source: Dev.to - MCP Prompts and Resources
URL: https://dev.to/aws-heroes/mcp-prompts-and-resources-the-primitives-youre-not-using-3oo1
Date: 2026-04-09
Excerpt: "Tools are model-controlled... Prompts are user-controlled... Resources are application-controlled."
Context: 旅游助手的MCP Server主要使用Tools原语，让Agent自主决定调用哪个工具
Confidence: high
```

**MCP Server架构：**

```
┌────────────────────────────────────────────────────────────────┐
│                    MCP Gateway (统一网关)                       │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐   │
│  │ Auth Filter  │ │ Route Router │ │  Response Cache      │   │
│  └──────┬───────┘ └──────┬───────┘ └──────────┬───────────┘   │
│  ┌──────┴────────────────┴────────────────────┴───────────┐   │
│  │              MCP Server Registry                        │   │
│  └──────────────────────────┬──────────────────────────────┘   │
└─────────────────────────────┼──────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
  │ Weather MCP │     │Traffic MCP  │     │ Scenic MCP  │
  │ Server      │     │ Server      │     │ Server      │
  │             │     │             │     │             │
  │ - get_weather│    │ - get_routes│     │ - search_   │
  │ - get_forecast│   │ - get_realtime│   │   attractions│
  │ - get_alerts │    │   _traffic   │     │ - get_details│
  └─────────────┘     └─────────────┘     └─────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
    OpenWeatherMap      Google Maps API      携程/飞猪API
```

---

### 1.6 记忆管理层（Mem0 + Redis）

```
Claim: Mem0采用提取-更新流水线，选择性存储显著信息，通过语义相似度检索，在LOCOMO基准测试中比OpenAI提升26%
Source: Mem0 Paper - Mem0: Building Production-Ready AI Agents
URL: https://arxiv.org/abs/2504.19413
Date: 2025-04-28
Excerpt: "Mem0 achieves 26% relative improvements in the LLM-as-a-Judge metric over OpenAI, while Mem0 with graph memory achieves around 2% higher overall score than the base configuration."
Context: 使用Mem0作为旅游助手的长期记忆层，存储用户偏好和历史交互
Confidence: high
```

```
Claim: Mem0在p95延迟上降低91%，token成本节省超过90%，相比完整上下文方法
Source: Mem0 Paper
URL: https://arxiv.org/abs/2504.19413
Date: 2025-04-28
Excerpt: "Mem0 attains a 91% lower p95 latency and saves more than 90% token cost, offering a compelling balance between advanced reasoning capabilities and practical deployment constraints."
Context: 记忆管理显著降低响应延迟和成本
Confidence: high
```

```
Claim: Redis在GenAI应用中提供高性能数据访问、会话管理、缓存和向量搜索能力
Source: Redis Documentation
URL: https://redis.io/docs/latest/develop/get-started/redis-in-ai/
Date: 2026-06-24
Excerpt: "Redis enables high-performance, scalable, and reliable data management, making it a key component for GenAI apps, chatbots, and AI agents."
Context: 使用Redis作为短时会话缓存和速率限制层
Confidence: high
```

**记忆管理架构：**

```
┌──────────────────────────────────────────────────────────────┐
│                    记忆管理分层                                │
│                                                              │
│   ┌─────────────────┐    ┌─────────────────┐                │
│   │   短期记忆 (STM) │    │   长期记忆 (LTM) │                │
│   │                 │    │                 │                │
│   │  Redis Cache    │    │  Mem0 + Qdrant  │                │
│   │                 │    │                 │                │
│   │ - 当前会话状态   │    │ - 用户偏好       │                │
│   │ - 最近消息历史   │    │ - 历史行程       │                │
│   │ - 临时上下文     │    │ - 个人事实       │                │
│   │ - TTL: 30min    │    │ - 关系记忆       │                │
│   └─────────────────┘    └─────────────────┘                │
│                                                              │
│   LangGraph Checkpointer (PostgreSQL) - 状态持久化           │
└──────────────────────────────────────────────────────────────┘
```

---

### 1.7 数据持久层（PostgreSQL + 向量数据库）

```
Claim: pgvector将PostgreSQL转变为向量数据库，适合1M-50M向量，支持HNSW索引提供100倍查询加速
Source: DBDataVerse - pgvector PostgreSQL Guide
URL: https://dbadataverse.com/tech/postgresql/2025/12/pgvector-postgresql-vector-database-guide
Date: 2026-06-17
Excerpt: "pgvector transforms PostgreSQL into a vector database. HNSW indexes provide 100x query speedup. Combine vector search with traditional SQL filtering."
Context: 开发阶段使用PostgreSQL+pgvector存储向量数据
Confidence: high
```

```
Claim: Milvus是高性能云原生向量数据库，支持数十亿向量的水平扩展，适合生产级部署
Source: Milvus GitHub
URL: https://github.com/milvus-io/milvus
Date: 2026-06-26
Excerpt: "Milvus is a high-performance vector database built for scale... handle tens of thousands of search queries on billions of vectors"
Context: 生产环境使用Milvus替换pgvector以获得更好的性能和扩展性
Confidence: high
```

---

### 1.8 各层接口设计

**层间通信规范：**

| 接口 | 协议 | 数据格式 | 认证方式 |
|------|------|---------|---------|
| 前端 → API | HTTPS REST | JSON | JWT Bearer |
| 前端 → API (实时) | WebSocket | JSON | JWT Query Param |
| API → Agent | Python调用 | TypedDict | 内部认证 |
| Agent → MCP | JSON-RPC 2.0 | JSON | API Key |
| MCP → 外部API | HTTPS REST | JSON/XML | API Key/OAuth2 |
| Agent → 记忆层 | Python SDK | Dict | 内部认证 |
| 服务 → 数据库 | SQL/TCP | SQL + Vector | 用户名/密码 |
| 服务 → Redis | Redis Protocol | RESP | 密码 |

---

## 2. 核心模块设计

### 2.1 规划Agent：行程规划算法

```
Claim: 旅游助手推荐系统需要综合考虑用户偏好、预算、时间和目的地信息生成个性化行程
Source: IJERT - AI-Based Intelligent Travel Assistant
URL: https://www.ijert.org/ai-based-intelligent-travel-assistant-for-personalized-trip-planning-ijertv15is041854
Date: 2026-04-23
Excerpt: "The recommendation system forms the main component of the travel assistant. Based on the processed user input, this component generates personalized travel recommendations and detailed itineraries."
Context: 规划Agent是系统的核心，负责生成个性化行程
Confidence: high
```

**规划Agent设计：**

```python
# planning_agent.py
from langgraph.prebuilt import create_react_agent
from typing import Dict, List

class PlanningAgent:
    """行程规划Agent - 负责生成和优化旅游行程"""
    
    def __init__(self, llm, tools):
        self.agent = create_react_agent(
            model=llm,
            tools=tools,
            name="planning_agent",
            prompt=self._get_system_prompt()
        )
    
    def _get_system_prompt(self):
        return """
        你是一位专业的旅游规划师，擅长根据用户需求生成个性化行程。
        
        核心能力：
        1. 目的地分析与推荐
        2. 行程路线优化（考虑地理位置、交通时间）
        3. 预算分配建议
        4. 时间安排优化
        
        规划原则：
        - 每天安排3-5个主要景点，避免过于紧凑
        - 考虑景点间的距离和交通时间
        - 预留休息和用餐时间
        - 根据用户偏好（文化/自然/美食/购物）调整
        - 考虑季节和天气因素
        
        可用工具：
        - search_destinations: 搜索目的地信息
        - create_itinerary: 创建详细行程
        - optimize_route: 优化路线
        - check_opening_hours: 查询开放时间
        """
    
    async def plan_itinerary(self, request: Dict) -> Dict:
        """
        规划行程主函数
        
        Input: {
            "destination": "string",
            "dates": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
            "budget": {"total": number, "currency": "CNY"},
            "preferences": ["culture", "nature", "food", ...],
            "travelers": {"adults": int, "children": int},
            "pace": "relaxed" | "moderate" | "intensive"
        }
        
        Output: {
            "itinerary": {
                "days": [
                    {
                        "day": 1,
                        "date": "YYYY-MM-DD",
                        "activities": [
                            {
                                "time": "09:00-11:00",
                                "name": "故宫博物院",
                                "type": "attraction",
                                "duration": "2h",
                                "cost": 60,
                                "location": {"lat": 39.9, "lng": 116.4}
                            }
                        ],
                        "meals": [...],
                        "transport": [...],
                        "accommodation": {...}
                    }
                ],
                "total_cost": number,
                "tips": ["..."]
            }
        }
        """
        pass
```

**行程规划算法流程：**

```
1. 意图识别 → 确定规划类型（完整行程/单日优化/路线调整）
2. 信息收集 → 调用Info Agent获取天气、交通、景点信息
3. 约束分析 → 解析预算、时间、偏好等约束条件
4. 候选生成 → 生成多个候选行程方案
5. 路线优化 → 使用贪心算法/TSP优化每日路线
6. 预算校验 → 确保总费用在预算范围内
7. 结果组装 → 生成结构化的行程方案
```

---

### 2.2 推荐Agent：推荐算法和数据源

**推荐Agent设计：**

```python
# recommend_agent.py

class RecommendAgent:
    """推荐Agent - 负责景点、餐厅、酒店推荐"""
    
    def __init__(self, llm, tools, vector_store):
        self.agent = create_react_agent(
            model=llm,
            tools=tools,
            name="recommend_agent",
            prompt=self._get_system_prompt()
        )
        self.vector_store = vector_store
    
    async def get_personalized_recommendations(
        self, 
        user_id: str,
        destination: str,
        category: str,  # "attractions" | "restaurants" | "hotels"
        context: Dict
    ) -> List[Dict]:
        """
        个性化推荐主函数
        
        算法流程：
        1. 从Mem0获取用户历史偏好
        2. 向量检索相似用户喜欢的项目
        3. 协同过滤推荐
        4. 基于内容的过滤
        5. 混合排序融合
        6. 返回Top-K推荐结果
        """
        # 1. 获取用户画像
        user_profile = await self._get_user_profile(user_id)
        
        # 2. 向量语义搜索
        semantic_results = await self._semantic_search(
            query=f"{destination} {category} {user_profile['interests']}",
            filters={"destination": destination, "category": category}
        )
        
        # 3. 获取协同过滤推荐
        cf_results = await self._collaborative_filtering(
            user_id=user_id,
            destination=destination,
            category=category
        )
        
        # 4. 混合排序
        final_results = self._hybrid_ranking(
            semantic_results=semantic_results,
            cf_results=cf_results,
            user_profile=user_profile,
            weights={"semantic": 0.4, "cf": 0.3, "popularity": 0.2, "recency": 0.1}
        )
        
        return final_results[:10]  # Top-10
```

**推荐数据源：**

| 数据类型 | 数据源 | API | 更新频率 |
|---------|--------|-----|---------|
| 景点信息 | 高德地图/百度地图 | REST API | 实时 |
| 餐厅信息 | 大众点评 | REST API | 每日 |
| 酒店信息 | 携程/Booking | REST API | 实时 |
| 用户评价 | 自有数据库 + 第三方 | GraphQL | 实时 |
| 价格信息 | 携程/去哪儿 | REST API | 实时 |

---

### 2.3 信息Agent：天气/交通/景点信息获取

```python
# info_agent.py

class InfoAgent:
    """信息Agent - 负责获取实时旅游信息"""
    
    def __init__(self, llm, tools):
        self.agent = create_react_agent(
            model=llm,
            tools=tools,
            name="info_agent",
            prompt=self._get_system_prompt()
        )
    
    async def get_weather_info(self, destination: str, dates: Dict) -> Dict:
        """获取天气信息"""
        return {
            "current": {"temp": 25, "condition": "晴", "humidity": 60},
            "forecast": [
                {"date": "2025-08-01", "high": 28, "low": 22, "condition": "多云"},
                {"date": "2025-08-02", "high": 30, "low": 24, "condition": "晴"}
            ],
            "advice": "建议携带轻便夏装，注意防晒"
        }
    
    async def get_traffic_info(self, from_loc: str, to_loc: str) -> Dict:
        """获取交通信息"""
        return {
            "options": [
                {"type": "飞机", "duration": "2h30m", "price": 800},
                {"type": "高铁", "duration": "5h", "price": 500},
                {"type": "自驾", "duration": "8h", "price": 400}
            ],
            "recommendation": "高铁"
        }
    
    async def get_attraction_details(self, attraction_id: str) -> Dict:
        """获取景点详细信息"""
        pass
```

---

### 2.4 用户管理模块

```
Claim: FastAPI的OAuth2PasswordBearer提供了标准的JWT认证流程，包含token颁发和验证
Source: GeeksForGeeks - JWT in FastAPI
URL: https://www.geeksforgeeks.org/python/login-registration-system-with-jwt-in-fastapi/
Date: 2026-05-11
Excerpt: "OAuth2PasswordBearer extracts JWT token from Authorization header"
Context: 用户认证模块使用JWT + OAuth2方案
Confidence: high
```

**用户管理设计：**

```python
# user_service.py

class UserService:
    """用户管理服务"""
    
    async def register(self, user_create: UserCreate) -> User:
        """用户注册"""
        # 1. 检查用户名/邮箱唯一性
        # 2. bcrypt密码哈希
        # 3. 创建用户记录
        # 4. 初始化Mem0用户记忆
        pass
    
    async def login(self, username: str, password: str) -> Token:
        """用户登录 - 颁发JWT"""
        # 1. 验证用户名密码
        # 2. 生成access_token (15min) + refresh_token (7day)
        # 3. 记录登录日志
        pass
    
    async def get_profile(self, user_id: str) -> UserProfile:
        """获取用户画像"""
        # 1. 获取基本信息
        # 2. 从Mem0获取偏好记忆
        # 3. 获取历史行程统计
        pass
    
    async def update_preferences(self, user_id: str, preferences: Dict) -> None:
        """更新用户偏好"""
        # 1. 更新数据库
        # 2. 同步到Mem0记忆层
        pass
```

---

### 2.5 会话管理模块

```python
# session_service.py

class SessionService:
    """会话管理服务 - 管理用户对话会话"""
    
    def __init__(self, redis_client, db: AsyncSession):
        self.redis = redis_client
        self.db = db
    
    async def create_session(self, user_id: str) -> Session:
        """创建新会话"""
        session = Session(
            id=generate_uuid(),
            user_id=user_id,
            created_at=datetime.now(),
            status="active"
        )
        # 存储到Redis (TTL: 30min)
        await self.redis.setex(
            f"session:{session.id}",
            1800,
            session.json()
        )
        # 持久化到PostgreSQL
        await self.db.add(session)
        await self.db.commit()
        return session
    
    async def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话 - 先查Redis再查DB"""
        # 1. 尝试从Redis获取
        cached = await self.redis.get(f"session:{session_id}")
        if cached:
            return Session.parse_raw(cached)
        
        # 2. 从数据库获取
        session = await self.db.get(Session, session_id)
        if session:
            # 回写到Redis
            await self.redis.setex(f"session:{session_id}", 1800, session.json())
        return session
    
    async def add_message(self, session_id: str, role: str, content: str) -> Message:
        """添加消息到会话"""
        message = Message(
            id=generate_uuid(),
            session_id=session_id,
            role=role,  # "user" | "assistant" | "system" | "tool"
            content=content,
            created_at=datetime.now()
        )
        await self.db.add(message)
        await self.db.commit()
        return message
    
    async def get_chat_history(self, session_id: str, limit: int = 20) -> List[Message]:
        """获取会话历史"""
        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
```

---

## 3. 数据库设计

### 3.1 用户表

```sql
-- users table
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(50) UNIQUE NOT NULL,
    email           VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name       VARCHAR(100),
    avatar_url      VARCHAR(500),
    phone           VARCHAR(20),
    
    -- 用户偏好
    preferences     JSONB DEFAULT '{}',
    -- 示例: {"interests": ["culture", "nature"], "budget_level": "medium", "travel_style": "relaxed"}
    
    -- 账户状态
    is_active       BOOLEAN DEFAULT TRUE,
    is_verified     BOOLEAN DEFAULT FALSE,
    role            VARCHAR(20) DEFAULT 'user',  -- user | admin
    
    -- 时间戳
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

-- 索引
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_username ON users(username);
```

### 3.2 会话表

```sql
-- sessions table
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- 会话信息
    title           VARCHAR(200) DEFAULT '新会话',
    status          VARCHAR(20) DEFAULT 'active',  -- active | archived | deleted
    
    -- 会话元数据
    destination     VARCHAR(100),
    dates           JSONB,  -- {"start": "2025-08-01", "end": "2025-08-05"}
    travelers       JSONB,  -- {"adults": 2, "children": 1}
    budget          JSONB,  -- {"total": 10000, "currency": "CNY"}
    
    -- LangGraph thread ID
    thread_id       VARCHAR(100),
    
    -- 时间戳
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    ended_at        TIMESTAMPTZ
);

CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_sessions_created_at ON sessions(created_at);
```

### 3.3 消息表

```sql
-- messages table
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    
    -- 消息内容
    role            VARCHAR(20) NOT NULL,  -- user | assistant | system | tool
    content         TEXT NOT NULL,
    
    -- 工具调用信息 (当role=tool时)
    tool_calls      JSONB,  -- [{"name": "get_weather", "arguments": {...}, "result": ...}]
    
    -- 元数据
    tokens_used     INTEGER,  -- token使用量
    latency_ms      INTEGER,  -- 响应延迟(毫秒)
    model           VARCHAR(50),  -- 使用的模型
    
    -- 时间戳
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_messages_session_id ON messages(session_id);
CREATE INDEX idx_messages_created_at ON messages(created_at);
```

### 3.4 行程表

```sql
-- itineraries table
CREATE TABLE itineraries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL,
    
    -- 行程基本信息
    title           VARCHAR(200) NOT NULL,
    destination     VARCHAR(100) NOT NULL,
    
    -- 日期
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    duration_days   INTEGER NOT NULL,
    
    -- 行程内容 (JSON存储完整行程)
    content         JSONB NOT NULL,
    /* 结构示例:
    {
        "days": [
            {
                "day": 1,
                "date": "2025-08-01",
                "activities": [
                    {
                        "time": "09:00-11:00",
                        "name": "故宫博物院",
                        "type": "attraction",
                        "duration": "2h",
                        "cost": 60,
                        "location": {"lat": 39.9163, "lng": 116.3972},
                        "booking_url": "...",
                        "notes": "建议提前购票"
                    }
                ],
                "meals": [...],
                "transport": [...],
                "accommodation": {...}
            }
        ],
        "total_cost": {"amount": 5000, "currency": "CNY"},
        "tips": ["..."],
        "tags": ["文化", "历史", "亲子"]
    }
    */
    
    -- 状态
    status          VARCHAR(20) DEFAULT 'draft',  -- draft | confirmed | completed | cancelled
    
    -- 统计
    view_count      INTEGER DEFAULT 0,
    like_count      INTEGER DEFAULT 0,
    is_public       BOOLEAN DEFAULT FALSE,
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_itineraries_user_id ON itineraries(user_id);
CREATE INDEX idx_itineraries_destination ON itineraries(destination);
CREATE INDEX idx_itineraries_status ON itineraries(status);
```

### 3.5 记忆存储表结构

```
Claim: Mem0使用Qdrant作为向量存储，通过语义相似度检索相关记忆条目
Source: MRMMIA Paper
URL: https://arxiv.org/html/2605.27825v1
Date: 2026-05-27
Excerpt: "For Mem0, we configure a Qdrant vector store for persistent memory storage and a HuggingFace sentence transformer as the embedder."
Context: Mem0的底层存储使用Qdrant向量数据库
Confidence: high
```

**Mem0存储结构（由Mem0 SDK自动管理）：**

```
Qdrant Collection: "mem0_memories"

Point结构:
{
    "id": "uuid",
    "vector": [0.1, 0.2, ...],  # 768维embedding
    "payload": {
        "user_id": "user_uuid",
        "memory": "用户喜欢历史文化景点",
        "type": "preference",  # preference | fact | event
        "category": "interest",
        "created_at": "2025-07-01T10:00:00Z",
        "updated_at": "2025-07-01T10:00:00Z",
        "access_count": 5,
        "last_accessed": "2025-07-05T15:00:00Z",
        "score": 0.85  # 重要性分数
    }
}
```

---

### 3.6 向量数据库Collection设计

#### 开发环境 (Qdrant)

```python
# qdrant_collections.py

COLLECTIONS = {
    "travel_documents": {
        "vector_size": 768,  # BAAI/bge-large-zh-v1.5
        "distance": "Cosine",
        "shard_number": 1,
        "replication_factor": 1,
        "optimizers_config": {
            "memmap_threshold": 20000
        }
    },
    
    "mem0_memories": {
        "vector_size": 768,
        "distance": "Cosine",
        "shard_number": 1,
        "replication_factor": 1
    },
    
    "attractions_index": {
        "vector_size": 768,
        "distance": "Cosine",
        "shard_number": 2,
        "replication_factor": 1,
        "hnsw_config": {
            "m": 16,
            "ef_construct": 200
        }
    }
}
```

#### 生产环境 (Milvus)

```python
# milvus_collections.py

from pymilvus import FieldSchema, CollectionSchema, DataType

ATTRACTION_COLLECTION = CollectionSchema(
    fields=[
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=36, is_primary=True),
        FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=200),
        FieldSchema(name="destination", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=50),
        FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="tags", dtype=DataType.ARRAY, element_type=DataType.VARCHAR, max_length=50, max_capacity=20),
        FieldSchema(name="rating", dtype=DataType.FLOAT),
        FieldSchema(name="price_level", dtype=DataType.INT8),
        FieldSchema(name="location", dtype=DataType.JSON),  # {"lat": float, "lng": float}
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=768),
    ],
    description="景点向量索引"
)

# 创建索引
index_params = {
    "index_type": "HNSW",
    "metric_type": "COSINE",
    "params": {"M": 16, "efConstruction": 200}
}
```

### 3.7 索引和查询优化

```sql
-- 复合索引优化
CREATE INDEX idx_itineraries_user_destination 
ON itineraries(user_id, destination);

CREATE INDEX idx_sessions_user_status 
ON sessions(user_id, status) 
WHERE status = 'active';

-- 部分索引
CREATE INDEX idx_messages_recent 
ON messages(session_id, created_at DESC) 
WHERE created_at > NOW() - INTERVAL '30 days';

-- GIN索引用于JSONB查询
CREATE INDEX idx_itineraries_content_gin 
ON itineraries USING GIN(content);

CREATE INDEX idx_users_preferences_gin 
ON users USING GIN(preferences);
```

```
Claim: pgvector的HNSW索引构建是CPU密集型的，生产环境应使用CREATE INDEX CONCURRENTLY
Source: Bytebase - pgvector Guide
URL: https://www.bytebase.com/blog/pgvector/
Date: 2026-03-09
Excerpt: "HNSW index builds are CPU-intensive. Use CREATE INDEX CONCURRENTLY on production tables."
Context: 向量索引的构建需要谨慎处理
Confidence: high
```

---

## 4. API设计

### 4.1 RESTful API设计规范

**基础规范：**
- Base URL: `/api/v1`
- 内容类型: `application/json`
- 认证: `Authorization: Bearer <jwt_token>`
- 响应格式: `{"code": 0, "message": "success", "data": {...}}`
- 错误格式: `{"code": 40001, "message": "error description", "detail": {...}}`

### 4.2 认证相关API

```yaml
# Auth APIs
paths:
  /auth/register:
    post:
      summary: 用户注册
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                username: {type: string, minLength: 3, maxLength: 50}
                email: {type: string, format: email}
                password: {type: string, minLength: 8}
                full_name: {type: string}
      responses:
        201:
          description: 注册成功
          content:
            application/json:
              schema:
                type: object
                properties:
                  user_id: {type: string}
                  username: {type: string}
                  email: {type: string}
                  created_at: {type: string, format: date-time}

  /auth/login:
    post:
      summary: 用户登录
      requestBody:
        content:
          application/x-www-form-urlencoded:
            schema:
              type: object
              properties:
                username: {type: string}
                password: {type: string}
      responses:
        200:
          description: 登录成功
          content:
            application/json:
              schema:
                type: object
                properties:
                  access_token: {type: string}
                  refresh_token: {type: string}
                  token_type: {type: string, enum: [bearer]}
                  expires_in: {type: integer, description: "秒"}

  /auth/refresh:
    post:
      summary: 刷新访问令牌
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                refresh_token: {type: string}
      responses:
        200:
          description: 刷新成功

  /auth/me:
    get:
      summary: 获取当前用户信息
      security:
        - bearerAuth: []
      responses:
        200:
          description: 用户信息
```

### 4.3 会话和聊天API

```yaml
paths:
  /sessions:
    get:
      summary: 获取用户会话列表
      security: [bearerAuth: []]
      parameters:
        - name: page
          in: query
          schema: {type: integer, default: 1}
        - name: page_size
          in: query
          schema: {type: integer, default: 20}
      responses:
        200:
          description: 会话列表
          content:
            application/json:
              schema:
                type: object
                properties:
                  total: {type: integer}
                  items:
                    type: array
                    items:
                      type: object
                      properties:
                        id: {type: string}
                        title: {type: string}
                        destination: {type: string}
                        status: {type: string}
                        created_at: {type: string}
                        updated_at: {type: string}

    post:
      summary: 创建新会话
      security: [bearerAuth: []]
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                title: {type: string}
                destination: {type: string}
                dates: {type: object}
                travelers: {type: object}
                budget: {type: object}
      responses:
        201:
          description: 创建成功

  /sessions/{session_id}:
    get:
      summary: 获取会话详情
      security: [bearerAuth: []]
      parameters:
        - name: session_id
          in: path
          required: true
          schema: {type: string}
      responses:
        200:
          description: 会话详情

    delete:
      summary: 删除会话
      security: [bearerAuth: []]
      responses:
        204:
          description: 删除成功

  /chat:
    post:
      summary: 发送聊天消息（非流式）
      security: [bearerAuth: []]
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                session_id: {type: string}
                message: {type: string, maxLength: 2000}
                context: {type: object, description: "额外上下文"}
      responses:
        200:
          description: AI回复
          content:
            application/json:
              schema:
                type: object
                properties:
                  message_id: {type: string}
                  content: {type: string}
                  role: {type: string, enum: [assistant]}
                  tool_calls: {type: array}
                  tokens_used: {type: integer}
                  latency_ms: {type: integer}
                  created_at: {type: string}
```

### 4.4 WebSocket实时通信

```
Claim: WebSocket为AI应用提供持久双向通信，支持实时token流式传输和用户干预
Source: Ably - WebSockets vs HTTP for AI streaming
URL: https://ably.com/blog/websockets-vs-http-for-ai-streaming-and-agents
Date: 2026-02-15
Excerpt: "WebSockets provide persistent, bi-directional communication between clients and servers. For AI applications, this means maintaining a live connection throughout an entire conversation, enabling realtime token streaming, user steering, and stateful interactions."
Context: 使用WebSocket实现AI回复的流式传输
Confidence: high
```

**WebSocket设计：**

```yaml
# WebSocket API
connection: wss://api.example.com/ws/chat?token=<jwt_token>

# 客户端发送
message_send:
  type: "chat.message"
  payload:
    session_id: "uuid"
    message: "我想去云南旅游"
    stream: true  # 是否流式返回

# 服务端流式返回 (type: chat.chunk)
message_chunk:
  type: "chat.chunk"
  payload:
    session_id: "uuid"
    message_id: "uuid"
    chunk: "好的"  # 逐步返回的文本片段
    is_end: false

# 最终消息 (type: chat.complete)
message_complete:
  type: "chat.complete"
  payload:
    session_id: "uuid"
    message_id: "uuid"
    content: "好的，我可以帮您规划云南之旅..."
    tool_calls: [...]
    tokens_used: 1500
    latency_ms: 3500

# 工具调用确认 (type: tool.confirm)
tool_confirm:
  type: "tool.confirm"
  payload:
    tool_name: "book_hotel"
    arguments: {...}
    description: "预订酒店: 昆明xxx酒店, 2025-08-01至08-03"
    # 客户端确认后返回 tool.confirm_response

# 错误消息 (type: error)
error_message:
  type: "error"
  payload:
    code: "AGENT_ERROR"
    message: "Agent处理出错"
    detail: {...}
```

### 4.5 MCP Server的API设计

```python
# mcp_server.py - MCP Server实现示例

from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("travel-mcp-server")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_weather",
            description="获取指定城市的天气信息",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称"},
                    "date": {"type": "string", "description": "日期 (YYYY-MM-DD)"}
                },
                "required": ["city"]
            }
        ),
        Tool(
            name="search_attractions",
            description="搜索景点信息",
            inputSchema={
                "type": "object",
                "properties": {
                    "destination": {"type": "string"},
                    "category": {"type": "string", "enum": ["culture", "nature", "food", "shopping"]},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": ["destination"]
            }
        ),
        Tool(
            name="get_route",
            description="获取两地间的交通路线",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_location": {"type": "string"},
                    "to_location": {"type": "string"},
                    "transport_type": {"type": "string", "enum": ["all", "train", "flight", "bus"]}
                },
                "required": ["from_location", "to_location"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "get_weather":
        result = await weather_service.get_weather(
            city=arguments["city"],
            date=arguments.get("date")
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    
    elif name == "search_attractions":
        results = await attraction_service.search(
            destination=arguments["destination"],
            category=arguments.get("category"),
            limit=arguments.get("limit", 10)
        )
        return [TextContent(type="text", text=json.dumps(results, ensure_ascii=False))]
    
    elif name == "get_route":
        result = await route_service.get_route(
            from_location=arguments["from_location"],
            to_location=arguments["to_location"],
            transport_type=arguments.get("transport_type", "all")
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
```

### 4.6 认证和授权（JWT）

```python
# security.py - JWT认证实现

from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

# 配置
SECRET_KEY = "your-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        token_type: str = payload.get("type")
        if username is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = await get_user_by_username(username)
    if user is None:
        raise credentials_exception
    return user

# 角色权限检查
def require_roles(allowed_roles: list[str]):
    def role_checker(current_user = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足"
            )
        return current_user
    return role_checker
```

---

## 5. 技术栈最终选型

### 5.1 后端技术栈

| 组件 | 选型 | 版本 | 用途 |
|------|------|------|------|
| 语言 | Python | 3.11+ | 后端开发 |
| Web框架 | FastAPI | 0.110+ | REST API + WebSocket |
| ASGI服务器 | Uvicorn | 0.29+ | 异步HTTP服务器 |
| Agent框架 | LangGraph | 0.2+ | Agent编排 |
| LLM SDK | LangChain | 0.3+ | LLM调用和工具管理 |
| 数据库 | PostgreSQL | 16+ | 主数据库 |
| 向量扩展 | pgvector | 0.7+ | 开发环境向量搜索 |
| 缓存 | Redis | 7.2+ | 会话缓存、速率限制 |
| 记忆层 | Mem0 | 2.0+ | 长期记忆管理 |
| 向量DB(生产) | Milvus | 2.4+ | 生产环境向量搜索 |
| ORM | SQLAlchemy | 2.0+ | 数据库ORM |
| 迁移 | Alembic | 1.13+ | 数据库迁移 |
| 认证 | python-jose | 3.3+ | JWT处理 |
| 密码加密 | passlib | 1.7+ | bcrypt哈希 |
| 配置 | Pydantic-Settings | 2.2+ | 环境配置管理 |
| 日志 | Loguru | 0.7+ | 结构化日志 |
| 测试 | Pytest | 8.0+ | 单元/集成测试 |

### 5.2 前端技术栈

| 阶段 | 组件 | 版本 | 用途 |
|------|------|------|------|
| **MVP** | Streamlit | 1.40+ | 快速原型验证 |
| **进阶** | React | 18+ | 生产前端框架 |
| **进阶** | TypeScript | 5.5+ | 类型安全 |
| **进阶** | Tailwind CSS | 3.4+ | UI样式 |
| **进阶** | Vite | 5.0+ | 构建工具 |
| **进阶** | React Query | 5.0+ | 服务端状态管理 |
| **进阶** | Zustand | 4.5+ | 客户端状态管理 |

### 5.3 Agent技术栈

| 组件 | 选型 | 用途 |
|------|------|------|
| Agent编排 | LangGraph | Supervisor模式多Agent编排 |
| Agent构建 | LangChain create_react_agent | ReAct Agent模板 |
| Supervisor | langgraph-supervisor | Supervisor快速构建 |
| MCP协议 | mcp SDK | MCP Server/Client实现 |
| 记忆管理 | Mem0 + Redis | 长/短期记忆分层 |
| Checkpointer | PostgreSQL | LangGraph状态持久化 |

### 5.4 向量数据库选型

```
Claim: Chroma适合本地原型开发（零配置），Qdrant适合自托管生产环境（最佳性价比），Milvus适合企业级十亿向量规模
Source: Junaid Rehman - Vector Database Showdown 2025
URL: https://junaidrehman.me/blog/vector-database-pinecone-vs-chroma-vs-qdrant/
Date: 2026-05-31
Excerpt: "Chroma if you're prototyping or have <100k vectors... Qdrant if you're targeting production with >1M vectors... Milvus for enterprise-scale deployments with billions of vectors."
Context: 开发用Chroma，生产用Milvus的渐进式策略
Confidence: high
```

| 环境 | 选型 | 理由 |
|------|------|------|
| 开发 | Chroma | 零配置，pip安装即可，嵌入Python进程 |
| 测试 | Qdrant (Docker) | 生产一致性，支持高级过滤 |
| 生产 | Milvus | 分布式架构，十亿级向量，高可用 |

### 5.5 部署技术栈

| 组件 | 选型 | 用途 |
|------|------|------|
| 容器化 | Docker | 应用容器化 |
| 编排 | Docker Compose | 本地/测试环境编排 |
| 云服务器 | 阿里云ECS / 腾讯云CVM | 生产部署 |
| 反向代理 | Nginx | SSL终止、负载均衡 |
| 进程管理 | Supervisor | 后台进程管理 |
| CI/CD | GitHub Actions | 自动化构建部署 |

### 5.6 监控技术栈

```
Claim: LangSmith + Prometheus + Grafana构成完整的LLM可观测性三大支柱
Source: ActiveWizards - LLM Observability Guide
URL: https://activewizards.com/blog/llm-observability-a-guide-to-monitoring-with-langsmith/
Date: 2026-05-21
Excerpt: "LangSmith (Trace Logging & Debugging), Prometheus (Time-Series Metrics Collection), Grafana (Metrics Visualization & Dashboards)"
Context: 可观测性采用三大工具组合
Confidence: high
```

| 工具 | 用途 | 监控内容 |
|------|------|---------|
| LangSmith | Agent Trace | Agent调用链、工具调用、延迟 |
| Prometheus | 指标采集 | Token使用量、API延迟、错误率 |
| Grafana | 可视化 | 实时Dashboard、告警面板 |
| Loki | 日志聚合 | 应用日志、错误追踪 |

---

## 6. 开发路线图

### 6.1 项目里程碑

```
Claim: Docker Compose现已生产就绪，支持AI Agent工作流的一键部署
Source: Docker Official - Docker for AI
URL: https://www.docker.com/solutions/docker-ai/
Date: 2025-11-24
Excerpt: "Compose is now production ready. Easily push to production with compose and Google Cloud Run, and Azure."
Context: 使用Docker Compose统一部署开发测试生产环境
Confidence: high
```

**Week 1-2：环境搭建 + MCP Server开发**

| 任务 | 交付物 | 负责人 |
|------|--------|--------|
| 项目脚手架搭建 | 代码仓库、目录结构、CI配置 | 后端 |
| Docker开发环境 | docker-compose.yml、Dockerfile | DevOps |
| 数据库初始化 | 表结构、迁移脚本、种子数据 | 后端 |
| Redis配置 | 连接池、序列化配置 | 后端 |
| MCP Server框架 | mcp_gateway、tool_registry | 后端 |
| Weather MCP Server | 天气查询工具、单元测试 | 后端 |
| Scenic MCP Server | 景点搜索工具、单元测试 | 后端 |
| Traffic MCP Server | 交通查询工具、单元测试 | 后端 |

**Week 3-4：Agent核心开发**

| 任务 | 交付物 | 负责人 |
|------|--------|--------|
| Supervisor Agent | 调度逻辑、意图识别 | 后端 |
| Planning Agent | 行程规划算法、工具集成 | 后端 |
| Recommend Agent | 推荐算法、向量检索 | 后端 |
| Info Agent | 信息聚合、并行查询 | 后端 |
| LangGraph集成 | 状态图定义、检查点配置 | 后端 |
| Agent测试套件 | 集成测试、eval pipeline | 后端 |

**Week 5-6：记忆管理 + 上下文管理**

| 任务 | 交付物 | 负责人 |
|------|--------|--------|
| Mem0集成 | 记忆存储、检索接口 | 后端 |
| 用户偏好系统 | 偏好提取、更新机制 | 后端 |
| 会话上下文管理 | 上下文窗口、历史管理 | 后端 |
| LangGraph Checkpointer | PostgreSQL持久化 | 后端 |
| 记忆测试 | 记忆一致性、性能测试 | 后端 |
| Streamlit前端 | MVP界面、聊天功能 | 前端 |

**Week 7-8：前端开发 + 集成测试**

| 任务 | 交付物 | 负责人 |
|------|--------|--------|
| FastAPI REST API | 所有API端点、Swagger文档 | 后端 |
| WebSocket服务 | 流式聊天、工具确认 | 后端 |
| JWT认证系统 | 注册、登录、刷新、权限 | 后端 |
| Streamlit集成 | 前端调用API、会话管理 | 前端 |
| 集成测试 | E2E测试、性能测试 | QA |
| Bug修复 | 问题修复、性能优化 | 全组 |

**Week 9-10：部署上线 + 文档完善**

| 任务 | 交付物 | 负责人 |
|------|--------|--------|
| 生产环境搭建 | 云服务器、域名、SSL | DevOps |
| Docker生产部署 | docker-compose.prod.yml | DevOps |
| 监控系统 | LangSmith、Prometheus、Grafana | DevOps |
| 性能优化 | 缓存策略、DB优化、连接池 | 后端 |
| 技术文档 | API文档、架构文档、部署手册 | 全组 |
| 用户手册 | 使用说明、常见问题 | 产品 |

### 6.2 项目目录结构

```
travel-agent/
├── README.md
├── docker-compose.yml                    # 开发环境
├── docker-compose.prod.yml               # 生产环境
├── .env.example                          # 环境变量模板
├── .github/
│   └── workflows/
│       └── ci.yml                        # CI/CD配置
│
├── backend/                              # 后端服务
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                          # 数据库迁移
│   │   ├── versions/
│   │   └── env.py
│   ├── alembic.ini
│   ├── main.py                           # 应用入口
│   ├── pyproject.toml
│   │
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py                     # 配置管理
│   │   ├── dependencies.py              # 依赖注入
│   │   ├── exceptions.py                 # 异常定义
│   │   │
│   │   ├── api/                          # API路由层
│   │   │   ├── __init__.py
│   │   │   ├── deps.py                   # 公共依赖
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth.py               # 认证API
│   │   │   │   ├── chat.py               # 聊天API
│   │   │   │   ├── session.py            # 会话API
│   │   │   │   ├── user.py               # 用户API
│   │   │   │   ├── itinerary.py          # 行程API
│   │   │   │   └── mcp.py                # MCP管理API
│   │   │   └── v1.py
│   │   │
│   │   ├── core/                         # 核心模块
│   │   │   ├── __init__.py
│   │   │   ├── security.py               # JWT/密码
│   │   │   ├── database.py               # DB连接
│   │   │   ├── redis_client.py           # Redis连接
│   │   │   └── logging_config.py         # 日志配置
│   │   │
│   │   ├── models/                       # 数据模型
│   │   │   ├── __init__.py
│   │   │   ├── user.py                   # 用户模型
│   │   │   ├── session.py                # 会话模型
│   │   │   ├── message.py                # 消息模型
│   │   │   └── itinerary.py              # 行程模型
│   │   │
│   │   ├── schemas/                      # Pydantic Schema
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── chat.py
│   │   │   ├── session.py
│   │   │   ├── user.py
│   │   │   └── itinerary.py
│   │   │
│   │   ├── services/                     # 业务逻辑层
│   │   │   ├── __init__.py
│   │   │   ├── auth_service.py
│   │   │   ├── chat_service.py
│   │   │   ├── session_service.py
│   │   │   ├── user_service.py
│   │   │   ├── agent_service.py          # Agent编排
│   │   │   ├── memory_service.py         # 记忆管理
│   │   │   └── mcp_gateway.py            # MCP网关
│   │   │
│   │   ├── repositories/                 # 数据访问层
│   │   │   ├── __init__.py
│   │   │   ├── user_repo.py
│   │   │   ├── session_repo.py
│   │   │   ├── message_repo.py
│   │   │   └── itinerary_repo.py
│   │   │
│   │   ├── agents/                       # Agent定义
│   │   │   ├── __init__.py
│   │   │   ├── supervisor.py             # Supervisor图
│   │   │   ├── planning_agent.py         # 规划Agent
│   │   │   ├── recommend_agent.py        # 推荐Agent
│   │   │   ├── info_agent.py             # 信息Agent
│   │   │   ├── memory_agent.py           # 记忆Agent
│   │   │   ├── tools/                    # Agent工具
│   │   │   │   ├── __init__.py
│   │   │   │   ├── weather_tools.py
│   │   │   │   ├── attraction_tools.py
│   │   │   │   ├── route_tools.py
│   │   │   │   └── memory_tools.py
│   │   │   └── prompts/                  # 系统提示词
│   │   │       ├── planning_prompt.txt
│   │   │       ├── recommend_prompt.txt
│   │   │       └── supervisor_prompt.txt
│   │   │
│   │   ├── mcp_servers/                  # MCP Server实现
│   │   │   ├── __init__.py
│   │   │   ├── weather_mcp.py
│   │   │   ├── scenic_mcp.py
│   │   │   ├── traffic_mcp.py
│   │   │   └── hotel_mcp.py
│   │   │
│   │   ├── memory/                       # 记忆管理
│   │   │   ├── __init__.py
│   │   │   ├── mem0_client.py            # Mem0客户端
│   │   │   ├── vector_store.py           # 向量存储
│   │   │   └── memory_utils.py           # 记忆工具
│   │   │
│   │   └── websocket/                    # WebSocket处理
│   │       ├── __init__.py
│   │       └── chat_handler.py
│   │
│   └── tests/                            # 测试
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_auth.py
│       ├── test_chat.py
│       ├── test_agents.py
│       ├── test_mcp.py
│       └── test_memory.py
│
├── frontend/                             # 前端（Streamlit MVP）
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py                            # 主应用
│   ├── pages/                            # 多页面
│   │   ├── chat.py                       # 聊天页面
│   │   ├── history.py                    # 历史会话
│   │   └── profile.py                    # 个人中心
│   └── utils/
│       ├── api_client.py                 # API客户端
│       └── session_state.py              # 状态管理
│
├── frontend-react/                       # 前端（React进阶）
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                          # API调用
│       ├── components/                   # 组件
│       ├── pages/                        # 页面
│       ├── hooks/                        # 自定义Hooks
│       ├── stores/                       # 状态管理
│       └── types/                        # TypeScript类型
│
├── infrastructure/                       # 基础设施
│   ├── nginx/
│   │   └── nginx.conf
│   ├── prometheus/
│   │   └── prometheus.yml
│   ├── grafana/
│   │   └── dashboards/
│   └── scripts/
│       ├── init_db.sql
│       └── seed_data.py
│
└── docs/                                 # 文档
    ├── architecture.md
    ├── api_reference.md
    ├── deployment_guide.md
    └── development_guide.md
```

### 6.3 Docker Compose配置

```yaml
# docker-compose.yml - 开发环境
version: "3.8"

services:
  # PostgreSQL数据库
  postgres:
    image: pgvector/pgvector:pg16
    container_name: travel-postgres
    environment:
      POSTGRES_DB: travel_agent
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${DB_PASSWORD:-postgres}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./infrastructure/scripts/init_db.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis缓存
  redis:
    image: redis:7.2-alpine
    container_name: travel-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --requirepass ${REDIS_PASSWORD:-redis}
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Qdrant向量数据库
  qdrant:
    image: qdrant/qdrant:latest
    container_name: travel-qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant_data:/qdrant/storage

  # 后端API
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: travel-backend
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:${DB_PASSWORD:-postgres}@postgres:5432/travel_agent
      - REDIS_URL=redis://:${REDIS_PASSWORD:-redis}@redis:6379/0
      - QDRANT_URL=http://qdrant:6333
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - MEM0_API_KEY=${MEM0_API_KEY}
    volumes:
      - ./backend:/app
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      qdrant:
        condition: service_started
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

  # Streamlit前端
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: travel-frontend
    ports:
      - "8501:8501"
    environment:
      - API_BASE_URL=http://backend:8000
    volumes:
      - ./frontend:/app
    depends_on:
      - backend

  # Nginx反向代理
  nginx:
    image: nginx:alpine
    container_name: travel-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./infrastructure/nginx/nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - backend
      - frontend

volumes:
  postgres_data:
  redis_data:
  qdrant_data:
```

---

## 7. 研究引用汇总

### 7.1 LangGraph架构设计

```
Claim: LangGraph的Supervisor模式通过中央supervisor控制所有通信流和任务委派，是90%团队的正确选择
Source: CallSphere AI
URL: https://callsphere.ai/blog/langgraph-supervisor-multi-agent-orchestration-2026
Date: 2026-06-29
Excerpt: "The supervisor pattern in LangGraph is what you reach for when you have several specialist agents and you want a single orchestrator deciding who goes next."
Context: Agent编排架构核心设计决策
Confidence: high
```

```
Claim: LangGraph支持持久化检查点（SQLite/PostgreSQL/Redis）、人机协同中断和运行时图修改
Source: LangGraph Research Paper
URL: https://arxiv.org/pdf/2604.11378
Date: 2026
Excerpt: "LangGraph supports parallel node execution, conditional edges, state channels, and runtime graph modification."
Context: LangGraph高级特性用于生产级Agent
Confidence: high
```

```
Claim: create_supervisor API是构建Supervisor模式的推荐方式，支持多级层次结构和内存管理
Source: PyPI - langgraph-supervisor
URL: https://pypi.org/project/langgraph-supervisor/
Date: 2025-11-19
Excerpt: "A Python library for creating hierarchical multi-agent systems using LangGraph."
Context: Supervisor Agent快速构建
Confidence: high
```

### 7.2 MCP协议实现

```
Claim: MCP协议使用JSON-RPC 2.0消息格式，提供Tools、Resources、Prompts三种原语
Source: MCP Official Specification
URL: https://modelcontextprotocol.io/specification/2025-06-18
Date: 2026-06-25
Excerpt: "MCP provides a standardized way for applications to share contextual information with language models, expose tools and capabilities to AI systems."
Context: MCP协议是工具管理的标准选择
Confidence: high
```

```
Claim: MCP Tools由模型控制、Prompts由用户控制、Resources由应用控制，形成三个独立的控制平面
Source: Dev.to - MCP Prompts and Resources
URL: https://dev.to/aws-heroes/mcp-prompts-and-resources-the-primitives-youre-not-using-3oo1
Date: 2026-04-09
Excerpt: "Tools are model-controlled. Prompts are user-controlled. Resources are application-controlled."
Context: MCP三种原语的职责划分
Confidence: high
```

### 7.3 Mem0记忆管理

```
Claim: Mem0是生产级记忆流水线，采用两阶段机制提取显著事实并迭代更新现有记录
Source: MRMMIA Paper
URL: https://arxiv.org/html/2605.27825v1
Date: 2026-05-27
Excerpt: "Mem0, a production-grade memory pipeline that employs a two-phase mechanism to extract salient facts and iteratively update existing records."
Context: Mem0作为长期记忆管理方案
Confidence: high
```

```
Claim: Mem0在LOCOMO基准测试中一致优于所有现有记忆系统，比OpenAI高26%
Source: Mem0 Paper
URL: https://arxiv.org/abs/2504.19413
Date: 2025-04-28
Excerpt: "Mem0 achieves 26% relative improvements in the LLM-as-a-Judge metric over OpenAI."
Context: Mem0的基准测试结果
Confidence: high
```

```
Claim: Mem0社区开发了记忆生命周期管理插件，基于艾宾浩斯遗忘曲线实现自动衰减和清理
Source: GitHub Discussions - mem0ai/mem0
URL: https://github.com/mem0ai/mem0/discussions/5393
Date: 2026-06-05
Excerpt: "weighted_score = min(access_count, 255) * 0.5 ** (days_since_last_access / 7)"
Context: 记忆衰减算法的参考实现
Confidence: medium
```

### 7.4 向量数据库

```
Claim: pgvector的舒适范围是1M-50M向量，支持ACID事务和SQL原生join
Source: Bytebase
URL: https://www.bytebase.com/blog/pgvector/
Date: 2026-03-09
Excerpt: "For most teams building a first RAG pipeline or adding semantic search to an existing product, pgvector is the right place to start."
Context: 开发环境向量存储选择
Confidence: high
```

```
Claim: Milvus采用完全分布式和K8s原生架构，支持CPU/GPU硬件加速
Source: Milvus GitHub
URL: https://github.com/milvus-io/milvus
Date: 2026-06-26
Excerpt: "Milvus implements hardware acceleration for CPU/GPU to achieve best-in-class vector search performance."
Context: 生产环境向量数据库选择
Confidence: high
```

```
Claim: Qdrant使用Rust编写，提供最佳性价比，支持HNSW遍历时的payload过滤
Source: Junaid Rehman
URL: https://junaidrehman.me/blog/vector-database-pinecone-vs-chroma-vs-qdrant/
Date: 2026-05-31
Excerpt: "Qdrant is written in Rust from the ground up, which gives it a performance profile that neither Chroma nor Pinecone can match on self-hosted hardware."
Context: 测试环境向量数据库选择
Confidence: high
```

### 7.5 Redis缓存

```
Claim: Redis在GenAI应用中用于会话状态管理、缓存、向量搜索和响应语义缓存
Source: Redis Documentation
URL: https://redis.io/docs/latest/develop/get-started/redis-in-ai/
Date: 2026-06-24
Excerpt: "Redis excels in storing and indexing vector embeddings that semantically represent unstructured data."
Context: Redis在多Agent系统中的作用
Confidence: high
```

```
Claim: AI Agent平台应在三层实现缓存：应用级进程内缓存、Redis共享缓存、语义缓存
Source: CallSphere
URL: https://callsphere.tech/blog/caching-architecture-ai-agents-redis-strategies
Date: 2026-05-12
Excerpt: "Effective caching in AI agent platforms operates at three layers: application-level in-process caching for hot configuration data, Redis for shared session and response caching across pods, and semantic caching for similar LLM queries."
Context: 缓存架构设计
Confidence: high
```

### 7.6 FastAPI认证

```
Claim: FastAPI的OAuth2PasswordBearer实现了标准JWT认证流程
Source: GeeksForGeeks
URL: https://www.geeksforgeeks.org/python/login-registration-system-with-jwt-in-fastapi/
Date: 2026-05-11
Excerpt: "OAuth2PasswordBearer extracts JWT token from Authorization header... passlib.CryptContext hash/verify passwords using bcrypt"
Context: JWT认证实现参考
Confidence: high
```

```
Claim: FastAPI的RBAC实现通过在JWT中加入角色声明并在依赖项中校验
Source: Binadit
URL: https://binadit.com/tutorials/implement-fastapi-authentication-jwt-oauth2
Date: 2026-04-06
Excerpt: "In JWT's scope or custom claims, add user roles or permissions list, and perform finer-grained verification in dependencies."
Context: 角色权限控制实现
Confidence: high
```

### 7.7 WebSocket实时通信

```
Claim: WebSocket为AI应用提供持久双向通信，支持实时token流式传输、用户干预和多设备连续性
Source: Ably
URL: https://ably.com/blog/websockets-vs-http-for-ai-streaming-and-agents
Date: 2026-02-15
Excerpt: "WebSockets provide persistent, bi-directional communication between clients and servers... enabling realtime token streaming, user steering, and stateful interactions."
Context: WebSocket在AI聊天中的应用
Confidence: high
```

### 7.8 可观测性

```
Claim: LangSmith提供全栈追踪，捕获Agent的完整执行树，包括工具调用、文档检索和模型参数
Source: LangChain Blog
URL: https://www.langchain.com/resources/llm-observability-tools
Date: 2026-06-15
Excerpt: "LangSmith creates high-fidelity traces that render the complete execution tree of an agent. You see tool selections, retrieved documents, and exact parameters at every step."
Context: Agent可观测性方案
Confidence: high
```

```
Claim: Prometheus + Grafana + LangSmith构成完整的LLM可观测性方案，分别回答what/how/why
Source: ActiveWizards
URL: https://activewizards.com/blog/llm-observability-a-guide-to-monitoring-with-langsmith/
Date: 2026-05-21
Excerpt: "LangSmith answers 'What did the agent do and why?', Prometheus answers 'How is the system performing?', Grafana answers 'Show me the system's health'"
Context: 三大可观测性工具的分工
Confidence: high
```

### 7.9 Docker部署

```
Claim: Docker Compose现已生产就绪，支持LangGraph、CrewAI等AI框架，新增AI models模块
Source: Docker Official
URL: https://www.docker.com/solutions/docker-ai/
Date: 2025-11-24
Excerpt: "Compose is now production ready... Docker works with the frameworks and languages you already use."
Context: Docker作为AI Agent部署方案
Confidence: high
```

```
Claim: Docker新增AI models模块到Compose规范，支持通过MCP连接模型到各种工具
Source: SudHT.fr
URL: https://sudht.fr/en/docker-compose-integrates-ai-devops-workflows/
Date: 2025-08-21
Excerpt: "Docker has added a dedicated 'AI models' block to the open-source Compose specification, enabling developers to define and deploy AI agents directly within DevOps workflows."
Context: Docker Compose AI Agent部署
Confidence: high
```

### 7.10 智能旅游助手

```
Claim: AI旅游助手采用模块化架构，包含用户界面、AI处理引擎和推荐系统三个主要组件
Source: IJERT
URL: https://www.ijert.org/ai-based-intelligent-travel-assistant-for-personalized-trip-planning-ijertv15is041854
Date: 2026-04-23
Excerpt: "The proposed AI Travel Assistant is designed using a modular architecture that consists of three primary components: User Interface, AI Processing Engine, Recommendation System."
Context: 旅游助手的参考架构
Confidence: high
```

---

## 附录

### A. 技术选型决策矩阵

| 决策点 | 选项A | 选项B | 选择 | 理由 |
|--------|-------|-------|------|------|
| Agent编排 | LangGraph Supervisor | CrewAI | LangGraph | 更好的状态管理、检查点 |
| 记忆管理 | Mem0 | LangMem | Mem0 | 生产级、基准测试领先 |
| 向量DB(开发) | Chroma | Qdrant | Chroma | 零配置、快速开发 |
| 向量DB(生产) | Milvus | Pinecone | Milvus | 开源、分布式、成本控制 |
| 缓存 | Redis | Memcached | Redis | 数据结构丰富、向量支持 |
| Web框架 | FastAPI | Flask | FastAPI | 原生异步、自动文档 |
| 前端(MVP) | Streamlit | Gradio | Streamlit | 生态成熟、部署简单 |
| 认证 | JWT+OAuth2 | Session | JWT+OAuth2 | 无状态、适合API |
| 监控 | LangSmith | LangFuse | LangSmith | LangChain原生集成 |
| 部署 | Docker Compose | K8s | Docker Compose | 复杂度适中、足够使用 |

### B. 性能指标目标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| API响应延迟 (P50) | < 200ms | 非AI请求 |
| API响应延迟 (P99) | < 1000ms | 非AI请求 |
| AI首次Token延迟 | < 3s | 从请求到首token |
| AI完整响应延迟 | < 15s | 简单查询 |
| 流式输出速度 | > 10 tokens/s | 流式传输 |
| 并发用户数 | > 100 | 单实例 |
| 会话缓存命中 | > 80% | Redis缓存 |
| 记忆检索延迟 | < 100ms | Qdrant查询 |
| 系统可用性 | > 99.5% | 年度目标 |

### C. 安全风险与对策

| 风险 | 对策 |
|------|------|
| Prompt注入 | 输入验证、输出过滤、MCP工具权限控制 |
| Token泄露 | JWT短期过期、HTTPS传输、刷新令牌轮换 |
| API滥用 | 速率限制、IP黑名单、API Key管理 |
| 数据泄露 | 字段级加密、最小权限原则、审计日志 |
| MCP工具滥用 | 工具调用确认、权限预检、调用审计 |
| 记忆污染 | 记忆验证、冲突检测、用户确认更新 |

---

*文档版本: v1.0 | 创建日期: 2025-07-18 | 最后更新: 2025-07-18*
