# Phase 5：核心功能完善 — 对话记忆持久化 + 历史会话 + 上下文压缩

> **For agentic workers:** 按照本文件中的 Task 顺序执行。每个 Task 完成后运行 mypy + ruff + pytest，全部通过后用中文 commit message 提交。开发完成后 push 到远程仓库。

**Goal:** 让 TourSwarm 从"内存级一次性对话"变成"持久化多轮对话Agent" — Redis存短期对话、PostgreSQL存历史会话和行程、上下文压缩裁剪防止token爆炸。

**Architecture:**
```
当前：SessionState.messages（内存，重启即丢）
目标：
  用户消息 → Redis（短期，TTL 30min，对话历史）→ 超过窗口触发压缩
           → PostgreSQL（长期持久化，会话列表+行程归档）
           → 前端可查看历史会话和行程
```

**Tech Stack:** Python 3.11+ / Redis / PostgreSQL + SQLAlchemy / FastAPI / Vue3 / mypy / ruff

**范围边界:** 不做 AgenticRAG / 不做小红书爬虫 / 不做图片 / 不做地图可视化 / 不做登录。专注记忆+持久化+压缩。

---

## 设计参考

### Claude Code 的记忆/压缩机制（参考实现）

1. **滑动窗口**：保留最近N轮完整对话，更早的压缩为摘要
2. **自动压缩**：当 token 数接近上下文窗口限制时，自动触发压缩
3. **压缩策略**：
   - 旧消息 → LLM 摘要为一条 system 消息
   - 保留最近几轮完整对话
   - 保留 system prompt
4. **持久化**：对话历史持久化到文件/数据库，重启可恢复

### 我们的实现映射

| Claude Code 机制 | 我们的实现 | 存储位置 |
|-----------------|-----------|----------|
| 滑动窗口 | 保留最近20轮（40条消息） | Redis |
| 自动压缩 | 超过阈值触发 LLM 摘要 | Redis（更新后的消息列表） |
| 持久化 | 会话+行程归档 | PostgreSQL |
| 恢复 | 刷新页面从 Redis 恢复对话 | Redis → API → 前端 |

---

## 文件结构

新增/修改的文件（★新增，🔄修改）：

```
tour-agent/
├── core/
│   └── memory/
│       ├── short_term.py          # 🔄 接入API层（已有，补充接入逻辑）
│       ├── compressor.py          # ★ 上下文压缩器（滑窗+摘要）
│       └── session_store.py       # ★ 会话存储管理器（Redis+PG双写）
├── db/
│   ├── __init__.py
│   ├── models.py                  # ★ SQLAlchemy 模型（Session/Message/ItineraryRecord）
│   ├── database.py                # ★ 数据库连接管理
│   └── migrations.py              # ★ 表创建/迁移
├── api/
│   ├── routes.py                  # 🔄 新增历史会话/行程 REST 接口
│   ├── schemas.py                 # 🔄 新增历史相关 schema
│   ├── ws.py                      # 🔄 接入 Redis 对话历史 + 压缩
│   └── services/
│       └── chat_runner.py         # 🔄 消息持久化（Redis+PG双写）
├── frontend/src/
│   ├── views/ChatView.vue         # 🔄 加载历史消息 + 历史会话侧边栏
│   ├── components/
│   │   ├── SessionList.vue        # ★ 历史会话列表
│   │   └── HistoryItinerary.vue   # ★ 历史行程查看
│   ├── stores/
│   │   ├── chat.ts                # 🔄 恢复对话历史
│   │   └── session.ts             # ★ 历史会话 store
│   └── types/api.ts               # 🔄 新增历史类型
└── tests/
    ├── core/memory/
    │   ├── test_compressor.py     # ★
    │   └── test_session_store.py  # ★
    ├── db/
    │   └── test_models.py         # ★
    └── api/
        └── test_history_routes.py # ★
```

---

## Task 1：PostgreSQL 数据模型与连接管理

**Files:**
- Create: `db/__init__.py`
- Create: `db/database.py`
- Create: `db/models.py`
- Create: `db/migrations.py`
- Test: `tests/db/__init__.py`
- Test: `tests/db/test_models.py`

### 数据模型设计

```python
# db/models.py

class SessionRecord(Base):
    """会话记录 — 每次对话创建一条。"""
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")    # 从第一条消息提取
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/archived

class MessageRecord(Base):
    """消息记录 — 每条用户/助手消息。"""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user/assistant/system
    content: Mapped[str] = mapped_column(Text)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

class ItineraryRecord(Base):
    """行程归档 — 每次生成的行程保存。"""
    __tablename__ = "itineraries"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    destination: Mapped[str] = mapped_column(String(100))
    content_json: Mapped[str] = mapped_column(Text)  # Itinerary.model_dump_json()
    total_cost: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

### 实现要点

- `db/database.py`：用 SQLAlchemy async engine，从 `settings.postgres_dsn` 创建连接
- `db/migrations.py`：`async def init_db()` 创建所有表（用 `Base.metadata.create_all`）
- 测试用 SQLite in-memory（`sqlite+aiosqlite:///:memory:`）替代 PostgreSQL

- [ ] 写失败测试（SessionRecord/MessageRecord/ItineraryRecord CRUD）
- [ ] 实现 db/database.py（async engine + session factory）
- [ ] 实现 db/models.py（3个表）
- [ ] 实现 db/migrations.py（init_db）
- [ ] mypy + ruff + pytest 通过
- [ ] commit: `feat: 新增PostgreSQL数据模型和连接管理`

---

## Task 2：上下文压缩器

**Files:**
- Create: `core/memory/compressor.py`
- Test: `tests/core/memory/test_compressor.py`

### 压缩策略（参考 Claude Code）

```python
# core/memory/compressor.py

class ContextCompressor:
    """上下文压缩器 — 防止对话历史 token 爆炸。

    策略（参考 Claude Code 的 auto-compact）：
    1. 估算消息总 token 数（近似：字符数 / 3）
    2. 超过阈值（默认 6000 token ≈ 18000 字符）时触发压缩
    3. 压缩：保留 system prompt + 最近 N 轮完整对话 + 旧消息 LLM 摘要
    4. 摘要作为一条 system 消息插入
    """

    def __init__(self, llm, max_tokens: int = 6000, keep_recent_turns: int = 6):
        ...

    def estimate_tokens(self, messages: list[dict[str, str]]) -> int:
        """估算消息列表的 token 数（近似算法）。"""
        ...

    def should_compress(self, messages: list[dict[str, str]]) -> bool:
        """判断是否需要压缩。"""
        return self.estimate_tokens(messages) > self.max_tokens

    async def compress(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """压缩旧消息，保留最近几轮 + 摘要。

        1. 分离 system prompt
        2. 旧消息 → LLM 摘要为一条 system 消息
        3. 保留最近 keep_recent_turns 轮完整对话
        4. 返回：[system_prompt, summary_msg, recent_messages...]
        """
        ...
```

### 压缩 prompt

```
请将以下对话历史压缩为简洁摘要，保留：
- 用户的核心需求（目的地/预算/偏好/日期）
- Agent 的关键决策和推荐
- 已确认的行程要点
- 用户反馈和调整要求

只输出摘要内容，不要其他文字。
```

### 测试场景

- `test_estimate_tokens` — 估算 token 数
- `test_should_compress_under_threshold` — 未超阈值不压缩
- `test_should_compress_over_threshold` — 超阈值触发压缩
- `test_compress_preserves_system_and_recent` — 保留 system + 最近几轮
- `test_compress_uses_llm_summary` — 旧消息被 LLM 摘要
- `test_compress_handles_llm_error` — LLM 失败时保留原始消息
- `test_compress_empty_messages` — 空消息不崩溃

- [ ] 写失败测试
- [ ] 实现 compressor.py
- [ ] mypy + ruff + pytest 通过
- [ ] commit: `feat: 新增上下文压缩器支持滑窗摘要`

---

## Task 3：会话存储管理器（Redis + PostgreSQL 双写）

**Files:**
- Create: `core/memory/session_store.py`
- Test: `tests/core/memory/test_session_store.py`

### 职责

```python
# core/memory/session_store.py

class SessionStore:
    """会话存储管理器 — Redis 热数据 + PostgreSQL 持久化。

    职责：
    1. 对话消息存 Redis（TTL 30min，热数据，低延迟）
    2. 消息同时写 PostgreSQL（持久化，历史查询）
    3. 会话创建/列表/归档
    4. 行程归档
    5. 对话历史恢复（Redis优先，Redis miss 回退 PostgreSQL）
    """

    def __init__(self, redis_client, db_session_factory, compressor: ContextCompressor | None = None):
        ...

    async def create_session(self, user_id: str, title: str = "") -> str:
        """创建新会话，写入 PostgreSQL。"""
        ...

    async def save_message(self, session_id: str, role: str, content: str, tool_calls: list | None = None) -> None:
        """保存消息：Redis（追加+刷新TTL）+ PostgreSQL（INSERT）。"""
        ...

    async def load_messages(self, session_id: str) -> list[dict[str, str]]:
        """加载对话历史：Redis优先，miss 时从 PostgreSQL 恢复并回填 Redis。"""
        ...

    async def archive_itinerary(self, session_id: str, user_id: str, itinerary: Itinerary) -> None:
        """归档行程到 PostgreSQL。"""
        ...

    async def list_sessions(self, user_id: str, limit: int = 20) -> list[dict]:
        """列出用户的历史会话。"""
        ...

    async def get_session_messages(self, session_id: str) -> list[dict]:
        """从 PostgreSQL 加载会话全部消息（历史回看）。"""
        ...

    async def maybe_compress(self, session_id: str) -> bool:
        """检查并执行上下文压缩（如果超过阈值）。返回是否执行了压缩。"""
        ...
```

### 测试场景

- 用 fakeredis + SQLite in-memory
- `test_create_session` — 创建会话写入PG
- `test_save_message_redis_and_pg` — 消息双写
- `test_load_messages_from_redis` — Redis命中
- `test_load_messages_fallback_to_pg` — Redis miss 回退PG
- `test_archive_itinerary` — 行程归档
- `test_list_sessions` — 历史会话列表
- `test_maybe_compress_triggers` — 超阈值触发压缩
- `test_maybe_compress_skips` — 未超阈值跳过

- [ ] 写失败测试
- [ ] 实现 session_store.py
- [ ] mypy + ruff + pytest 通过
- [ ] commit: `feat: 新增会话存储管理器支持Redis+PG双写`

---

## Task 4：API 层接入 — WebSocket + REST 历史接口

**Files:**
- Modify: `api/routes.py` — 新增历史会话 REST 接口
- Modify: `api/schemas.py` — 新增历史相关 schema
- Modify: `api/ws.py` — 接入 SessionStore
- Modify: `api/services/chat_runner.py` — 消息持久化
- Modify: `api/main.py` — 构建 SessionStore
- Test: `tests/api/test_history_routes.py`

### 新增 REST 接口

```
GET  /api/v1/sessions?user_id=xxx          → 历史会话列表
GET  /api/v1/sessions/{id}/messages        → 会话消息历史
GET  /api/v1/sessions/{id}/itineraries     → 会话行程列表
GET  /api/v1/itineraries?user_id=xxx       → 用户所有行程
```

### 新增 Schema

```python
class SessionSummary(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str
    status: str

class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]

class HistoryMessage(BaseModel):
    role: str
    content: str
    tool_calls: list[dict] | None = None
    created_at: str

class HistoryItinerary(BaseModel):
    id: int
    destination: str
    total_cost: int
    created_at: str
    content: Itinerary
```

### WebSocket 改动

```python
# api/ws.py — 接入 SessionStore
# 1. 收到用户消息 → session_store.save_message()
# 2. Agent 回复后 → session_store.save_message()
# 3. Agent 回复后 → session_store.maybe_compress()
# 4. 如果有行程 → session_store.archive_itinerary()
# 5. 历史消息 → session_store.load_messages() 传给 Agent
```

### 测试场景

- `test_list_sessions` — 返回用户历史会话
- `test_get_session_messages` — 返回会话消息历史
- `test_get_itineraries` — 返回用户行程列表
- `test_ws_saves_message_to_store` — WS 消息持久化
- `test_ws_loads_history_on_connect` — 连接时加载历史

- [ ] 写失败测试
- [ ] 修改 routes.py（新增4个接口）
- [ ] 修改 schemas.py（新增4个 schema）
- [ ] 修改 ws.py（接入 SessionStore）
- [ ] 修改 chat_runner.py（消息持久化）
- [ ] 修改 main.py（构建 SessionStore）
- [ ] mypy + ruff + pytest 通过
- [ ] commit: `feat: API层接入会话持久化和历史接口`

---

## Task 5：前端历史会话侧边栏

**Files:**
- Create: `frontend/src/components/SessionList.vue`
- Create: `frontend/src/stores/session.ts`
- Modify: `frontend/src/views/ChatView.vue` — 加侧边栏 + 加载历史
- Modify: `frontend/src/types/api.ts` — 历史类型
- Modify: `frontend/src/api/chat.ts` — 历史API请求

### 前端布局

```
┌──────────┬───────────────────────────┐
│ 历史会话  │  聊天主区                  │
│          │                           │
│ ▸ 杭州2日│  👤 帮我规划杭州2日游       │
│ ▸ 莆田   │  🤖 [思考过程] 好的...     │
│ ▸ 北京   │     [行程卡片]             │
│          │                           │
│ + 新对话  │  [输入框]         [发送]   │
└──────────┴───────────────────────────┘
```

### 功能

1. 左侧侧边栏：历史会话列表（点击可回看）
2. "+ 新对话"按钮：创建新会话
3. 点击历史会话：加载该会话的消息历史到聊天区
4. 历史行程：在消息流中以卡片展示

- [ ] 实现 SessionList.vue
- [ ] 实现 session.ts store
- [ ] 修改 ChatView.vue 加侧边栏
- [ ] 修改 types/api.ts
- [ ] 修改 api/chat.ts
- [ ] 前端 vitest 通过
- [ ] commit: `feat: 前端新增历史会话侧边栏`

---

## Task 6：全量验收 + 推送远程

- [ ] 运行 `bash scripts/check.sh`（后端全绿）
- [ ] 运行 `cd frontend && npm run test -- --run`（前端全绿）
- [ ] 联调验证清单：
  ```
  □ 发送消息后刷新页面 → 对话历史保留（Redis 30min内）
  □ 重启后端 → PostgreSQL 中有历史会话记录
  □ 左侧侧边栏显示历史会话列表
  □ 点击历史会话 → 加载之前的对话和行程
  □ 长对话超过阈值 → 触发压缩（日志可见）
  □ 多轮对话上下文保持（追问"换一个"能基于上下文调整）
  ```
- [ ] commit: `milestone: Phase 5 核心功能完善 — 对话持久化+历史会话+上下文压缩`
- [ ] push 到远程仓库

---

## 验收标准

- [ ] Redis 存对话历史，TTL 30分钟，刷新页面可恢复
- [ ] PostgreSQL 持久化会话/消息/行程，重启不丢
- [ ] 上下文压缩器：超阈值自动摘要旧消息
- [ ] 前端历史会话侧边栏可查看回看
- [ ] WebSocket 接入 SessionStore 实现消息双写
- [ ] 4个历史 REST 接口可用
- [ ] mypy + ruff + pytest 全绿
- [ ] 前端 vitest 全绿

## 约束

- commit 消息用中文
- 不修改 Phase 2 的 agents/graph.py 和4个Agent文件
- 不修改 Phase 4.5 的 agents/react_agent.py 核心逻辑（只加 on_tool_event 回调已有）
- 测试用 fakeredis + SQLite in-memory，不依赖真实 Redis/PostgreSQL
- 前端 TypeScript strict
- 所有 Python 代码有 type hints + mypy strict + ruff
