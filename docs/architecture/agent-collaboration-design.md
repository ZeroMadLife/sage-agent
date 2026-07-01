# Agent 协作架构确认（基于报告 3.2 节）

> **状态：待确认** — 本文锁定 4-Agent Supervisor 协作设计，作为 Phase 2 开发的依据。
> 确认后冻结，变更需走 ADR。

## 一、架构总览：Supervisor 模式

```
                          ┌──────────────┐
                          │   用户请求    │
                          └──────┬───────┘
                                 ↓
                    ┌────────────────────────┐
                    │   FastAPI API Router    │
                    │  /api/v1/chat (WS流式)  │
                    └───────────┬────────────┘
                                ↓
                    ┌────────────────────────┐
                    │  LangGraph StateGraph   │
                    │  TravelState (TypedDict)│
                    └───────────┬────────────┘
                                ↓
                    ┌────────────────────────┐
                    │    Supervisor Agent     │
                    │  意图识别/任务分解/调度  │
                    │    temperature=0        │
                    └────┬─────────┬─────────┘
                         │ 扇出     │ 扇入
          ┌──────────────┼─────────┼──────────────┐
          ↓              ↓         ↓              ↓
    ┌──────────┐  ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ 规划Agent │  │ 推荐Agent │ │ 预算Agent │ │ 信息Agent │
    │ Planning │  │ Recommend│ │  Budget  │ │   Info   │
    └────┬─────┘  └────┬─────┘ └────┬─────┘ └────┬─────┘
         │             │            │            │
         └─────────────┴─────┬──────┴────────────┘
                               ↓
                    ┌────────────────────────┐
                    │  MultiServerMCPClient   │
                    └──────┬────────┬─────────┘
                           ↓        ↓
              ┌────────────────┐ ┌────────────────┐
              │  MCP Server集群 │ │  Mem0记忆系统   │
              └────────────────┘ └────────────────┘
```

**为什么选 Supervisor 而非 Swarm/Hierarchical：**
- Supervisor 适合 ~90% 真实场景，一个编排器 + 多个专家
- 基准：成功率 89%（vs 单Agent 71%），成本约3倍token，对高价值旅游规划任务可接受
- 旅游场景需要**严格的预算约束全局传递**——"全局状态一致性"比"自主协商"更重要
- 4个专家 < 8个，不需要 Hierarchical（Hierarchical成本是Supervisor的3倍）

---

## 二、四个专业 Agent 设计

### 2.1 规划 Agent（Planning Agent）

| 项 | 内容 |
|----|------|
| **职责** | 根据约束条件生成个性化多日行程方案 |
| **核心算法** | CSP（约束满足问题）+ TSP近似算法（最小化交通时间） |
| **读入State** | destination / dates / budget / 用户偏好 / weather_info |
| **写回State** | itinerary |
| **MCP工具** | search_attractions / get_route / geocode / search_scenic_spots / get_scenic_detail / get_opening_hours |

**执行流程（5步）：**
1. **意图识别** — 判断"完整行程规划"/"单日优化"/"路线调整"
2. **约束解析** — 从自然语言提取时间/预算/偏好/人员构成
3. **候选生成** — 从景点数据库筛选符合条件的景点组合（贪心算法生成初始候选集）
4. **路线优化** — TSP近似算法优化每日景点顺序，目标最小化交通时间
5. **预算校验** — 确保总费用在预算内，超出则触发降配（替换免费景点/调整餐厅档次）

### 2.2 推荐 Agent（Recommend Agent）

| 项 | 内容 |
|----|------|
| **职责** | 景点/餐厅/酒店的个性化推荐 |
| **核心算法** | 三层混合推荐：协同过滤(CF) 40% + 内容推荐(CB) 30% + 知识图谱(KG) 30% |
| **读入State** | destination / 用户偏好 / memory_facts / itinerary（已选景点，避免重复推荐） |
| **写回State** | recommendations |
| **MCP工具** | search_scenic_spots / get_scenic_detail |
| **记忆依赖** | Mem0 检索用户历史偏好（如"喜欢海鲜""对寺庙感兴趣"） |

**关键能力：** 语义检索——用户说"我想看古建筑"，向量相似度搜索映射到相关POI，而非关键词匹配。768维向量（BAAI/bge-large-zh-v1.5）存于 pgvector。

### 2.3 预算 Agent（Budget Agent）★核心差异化

| 项 | 内容 |
|----|------|
| **职责** | 预算分配、性价比计算、实时花费追踪、超支预警 |
| **核心理念** | **"预算作为输入"（Budget as Input）** — 行程生成阶段即按预算约束筛选，而非事后统计 |
| **读入State** | budget / itinerary |
| **写回State** | budget（更新分配与已花费） + 超支标记 |
| **MCP工具** | get_ticket_price（计算门票花费） |
| **外部依赖** | 无（纯算法，零外部API依赖，MVP最可控的模块） |

**预算分配比例（默认，用户可调）：**

| 类别 | 比例 | 说明 |
|------|------|------|
| 交通 | 30% | 往返+市内 |
| 住宿 | 25% | 酒店/青旅 |
| 餐饮 | 25% | 每日三餐 |
| 门票与活动 | 15% | 景点门票 |
| 机动 | 5% | 应急储备 |

**核心机制：**
- 性价比排序：`体验评分 / 价格` 为核心指标，保证不低于最低体验阈值
- 跨类别调剂：某项超支时自动从其他类别借调（如减少住宿增加餐饮）
- 实时仪表盘：行程页展示分项花费占比，超支变警示色 + 推送降配建议

### 2.4 信息 Agent（Info Agent）

| 项 | 内容 |
|----|------|
| **职责** | 天气/交通/景点信息实时聚合 + 异常预警 |
| **核心能力** | **并行扇出（fan-out）** — 同时发起多个外部调用 |
| **读入State** | destination / dates / itinerary |
| **写回State** | weather_info / 预警标记 |
| **MCP工具** | get_weather / get_forecast / get_weather_alert / get_route |

**异常预警能力（从"被动回答"升级为"主动代理"）：**
- 检测到目的地暴雨/高温 → 行程中插入预警提示
- 检测到景点临时闭馆 → 推荐替代方案
- 检测到交通拥堵 → 建议调整出发时间

---

## 三、协作流程（端到端）

以"周末去杭州2日游预算500元喜欢美食"为例：

```
Step 1: 用户输入 → FastAPI → StateGraph
Step 2: Supervisor 意图识别 → 组合意图（需规划+推荐+预算+信息四个Agent）
Step 3: Supervisor 任务分解 + 并行扇出
        ├─→ 信息Agent: 查杭州天气/交通（并行）
        ├─→ 规划Agent: 生成行程框架（依赖景点数据）
        ├─→ 推荐Agent: 基于偏好填充景点/餐厅
        └─→ 预算Agent: 校验费用约束
Step 4: 各Agent独立完成，结果返回Supervisor
Step 5: Supervisor 结果汇总 + 冲突消解
        └─ 冲突示例: 规划Agent推荐的景点超出预算Agent限额
           → 优先采纳预算Agent约束 → 通知规划Agent降配
Step 6: Supervisor 输出结构化行程 → 用户
Step 7: 用户一键调整 → 触发反馈循环（重新进入Step 2）
```

**并行设计价值：** 层级调度模式较平等群聊模式开发效率提升10倍以上，任务成功率从<30%提升至>90%。

---

## 四、状态共享：TravelState

所有Agent共享同一 `TravelState` 对象，但每个Agent只读写与其职责相关的字段（"只返回需要修改的状态字段的增量更新"）。

| 字段 | 类型 | 作用域 | 说明 |
|------|------|--------|------|
| `messages` | `Annotated[list, add_messages]` | 全局 | 消息历史（reducer合并，**必须有reducer否则覆盖**） |
| `user_id` | `str` | 全局 | 用户标识 |
| `session_id` | `str` | 全局 | 会话标识 |
| `intent` | `str` | Supervisor | 识别出的意图 |
| `destination` | `str` | 全局 | 目的地 |
| `budget` | `dict` | 全局 | 预算（总额/已分配/已花费） |
| `dates` | `dict` | 全局 | 出行日期范围 |
| `itinerary` | `dict` | Planning | 行程规划结果 |
| `recommendations` | `list` | Recommend | 推荐列表 |
| `weather_info` | `dict` | Info | 天气查询结果 |
| `memory_facts` | `list` | Memory | 检索到的用户记忆 |
| `final_response` | `str` | Supervisor | 最终聚合回复（终止标记） |
| `metadata` | `dict` | 全局 | 执行元数据（token用量/延迟/模型） |

> ⚠️ `add_messages` reducer 是消息列表正确合并的关键——没有它，每个节点都会覆盖消息列表，导致模型在第一次工具调用后"失去记忆"。

---

## 五、冲突消解规则

当多个Agent结果矛盾时，Supervisor 按优先级仲裁：

| 冲突场景 | 仲裁规则 |
|----------|----------|
| 规划Agent景点 vs 预算Agent限额 | **预算优先** — 通知规划Agent降配（替换免费景点/调整餐厅） |
| 规划Agent路线 vs 信息Agent天气 | **安全优先** — 暴雨/高温天气户外活动改为室内 |
| 推荐Agent结果 vs 用户偏好记忆 | **用户偏好优先** — Mem0记忆中的偏好覆盖通用推荐 |
| 多Agent同时修改同一字段 | Supervisor 串行化写入，后写者需基于前写者结果 |

---

## 六、边与循环控制

| 边类型 | 用途 | 实现 |
|--------|------|------|
| 静态边 | 固定节点连接 | `graph.add_edge(A, B)` |
| 条件边 | Supervisor意图路由 | `graph.add_conditional_edges(supervisor, route_fn)` |
| 反馈边 | 迭代优化（如预算超支→重新规划） | 条件边返回Planning Agent |
| 终止边 | final_response填充→END | 条件边返回`END` |

**死循环防护（4道）：**
1. 迭代上限：`iteration >= 10 → END`（硬性停止）
2. 超时控制：复杂编排<90秒
3. 重复检测：状态哈希比对，两步间状态相同→熔断
4. 最大LLM调用次数：25次

---

## 七、Human-in-the-loop

通过 LangGraph `interrupt()` + `Command(resume=)` 实现：

- **触发场景：** 调用产生费用的工具前（如预订API）、行程方案生成后需用户确认
- **流程：** `interrupt(value)`暂停图 → 展示待执行操作 → 用户 approve/reject/edit → `Command(resume=...)`恢复
- **保障：** Checkpoint自动保存，服务器重启不丢状态

---

## 八、Multi-Agent vs Workflow 的边界

> 面试官致命追问："你的多Agent之间到底有没有交流？"

本项目必须是真 Multi-Agent，而非固定路由的 Workflow：

| 特征 | Workflow（减分） | Multi-Agent（本项目） |
|------|-----------------|---------------------|
| 路由 | 固定router分发 | Supervisor基于意图动态路由 |
| 通信 | 无，各做各的 | 任务委托 + 状态共享 |
| 错误处理 | 独立 | 错误传递给Supervisor，触发替代Agent |
| 冲突 | 无 | 有冲突消解（预算vs规划） |

**实施红线：** Agent之间必须展示真正的自主决策能力——任务委托、状态共享、错误传递，缺一不可。
