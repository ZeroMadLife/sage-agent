## 4. 技术架构方案

### 4.1 系统总体架构

#### 4.1.1 前后端分离架构

智能旅游助手采用前后端分离的架构模式，后端以FastAPI构建RESTful API服务，前端在MVP阶段使用Streamlit快速验证产品逻辑，生产环境迁移至React实现完整的用户体验。这一分层决策基于技术生态的成熟度与团队开发效率的权衡：FastAPI在简单I/O端点上比Flask快6-8倍（15{,}000-20{,}000 RPS vs 2{,}000-3{,}000 RPS），于2025年12月在GitHub星数上超过Flask [^31^]，其原生异步支持能够高效处理多Agent系统中大量的LLM API并发调用。FastAPI的设计哲学——API-first、类型驱动开发——完全兼容OAuth 2.0和OpenAPI，使用Pydantic进行请求验证和类型检查 [^31^]，这使Agent系统的接口契约在开发阶段即获得自动生成的API文档和严格的输入校验。

前端技术栈的分阶段选型遵循"验证优先、体验跟进"的原则。Streamlit适合在数小时内搭建交互式Demo界面，且与Python后端深度集成无需额外配置 [^32^]；Gradio虽提供预建聊天UI组件 [^33^]，但Streamlit的生态和社区资源更为丰富。MVP阶段的核心目标是验证Agent协作逻辑和用户需求匹配度，Streamlit的session_state和原生组件足以支撑对话界面、行程展示和预算图表等基础功能。当产品进入生产阶段，前端迁移至React 18配合Tailwind CSS实现响应式布局 [^34^]，通过Zustand管理全局对话状态，WebSocket原生支持实现AI回复的流式传输（token streaming），React Router v6管理多页面路由。生产级架构中，FastAPI后端配合React前端是学术研究和工业实践验证的标准方案 [^34^]。

#### 4.1.2 六层架构设计

系统整体采用六层纵向架构，每一层承担独立的职责边界，层间通过明确定义的接口协议通信。以下架构图描述了从用户交互到基础设施的完整数据流：

**架构图一：六层系统架构**
```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ① 前端层 (Presentation)                                                   │
│  Streamlit(MVP) / React(生产) → HTTPS/WSS →                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  ② API网关层 (Gateway)                                                     │
│  Nginx反向代理 → SSL终止 / 速率限制 / 负载均衡 / 静态文件服务                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│  ③ Agent编排层 (Orchestration)                                             │
│  FastAPI + LangGraph StateGraph → Supervisor → 4个专业Agent                 │
│  (规划Agent / 推荐Agent / 预算Agent / 信息Agent)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓ JSON-RPC 2.0
┌─────────────────────────────────────────────────────────────────────────────┐
│  ④ Skills管理层 (Skills Management)                                        │
│  MultiServerMCPClient → MCP Server集群                                     │
│  (高德地图MCP / 天气MCP / 景点信息MCP / 交通MCP)                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓ SQL/Vector Protocol
┌─────────────────────────────────────────────────────────────────────────────┐
│  ⑤ 数据持久层 (Data Persistence)                                           │
│  PostgreSQL(结构化) + Redis(缓存) + Mem0/Qdrant(向量/记忆)                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓ Metrics/Logs
┌─────────────────────────────────────────────────────────────────────────────┐
│  ⑥ 基础设施层 (Infrastructure)                                             │
│  Docker容器化 + 阿里云ECS + Prometheus/Grafana + Loki日志                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

六层架构的层间通信遵循严格的协议规范。前端至API网关使用HTTPS REST和WebSocket传输JSON数据，认证采用JWT Bearer令牌；API网关至Agent编排层通过Python内部调用传递TypedDict类型化的状态对象；Agent编排层至Skills管理层使用MCP协议的JSON-RPC 2.0消息格式 [^1^]；Skills管理层至外部数据源通过HTTPS REST调用第三方API（高德地图、和风天气等）；Agent编排层至数据持久层通过Python SDK和SQL/TCP协议交互；全系统的可观测性数据（指标、追踪、日志）通过OpenTelemetry协议和HTTP推送至基础设施层的监控组件。

#### 4.1.3 核心组件关系图

Agent编排层是系统的技术核心，其组件关系体现了Supervisor模式的多Agent协作拓扑。LangGraph官方定义了三种多Agent拓扑结构：Network（每个Agent可调用每个Agent）、Supervisor（一个Supervisor路由到N个Worker）和Hierarchical（Supervisor的Supervisor）[^7^]。对于智能旅游助手这一包含4个专业Agent的场景，Supervisor模式被确定为最优选择——它适合约90%的真实团队场景，一个编排器加多个专家，当专家超过8个时才值得考虑Hierarchical模式 [^7^]。

**架构图二：核心组件关系图**
```
                          ┌──────────────┐
                          │   用户请求    │
                          └──────┬───────┘
                                 ↓
                    ┌────────────────────────┐
                    │   FastAPI API Router    │
                    │  /api/v1/chat (WebSocket)│
                    └───────────┬────────────┘
                                ↓
                    ┌────────────────────────┐
                    │  LangGraph StateGraph   │
                    │  TravelState (TypedDict)│
                    └───────────┬────────────┘
                                ↓
                    ┌────────────────────────┐
                    │    Supervisor Agent     │
                    │  (意图识别/任务分解/调度) │
                    │      temperature=0      │
                    └──────┬────────┬────────┘
                           │ 扇出    │ 扇入
              ┌────────────┼────────┼────────────┐
              ↓            ↓        ↓            ↓
        ┌─────────┐  ┌────────┐ ┌────────┐  ┌────────┐
        │ Planning│  │Recommend│ │ Budget │  │  Info  │
        │  Agent  │  │ Agent  │ │ Agent  │  │ Agent  │
        └────┬────┘  └────┬───┘ └───┬────┘  └───┬────┘
             │            │         │           │
             └────────────┴────┬────┴───────────┘
                               ↓
                    ┌────────────────────────┐
                    │  MultiServerMCPClient   │
                    │   (MCP Client 网关)      │
                    └──────┬────────┬─────────┘
                           │        │
              ┌────────────┘        └────────────┐
              ↓                                  ↓
    ┌──────────────────┐              ┌──────────────────┐
    │   MCP Server集群  │              │   Mem0记忆系统    │
    │ (高德/天气/景点)  │              │ (长期记忆存储)    │
    └──────────────────┘              └──────────────────┘
```

Supervisor Agent作为中央协调节点，负责接收用户输入、识别意图、将任务分解并路由至最合适的专业Agent，最终聚合各Agent的输出结果生成完整回复。每个专业Agent通过`create_react_agent`工厂函数创建，拥有独立的工具集和系统提示词 [^7^]。规划Agent负责生成和优化旅游行程，推荐Agent负责景点、餐厅、酒店的个性化推荐，预算Agent负责预算分配与花费追踪，信息Agent负责天气、交通、景点开放时间的实时查询。4个Agent节点之间通过共享的TravelState对象实现状态同步，Supervisor通过条件边（Conditional Edge）根据当前状态决定执行路径 [^8^]。

组件间的数据流遵循请求-响应模式：用户请求经FastAPI路由进入LangGraph图，Supervisor节点首先执行意图识别，将用户输入分类为"行程规划"、"景点推荐"、"预算管理"或"信息查询"等意图类型，然后通过条件边将控制流路由至对应的专业Agent。专业Agent在执行过程中通过MCP Client调用外部工具获取数据，更新State中的中间结果，执行完毕后返回Supervisor进行结果聚合。Supervisor模式相比单Agent有约3倍token成本，但成功率提升18个百分点（从71%提升至89%），对于旅游规划这类高价值任务，这一成本增加是可接受的 [^7^]。

**表1：核心技术组件选型表**

| 层次 | 组件 | 选型方案 | 备选方案 | 选型依据 |
|:---|:---|:---|:---|:---|
| 前端层 | UI框架 | Streamlit (MVP) → React (生产) | Gradio, Vue | Streamlit快速验证，React生产级体验 [^33^][^34^] |
| API网关层 | 反向代理 | Nginx | Traefik, Caddy | 成熟稳定、SSL/Lua生态丰富 |
| Agent编排层 | 编排框架 | LangGraph StateGraph | CrewAI, AutoGen | Supervisor模式原生支持、Checkpoint持久化 [^7^] |
| Agent运行时 | LLM SDK | LangChain | LlamaIndex | 生态系统最完善、60%开发者首选 [^35^] |
| Skills管理层 | 协议标准 | MCP (Model Context Protocol) | Function Calling | AI界的USB-C标准、7{,}400+ Server生态 [^1^] |
| 数据持久层 | 主数据库 | PostgreSQL + pgvector | MongoDB, MySQL | 关系型+向量统一存储、HNSW索引支持 [^36^] |
| 数据持久层 | 缓存层 | Redis | Valkey, Memcached | 亚毫秒级延迟、TTL自动过期、向量搜索 [^37^] |
| 数据持久层 | 记忆系统 | Mem0 + Qdrant | LangMem, Letta | 支持20+向量后端、91%延迟降低 [^38^][^39^] |
| 基础设施层 | 容器化 | Docker + Docker Compose | Kubernetes | 开发简单、学生项目资源适配 [^40^] |
| 基础设施层 | 云平台 | 阿里云ECS (学生免费) | 腾讯云, 华为云 | t6 2核2G免费+轻量68元/年 [^41^] |
| 可观测性 | LLM追踪 | LangSmith | Langfuse, Arize | LangGraph原生集成、Agent树可视化 [^42^] |
| 可观测性 | 指标监控 | Prometheus + Grafana | Datadog, New Relic | K8s生态标准、开源免费 [^43^] |

上表覆盖了系统从用户界面到基础设施的12个关键技术组件。选型决策综合了生态成熟度、性能指标、运维成本和学习曲线四个维度。其中LangGraph作为Agent编排层的核心框架，其选择基于官方基准测试的量化数据：Supervisor + 4个专家的模式每次任务消耗约11{,}400 tokens，成本约$0.061，端到端成功率89% [^7^]。对于学生项目而言，阿里云学生免费ECS（t6 2核2G 1M带宽 40G云盘）配合轻量应用服务器68元/年的配置 [^41^]，在Docker Compose单节点部署模式下足以支撑完整的多Agent系统运行。

### 4.2 Agent编排层实现

#### 4.2.1 StateGraph设计

LangGraph的StateGraph是Agent编排的中央抽象，它是一种有向状态图，其中Node是计算单元（LLM调用、工具调用、Python函数），接收当前状态并返回增量更新；Conditional Edge通过状态谓词评估来定义执行流 [^8^]。状态以类型化数据结构（TypedDict或Pydantic模型）沿边传播，LangGraph通过reducer函数将节点输出合并回全局状态。

智能旅游助手的全局状态`TravelState`采用TypedDict定义，包含以下核心字段：

| 字段名 | 类型 | 作用域 | 说明 |
|:---|:---|:---|:---|
| `messages` | `Annotated[list, add_messages]` | 全局 | 消息历史，使用add_messages reducer合并 |
| `user_id` | `str` | 全局 | 用户唯一标识 |
| `session_id` | `str` | 全局 | 会话唯一标识 |
| `intent` | `str` | Supervisor | 识别出的用户意图 |
| `destination` | `str` | 全局 | 旅游目的地 |
| `budget` | `dict` | 全局 | 预算信息（总额/货币/已分配） |
| `dates` | `dict` | 全局 | 出行日期范围 |
| `itinerary` | `dict` | Planning Agent | 行程规划结果 |
| `recommendations` | `list` | Recommend Agent | 推荐列表 |
| `weather_info` | `dict` | Info Agent | 天气查询结果 |
| `memory_facts` | `list` | Memory | 检索到的用户记忆事实 |
| `final_response` | `str` | Supervisor | 最终聚合回复 |
| `metadata` | `dict` | 全局 | 执行元数据（token用量/延迟/模型） |

`add_messages` reducer是StateGraph中消息列表正确合并的关键机制——没有它，每个节点都会覆盖消息列表，导致模型在第一次工具调用后"失去记忆" [^44^]。`final_response`字段由Supervisor节点在结果聚合阶段写入，作为图的终止标记。所有Agent共享同一状态对象，但每个专业Agent只读写与其职责相关的字段，遵循"只返回需要修改的状态字段的增量更新"的原则 [^45^]。

#### 4.2.2 Supervisor节点

Supervisor Agent是编排层的中枢，承担四项核心职责：意图识别、任务分解、Agent调度和结果聚合。其设计遵循`langgraph-supervisor`包提供的`create_supervisor()`工厂函数模式 [^7^]。

意图识别模块将用户自然语言输入分类为预定义的意图类型。分类体系包括：行程规划（"帮我规划北京5日游"）、景点推荐（"北京有哪些值得去的景点"）、预算管理（"这个行程总费用多少"）、信息查询（"北京明天天气怎么样"）和组合意图（"规划一个去西安的预算3000元以内的行程"）。Supervisor的temperature参数设为0以确保路由决策的确定性 [^7^]，其系统提示词中必须明确禁止"自己做专家工作"——否则Supervisor会试图直接回答简单问题而不是委托给专业Agent。

任务分解模块处理组合意图，将复杂请求拆分为多个子任务。例如"帮我规划去云南的7天行程，预算5000元，喜欢自然风光"会被分解为：①获取云南主要自然景点信息（信息Agent）→ ②生成候选行程方案（规划Agent）→ ③进行预算分配和校验（预算Agent）→ ④根据偏好调整推荐（推荐Agent）。分解后的子任务通过图的Conditional Edge依次或并行执行。

调度决策通过`output_mode="last_message"`配置保持上下文窗口可控 [^7^]。Supervisor的提示词中必须包含明确的防御性指令：要求Worker在收到超出能力范围的请求时转交回Supervisor重新分配，防止Agent误处理不属于其专业领域的问题。

#### 4.2.3 专业Agent节点

4个专业Agent节点通过`create_react_agent`创建，每个Agent拥有独立的工具集和系统提示词。专业Agent的核心设计原则是将领域知识编码在系统提示词中，让LLM在ReAct循环（Reason→Act→Observe→Repeat）中自主决定工具调用序列。

**规划Agent（Planning Agent）** 的系统提示词编码了旅游规划的专业规则：每天安排3-5个主要景点避免过于紧凑，考虑景点间的距离和交通时间，预留休息和用餐时间，根据用户偏好（文化/自然/美食/购物）调整权重，考虑季节和天气因素。其工具集包括`search_destinations`、`create_itinerary`、`optimize_route`和`check_opening_hours`，通过MCP Server调用高德地图API获取地理位置和路线数据。

**推荐Agent（Recommend Agent）** 实现个性化推荐算法：首先从Mem0获取用户历史偏好，然后进行向量语义搜索检索相似目的地和类别的推荐候选，最后使用混合排序融合语义相似度、协同过滤分数和流行度权重生成Top-10推荐列表 [^39^]。

**预算Agent（Budget Agent）** 负责预算分配、性价比计算和实时花费追踪，将经济约束作为规划的第一性原理。其核心能力包括：按类别（交通/住宿/餐饮/门票/购物）自动分配预算、计算每项推荐的性价比评分、追踪已确认行程的累计花费与预算的偏差。

**信息Agent（Info Agent）** 执行并行工具调用获取实时数据。LangGraph原生支持并行扇出（fan-out）模式——从Supervisor发出多条边，多个Worker同时运行 [^46^]。当用户查询"北京明天天气和故宫开放时间"时，信息Agent并行调用天气MCP和景点MCP，两个查询同时执行后将结果汇总至State。

#### 4.2.4 边（Edge）设计

LangGraph的边分为静态Edge和条件Edge（Conditional Edge）。静态Edge固定连接两个节点，条件Edge通过路由函数在运行时决定下一个执行节点 [^8^]。路由函数必须是同步函数，因为它需要立即确定图拓扑——如果需要在路由决策中进行API调用，应该在节点中完成并将结果存入状态，然后由路由函数同步读取 [^45^]。

Supervisor节点的条件边实现意图路由：当`intent`字段为"planning"时路由至Planning Agent，"recommendation"时路由至Recommend Agent，"budget"时路由至Budget Agent，"information"时路由至Info Agent。当所有子任务完成且`final_response`字段被填充时，路由至`END`节点终止图执行。

循环反馈边用于处理需要迭代优化的场景。例如行程规划完成后，预算Agent发现总费用超出预算，通过条件边将控制流返回规划Agent进行重新优化。这种循环结构必须设置硬性的迭代上限（如`iteration >= 10 → END`），防止模型在不良停止条件下永远循环 [^44^]。

人工介入点通过LangGraph的`interrupt()`原语实现 [^47^]。在工具执行前需要人工批准的场景（如调用预订API产生费用），`interrupt(value)`在节点内部调用引发可恢复异常暂停图执行，向用户展示待执行的工具调用详情并提供approve/reject/edit三个选项。用户通过`Command(resume=...)`恢复执行后，payload成为`interrupt()`在节点内的返回值，图从checkpoint自动恢复继续执行 [^47^]。

### 4.3 Skills管理层实现

#### 4.3.1 自定义MCP Server开发

MCP（Model Context Protocol，模型上下文协议）是由Anthropic于2024年11月发布、2025年12月捐赠给Linux Foundation的开放协议标准，它为AI应用连接外部数据源和工具提供标准化方式，被业界称为"AI界的USB-C" [^1^]。MCP协议采用客户端-服务器架构，使用JSON-RPC 2.0消息格式进行通信，核心消息类型包括发现阶段的schema声明、调用阶段的请求/响应以及流式进度事件（0-100%）[^1^]。

智能旅游助手需要开发3个自定义MCP Server，每个Server封装一类外部数据源的工具调用：

**高德地图MCP Server** 封装高德地图Web服务API（日调用上限30万次），暴露以下工具：`geocode`（地址解析为经纬度）、`search_attractions`（POI搜索，支持关键词、类别、城市过滤）、`get_route`（驾车/公交/步行路线规划，返回距离、预计时间和交通方式）、`get_place_detail`（POI详情查询，包含开放时间、评分、价格）。每个工具通过JSON Schema定义输入参数和返回结构，使LLM能够自主理解工具用途并构造正确调用。

**天气MCP Server** 封装和风天气API（月调用上限5万次），暴露工具：`get_weather`（指定城市和日期的天气预报，返回温度、湿度、风力、天气状况）、`get_weather_alert`（极端天气预警查询）。工具描述中嵌入使用场景说明（如"在规划户外活动前调用此工具检查天气"），这是模型理解工具的主要上下文 [^48^]。

**景点信息MCP Server** 整合多源景点数据，暴露工具：`search_scenic_spots`（按目的地和类别搜索景点）、`get_scenic_detail`（景点详细信息包括历史背景、游览建议、最佳游览时间）、`get_ticket_price`（门票价格查询）、`get_opening_hours`（开放时间查询）。

MCP Server的开发遵循Outcome-oriented Design原则——按最终业务结果设计工具，而非单个技术操作 [^48^]。例如，不是暴露"调用高德POI搜索API"这样的底层操作，而是设计"搜索某城市某类别的旅游景点并返回Top-N结果"这样的业务级工具。Server实现使用Python MCP SDK的`FastMCP`类，通过`@tool`装饰器快速定义工具，通常不到200行代码即可完成一个REST API的MCP封装 [^1^]。

#### 4.3.2 MCP Client集成

LangGraph通过`langchain-mcp-adapters`包与MCP集成，提供`MultiServerMCPClient`类实现多服务器连接 [^49^]。集成流程包括：定义服务器配置（支持stdio和HTTP传输）、初始化客户端并连接到所有配置的Server、从Server加载工具列表、将工具注入Agent的ReAct循环。

多服务器连接时的关键工程问题是工具命名空间管理。每个MCP Server需要独立的Client实例，使用`{server_name}__{tool_name}`的前缀命名空间避免工具名冲突 [^50^]。例如高德地图Server的`get_route`工具在Agent上下文中呈现为`amap__get_route`，天气Server的同名工具呈现为`weather__get_route`。这种前缀隔离确保LLM在工具选择时能够明确区分不同Server提供的同类功能。

MCP Client在应用启动时建立所有Server连接，通过`tools/list`端点获取完整的工具schema并缓存。工具调用时通过`tools/call`端点发送请求，调用结果以JSON格式返回并注入Agent的消息历史。MCP支持的工具发现通过自描述元数据实现——Server使用完整的参数名称、类型、描述和返回schema广告每个工具 [^1^]，LLM在做出选择前"看到"每个可用函数及其精确签名。

#### 4.3.3 Skills注册与发现

Skills管理系统采用JSON配置注册和动态加载机制。每个MCP Server的配置在`mcp_servers.json`中声明：

```json
{
  "amap_mcp": {
    "command": "python",
    "args": ["-m", "mcp_servers.amap_server"],
    "env": {"AMAP_API_KEY": "${AMAP_API_KEY}"}
  },
  "weather_mcp": {
    "command": "python",
    "args": ["-m", "mcp_servers.weather_server"],
    "env": {"WEATHER_API_KEY": "${WEATHER_API_KEY}"}
  }
}
```

系统启动时读取配置，为每个Server创建独立的MCP连接。Health Check机制每分钟对每个MCP Server执行`tools/list`探测，检测连接状态和工具可用性。当Server连续3次Health Check失败时标记为不可用，Agent的工具列表中自动排除该Server的工具，实现 graceful degradation——某个数据源不可用时系统仍能通过其他数据源提供服务 [^51^]。

Skills的动态发现通过MCP的`tools/list_changed`通知实现 [^52^]。当Server的工具定义发生变更（如新增工具或修改参数schema）时，主动向Client发送变更通知，Client重新拉取工具列表更新缓存。这一机制确保Agent始终使用最新的工具定义，避免由于工具schema变更导致的调用失败。

### 4.4 记忆管理层实现

#### 4.4.1 短期记忆

短期记忆（Short-Term Memory，STM）负责维护当前会话的对话上下文和临时状态。系统采用Redis作为短期记忆存储，基于以下技术优势：亚毫秒级延迟（<1ms）、TTL自动过期、支持分布式部署和向量搜索能力 [^37^]。Redis Agent Memory采用双层记忆模型——Session Memory（短期记忆/工作记忆）配合可配置的TTL控制，适合多实例部署中的分布式会话管理 [^37^]。

会话隔离通过Redis的key前缀实现：`session:{session_id}`作为namespace，每个会话存储当前对话轮次、已确认的行程参数（目的地/日期/预算/ travelers）和Agent执行中间结果。TTL策略设置为30分钟无活动自动过期，活跃会话每次交互后刷新TTL。滑动窗口管理采用消息对计数策略——当对话轮次超过maxTurns（默认20轮）时，丢弃最旧的消息对，始终保留系统提示词和第一条用户消息 [^53^]。

LangGraph的checkpointer提供线程级持久化，使用thread_id作为作用域 [^54^]。开发阶段使用`MemorySaver`，生产阶段使用`RedisSaver`或`PostgresSaver` [^44^]。Redis checkpoint的性能数据显示，Get checkpoint操作达到2{,}950 ops/sec，虽低于内存（8{,}392 ops/sec）和SQLite（7{,}083 ops/sec），但提供了跨容器的持久化能力，适合分布式部署场景 [^55^]。

#### 4.4.2 长期记忆

长期记忆（Long-Term Memory，LTM）负责跨会话存储用户偏好、历史行程和个人事实，使Agent能够"记住"用户并在多次对话中提供个性化服务。系统采用Mem0作为长期记忆框架，其在LOCOMO基准测试中比OpenAI Assistants记忆提升26%的相对表现，在p95延迟上降低91%，token成本节省超过90% [^38^]。

Mem0采用提取-更新流水线（Extraction-Update Pipeline）的两阶段设计 [^56^]。提取阶段结合全局对话摘要和近期消息窗口，由LLM从对话中提取候选记忆事实（如"用户喜欢历史文化景点"、"用户对海鲜过敏"）；更新阶段通过向量相似度检索Top-K个相似记忆，由LLM决定执行ADD（新增）、UPDATE（更新）、DELETE（删除）或NOOP（无操作）。这一设计自动过滤防止记忆膨胀，通过衰减机制移除不相关信息 [^39^]。

Mem0支持20+向量存储后端，切换后端仅需配置变更而无需代码改动 [^39^]。开发环境使用Chroma（零配置、嵌入式），生产环境迁移至Qdrant（Rust引擎、HNSW索引、在多个基准测试中表现最快）[^57^]或Milvus（超大规模十亿级向量支持）[^58^]。Mem0与LangGraph的集成模式是保留LangGraph checkpointer用于线程内状态，通过`mem0.search()`和`mem0.add()`在每个LLM调用前后添加长期记忆层，所有Mem0调用包裹try/except实现优雅降级 [^59^]。

#### 4.4.3 上下文压缩

上下文压缩解决LLM上下文窗口有限与对话历史持续增长之间的矛盾。系统采用三层压缩策略组合：锚定摘要、BM25+向量混合检索和结构化蒸馏。

**锚定摘要**（Anchored Summaries）策略维护一个持久化的滚动摘要，压缩时仅摘要新掉落的段落并合并到已有摘要中，避免了朴素方法中每次请求对完整历史重新摘要的开销 [^60^]。Factory.ai的评估表明，锚定摘要在长对话场景下将成本线性增长问题转化为近似常数成本。

**BM25+向量混合检索**用于从长期记忆中检索相关记忆条目。BM25关键词搜索在精确匹配、短查询和技术术语方面优于纯向量搜索，向量语义搜索在同义改写和潜在意图理解方面更强 [^61^]。系统采用30%-40% BM25 + 60%-70%向量相似度的加权融合策略，通过RRF（Reciprocal Rank Fusion）合并两种检索结果，在LangChain的`EnsembleRetriever`中实现。

**结构化蒸馏**用于行程规划和预算讨论的结果压缩，将对话内容提取为结构化schema（Decision、Owner、Rationale、Open Questions、References），实现20x-30x压缩比且信息保真度不降反升 [^62^]。这种压缩方式特别适合旅游场景——将冗长的讨论压缩为结构化的决策记录，既节省token又便于后续检索。

### 4.5 数据持久层设计

#### 4.5.1 数据库设计

PostgreSQL作为主数据库存储结构化业务数据，pgvector扩展提供向量存储能力，实现统一存储避免维护两个数据库的运维复杂度 [^36^]。系统定义5张核心表：

**users表**存储用户基本信息和偏好设置。字段包括id（UUID主键）、username（唯一）、email（唯一）、hashed_password（bcrypt）、full_name、phone、preferences（JSONB存储兴趣标签、预算等级、旅行风格等）、is_active、is_verified、role（user/admin）、created_at和last_login_at。preferences字段的JSONB类型允许灵活存储和查询用户偏好，配合GIN索引实现高效的模式匹配。

**sessions表**管理用户对话会话。字段包括id（UUID主键）、user_id（外键）、title（会话标题）、status（active/archived/deleted）、destination（当前会话的目的地）、dates（JSONB，出行日期范围）、travelers（JSONB，人数配置）、budget（JSONB，预算配置）、thread_id（LangGraph线程标识）、created_at和updated_at。thread_id字段将数据库会话与LangGraph的checkpoint线程关联，实现跨请求的图状态持久化。

**messages表**记录对话历史。字段包括id（UUID主键）、session_id（外键，级联删除）、role（user/assistant/system/tool）、content（TEXT）、tool_calls（JSONB，工具调用详情）、tokens_used、latency_ms、model（使用的LLM模型）、created_at。工具调用消息记录Agent调用的工具名称、参数和返回结果，用于调试和审计。

**itineraries表**存储生成的行程方案。字段包括id（UUID主键）、user_id（外键）、session_id（外键，可为空）、title、destination、start_date、end_date、duration_days、content（JSONB，完整的行程结构，包含每日活动、用餐、交通、住宿、费用明细）、status（draft/confirmed/completed/cancelled）、view_count、like_count、is_public、created_at和updated_at。content字段的JSONB结构设计支持嵌套的行程数据模型，无需额外的关联表即可表达复杂的多日行程。

**memories表**作为Mem0的本地备份和审计日志。字段包括id（UUID主键）、user_id、memory_text（记忆文本内容）、memory_type（preference/fact/event）、category（兴趣/禁忌/习惯等）、vector_embedding（pgvector向量）、confidence（置信度分数0-1）、source（记忆来源对话ID）、created_at和updated_at。该表在Mem0不可用时提供降级查询能力。

#### 4.5.2 向量数据库

向量数据库负责存储景点embedding、用户记忆向量等语义数据。开发环境使用Chroma（零配置、嵌入式运行），生产环境迁移至Qdrant或Milvus [^57^][^58^]。

**景点向量索引Collection**的schema设计：向量维度768（使用BAAI/bge-large-zh-v1.5中文embedding模型），距离度量采用Cosine相似度，HNSW索引参数M=16、efConstruct=200。每个向量点携带payload元数据：name（景点名称）、destination（目的地）、category（类别标签数组）、description（描述文本）、rating（评分）、price_level（价格等级）、location（经纬度JSON）。payload过滤支持带地理位置和类别条件的语义搜索，例如"搜索杭州评分4.5以上且类别为'自然'的景点中与'山水风光'语义相似的Top-10"。

**记忆向量Collection**的schema设计：向量维度768，Cosine距离，为每个用户维护独立的memory命名空间。Mem0的检索机制使用密集向量相似度搜索，检索到的项目是"不断演化的、经过策展的记忆状态"而非静态日志 [^56^]。

pgvector的HNSW索引构建是CPU密集型操作，生产环境应使用`CREATE INDEX CONCURRENTLY`避免锁表 [^63^]。pgvector在1{,}000万向量以下规模表现良好 [^36^]，配合pgvectorscale扩展的StreamingDiskANN可支持更大规模的工作负载。

#### 4.5.3 缓存策略

Redis三层缓存架构覆盖不同类型的热点数据：

**第一层：热点数据缓存**存储高频访问且不常变更的数据，如热门景点列表、城市列表、景点类别映射。TTL设置为1小时，使用allkeys-lru驱逐策略 [^64^]。

**第二层：会话状态缓存**存储活跃会话的LangGraph状态快照，支持快速恢复对话上下文。TTL与会话TTL同步（30分钟），使用Redis JSON模块存储结构化的状态对象。

**第三层：API响应缓存**缓存外部API的响应结果，减少重复调用成本和延迟。高德地图POI搜索响应缓存15分钟，天气数据缓存1小时（天气数据更新频率低），交通路线缓存30分钟。每个缓存key包含请求参数的哈希值，确保相同参数的请求命中缓存。

缓存一致性采用Cache-Aside模式：读取时先查缓存未命中再查数据库并回填缓存；写入时先更新数据库再删除缓存。Redis的惰性删除+主动定期删除组合策略每秒10次随机采样20个key，若超过25%过期则重复，经验证该参数在内存和CPU间取得最优平衡 [^64^]。

### 4.6 基础设施与部署

#### 4.6.1 容器化

系统采用Docker多阶段构建优化镜像体积。构建阶段使用`python:3.12-slim`作为基础镜像安装编译依赖（如C扩展），运行阶段仅复制构建产物和必要的运行时依赖。多阶段构建可将Python应用镜像从259MB减少至156MB（体积缩减约40%）[^65^]。安全最佳实践包括：以非root用户运行容器、不在生产容器中使用包管理器、不在容器中存储密钥（通过环境变量或Docker Secrets注入）[^66^]。

Docker Compose编排定义以下服务：fastapi（后端API服务，端口8000）、streamlit（前端MVP，端口8501）、postgres（PostgreSQL 16+pgvector，端口5432）、redis（Redis 7，端口6379）、qdrant（向量数据库，端口6333）、nginx（反向代理，端口80/443）。各服务通过自定义Docker网络通信，使用命名卷持久化PostgreSQL和Qdrant数据。生产环境checklist包括：所有密钥使用文件或vault管理、Health Check配置、资源限制（CPU/内存）、重启策略`unless-stopped` [^40^]。

#### 4.6.2 云平台选型

部署平台选择阿里云，基于学生优惠的最大化利用。阿里云学生认证用户可免费领取1台ECS云服务器（t6 2核2G 1M带宽 40G云盘），完成实验任务后可0元续费6个月 [^41^]。该配置在Docker Compose单节点部署模式下足以运行完整的多Agent系统。若需更高带宽，轻量应用服务器2核2G3M配置仅需68元/年 [^41^]。

**表2：基础设施与部署技术选型表**

| 维度 | 组件 | 开发环境 | 生产环境 | 成本估算 |
|:---|:---|:---|:---|:---|
| 容器化 | 构建工具 | Docker Desktop | Docker CE | 免费 |
| 编排 | 容器编排 | Docker Compose | Docker Compose / K3s | 免费 |
| 计算 | 云服务器 | 本地开发机 | 阿里云ECS t6 2核2G | 学生免费 [^41^] |
| 网络 | 反向代理 | Nginx (本地) | Nginx + SSL证书 | 免费(Let's Encrypt) |
| 数据库 | 主数据库 | PostgreSQL 16 (Docker) | PostgreSQL 16 + pgvector | 包含在ECS中 |
| 缓存 | KV存储 | Redis 7 (Docker) | Redis 7 (Docker) | 包含在ECS中 |
| 向量库 | 向量数据库 | Chroma (嵌入式) | Qdrant / Milvus | 包含在ECS中 |
| LLM追踪 | Agent监控 | LangSmith (开发版) | LangSmith / Langfuse | 开发版免费 |
| 指标监控 | 基础设施监控 | Prometheus + Grafana (Docker) | Prometheus + Grafana | 免费开源 |
| 日志 | 日志收集 | 本地文件 | Loki + Grafana | 免费开源 |
| CI/CD | 自动化部署 | 手动部署 | GitHub Actions + SSH | 免费 |
| 域名 | DNS | localhost | 阿里云域名 / 免费二级域名 | 约30元/年 |

上表展示了从开发到生产的完整基础设施栈。总成本控制在100元/年以内，其中云服务器通过学生认证免费获取，域名和SSL证书是唯一的必要支出。若项目需要更高可用性，可考虑增加轻量应用服务器（68元/年）作为冗余节点，使用Nginx负载均衡实现简单的双节点部署。

#### 4.6.3 监控可观测性

多Agent系统的监控需要同时覆盖基础设施指标和Agent业务指标。研究表明68%的多Agent系统故障无法通过传统监控方案发现 [^67^]，核心监控指标包括：token用量（按Agent和模型维度聚合）、任务成功率（各Agent完成任务的比率）、工具调用成功率（MCP工具调用的成功/失败比率）、延迟分布（P50/P95/P99响应时间）、对话轮次和对话完成率。

LangSmith为LangGraph提供原生追踪能力，可观察ReAct Agent执行过程中的每个步骤，包括状态流转、节点调用和工具执行 [^42^]。LangSmith在2026年5月推出SmithDB（Rust-based数据层），P50 trace tree加载时间降至92毫秒 [^42^]。每条追踪记录包含完整的执行链路，便于诊断"哪个节点慢、哪个token消耗多"。

Prometheus + Grafana负责基础设施指标监控。Prometheus采集FastAPI服务、Redis、PostgreSQL和Docker容器的指标数据，Grafana提供可视化仪表盘和告警视图 [^43^]。关键告警规则包括：API响应时间P95超过5秒、错误率超过1%、Redis内存使用率超过80%、PostgreSQL连接数超过80%上限。

Loki负责日志收集和分析。相比ELK Stack，Loki的存储成本低约10倍，与Grafana深度集成 [^68^]。结构化日志采用JSON格式，每条日志包含timestamp、level、message、service_name、request_id、agent_name和workflow_id字段 [^69^]。request_id贯穿整个请求链路，实现从API入口到Agent执行到工具调用的全链路追踪关联。

#### 4.6.4 错误处理与容错

多Agent系统的生产失败率高达41%-87%，其中79%的失败归因于协调缺陷（规格歧义、Agent间不一致、验证缺失）而非基础模型能力问题 [^67^]。系统设计4级Fallback策略应对不同层级的故障：

**Level 1：工具级容错**。单个MCP工具调用失败时，系统尝试3次指数退避重试（初始延迟1秒，上限30秒，抖动范围±25%），仍失败后返回默认值或缓存数据 [^70^]。例如天气查询失败时返回"天气信息暂不可用，建议出行前再次确认"。

**Level 2：Agent级容错**。单个Agent执行失败时，Supervisor捕获异常并将错误信息纳入最终回复。例如规划Agent失败后，Supervisor向用户说明"行程规划服务暂时不可用，我可以为您推荐一些热门景点作为参考"，然后调用推荐Agent提供替代方案。

**Level 3：服务级容错**。LLM API服务不可用（如限流或超时）时，系统切换至备用模型供应商。通过LiteLLM代理层实现多供应商路由，在主供应商429或5xx错误时自动切换至备用供应商，典型切换时间15-45秒 [^71^]。模型降级策略在主模型不可用时切换至较小但更快的模型，降级响应时间200-800毫秒 [^71^]。

**Level 4：系统级容错**。全部上游服务不可用时，系统返回缓存的静态响应（如热门景点推荐、常见问题解答），并记录故障日志供后续分析。同时触发告警通知运维人员介入。

超时控制按操作类型分级配置：快速分流（意图识别）<30秒、复杂编排（行程规划）<90秒、文本生成（回复生成）<45秒 [^72^]。熔断器配置为连续3次失败后打开，5分钟后尝试恢复 [^72^]。韧性架构从便宜到昂贵分层构建：错误分类（免费）→ 指数退避重试（时间成本）→ 熔断器（状态管理开销）→ 降级模型（能力成本）→ 人工升级（人力成本）[^71^]。
