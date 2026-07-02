# Phase 3：记忆系统集成 — TDD 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redis短期记忆 + Mem0长期记忆 + 摘要压缩，让Agent跨会话记住用户旅游偏好。CLI两轮对话脚本可演示"会话1说喜欢海鲜→会话2推荐含海鲜"。

**Architecture:** graph中planning前插入memory_node检索用户偏好写入state；Agent回复后异步调Mem0提取记忆事实；Redis做短期会话状态（TTL 30min）；对话超20轮触发摘要压缩。

**Tech Stack:** Python 3.11+ / Redis / Mem0 / Qdrant / LangGraph / Pydantic v2 / mypy / ruff

**类型安全约定：** 所有函数标注 type hints；记忆数据用 Pydantic 模型；每个 Task 完成后跑 mypy + ruff check。

**范围边界：** Phase 3 只做记忆系统。确定性验证器→Phase 4，FastAPI/前端→Phase 4，Offload/Isolate压缩→Phase 4。

**预计耗时：** Week 5-6（10个工作日）

---

## 设计决策（grill-me 对齐结果）

| 决策点 | 结论 |
|--------|------|
| 记忆范围 | 纯旅游记忆（景点类型/预算习惯/餐饮口味/住宿偏好） |
| 短期记忆 | Redis（LangGraph RedisSaver Checkpointer，TTL 30min） |
| 长期记忆 | Mem0 + Qdrant（自动提取，ADD/UPDATE/DELETE/NOOP） |
| 记忆提取 | 每轮异步提取（Agent回复后后台调Mem0，不阻塞） |
| 记忆注入 | 规划Agent前注入（memory_node检索偏好写入state） |
| 上下文压缩 | 只做摘要压缩（超20轮旧消息摘要为一条） |
| 集成方式 | 加memory_node（不改现有Agent代码） |
| 验证方式 | CLI两轮对话脚本（会话1→会话2跨会话验证） |

---

## 文件结构

Phase 3 完成后的目录结构（新增部分标 ★）：

```
tour-agent/
├── core/
│   ├── state.py                     # 已有，扩展 memory_facts 字段
│   ├── llm.py                       # 已有
│   └── memory/                      # ★ 记忆系统
│       ├── __init__.py
│       ├── short_term.py            # ★ Redis 短期记忆 + 摘要压缩
│       ├── long_term.py             # ★ Mem0 长期记忆
│       └── extractor.py             # ★ 记忆提取与注入
├── agents/
│   ├── memory_node.py               # ★ 记忆检索节点（graph中planning前）
│   ├── graph.py                     # 已有，扩展插入 memory_node
│   └── ...                          # 其他Agent不变
├── scripts/
│   ├── demo_chat.py                 # 已有，扩展支持多会话
│   └── demo_memory.py               # ★ 两轮对话记忆演示脚本
├── tests/
│   └── core/
│       └── memory/                  # ★ 记忆系统测试
│           ├── __init__.py
│           ├── test_short_term.py
│           ├── test_long_term.py
│           └── test_extractor.py
│   └── agents/
│       └── test_memory_node.py      # ★
└── ...
```

---

## Task 1：短期记忆 — Redis 会话状态管理

**Files:**
- Create: `core/memory/__init__.py`
- Create: `core/memory/short_term.py`
- Test: `tests/core/memory/__init__.py`
- Test: `tests/core/memory/test_short_term.py`

短期记忆负责管理当前会话的对话上下文，包括滑动窗口和摘要压缩。

- [ ] **Step 1: 创建目录和 __init__.py**

```bash
mkdir -p core/memory tests/core/memory
touch core/memory/__init__.py tests/core/memory/__init__.py
```

- [ ] **Step 2: 写失败测试**

创建 `tests/core/memory/test_short_term.py`：

```python
"""短期记忆（Redis）测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.short_term import ShortTermMemory


@pytest.fixture
def mock_redis() -> MagicMock:
    return MagicMock()


@pytest.fixture
def memory(mock_redis: MagicMock) -> ShortTermMemory:
    return ShortTermMemory(redis_client=mock_redis, session_id="test-session", max_turns=20)


def test_short_term_memory_creates_key_with_session_id(memory: ShortTermMemory) -> None:
    """Redis key 应包含 session_id。"""
    assert "test-session" in memory._key


async def test_save_message_stores_in_redis(memory: ShortTermMemory, mock_redis: MagicMock) -> None:
    """save_message 应将消息存入 Redis。"""
    await memory.save_message(role="user", content="我喜欢海鲜")
    mock_redis.setex.assert_called_once()
    args = mock_redis.setex.call_args
    assert "test-session" in args.args[0]
    assert 1800 == args.args[1]  # TTL 30分钟 = 1800秒


async def test_load_messages_returns_empty_for_new_session(
    memory: ShortTermMemory, mock_redis: MagicMock
) -> None:
    """新会话加载消息应返回空列表。"""
    mock_redis.get.return_value = None
    messages = await memory.load_messages()
    assert messages == []


async def test_load_messages_returns_stored_messages(
    memory: ShortTermMemory, mock_redis: MagicMock
) -> None:
    """加载消息应返回已存储的消息列表。"""
    import json
    stored = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}]
    mock_redis.get.return_value = json.dumps(stored, ensure_ascii=False)
    messages = await memory.load_messages()
    assert len(messages) == 2
    assert messages[0]["content"] == "你好"


async def test_clear_session_deletes_key(memory: ShortTermMemory, mock_redis: MagicMock) -> None:
    """clear 应删除 Redis 中的会话 key。"""
    await memory.clear()
    mock_redis.delete.assert_called_once()
    assert "test-session" in mock_redis.delete.call_args.args[0]


def test_should_summarize_returns_false_under_threshold(memory: ShortTermMemory) -> None:
    """消息数未超阈值时不需要摘要。"""
    messages = [{"role": "user", "content": "msg"}] * 10
    assert memory.should_summarize(messages) is False


def test_should_summarize_returns_true_over_threshold(memory: ShortTermMemory) -> None:
    """消息数超过 max_turns*2 时需要摘要（每轮=2条消息）。"""
    messages = [{"role": "user", "content": "msg"}] * 50  # 25轮 = 50条 > 20轮阈值
    assert memory.should_summarize(messages) is True


async def test_summarize_uses_llm(memory: ShortTermMemory) -> None:
    """摘要应调用 LLM 压缩旧消息。"""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="用户喜欢海鲜和自然风光"))
    messages = [
        {"role": "user", "content": "我喜欢海鲜"},
        {"role": "assistant", "content": "好的，我会推荐海鲜餐厅"},
        {"role": "user", "content": "也喜欢自然风光"},
        {"role": "assistant", "content": "明白"},
    ]
    result = await memory.summarize(messages, llm=mock_llm)

    # 摘要后应保留 system prompt + 摘要消息 + 最近2轮
    assert len(result) < len(messages)
    assert any("海鲜" in m.get("content", "") for m in result)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/core/memory/test_short_term.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: 实现短期记忆**

创建 `core/memory/short_term.py`：

```python
"""短期记忆 — Redis 会话状态管理 + 摘要压缩。

职责：
1. 存储当前会话的对话历史（Redis，TTL 30分钟）
2. 滑动窗口：超 max_turns 轮时触发摘要压缩
3. 摘要压缩：旧消息用 LLM 压缩为一条摘要消息

设计原则：
- 核心链路不依赖 Redis（Redis 挂了不阻断行程生成）
- 摘要只压缩旧消息，始终保留 system prompt 和最近几轮完整对话
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 1800  # 30分钟
MAX_TURNS = 20  # 超过20轮触发摘要
KEEP_RECENT_TURNS = 4  # 摘要时保留最近4轮完整对话（8条消息）


class ShortTermMemory:
    """Redis-backed short-term session memory with sliding window summarization."""

    def __init__(
        self,
        redis_client: Any,
        session_id: str,
        max_turns: int = MAX_TURNS,
    ) -> None:
        self._redis = redis_client
        self._session_id = session_id
        self._max_turns = max_turns
        self._key = f"session:{session_id}:messages"

    async def save_message(self, role: str, content: str) -> None:
        """保存一条消息到 Redis，刷新 TTL。"""
        messages = await self.load_messages()
        messages.append({"role": role, "content": content})
        self._redis.setex(
            self._key,
            SESSION_TTL_SECONDS,
            json.dumps(messages, ensure_ascii=False),
        )

    async def load_messages(self) -> list[dict[str, str]]:
        """加载当前会话的全部消息。"""
        raw = self._redis.get(self._key)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to load messages for %s: %s", self._session_id, e)
        return []

    async def clear(self) -> None:
        """清除会话消息。"""
        self._redis.delete(self._key)

    def should_summarize(self, messages: list[dict[str, str]]) -> bool:
        """判断是否需要摘要压缩（消息数 > max_turns * 2）。"""
        return len(messages) > self._max_turns * 2

    async def summarize(
        self,
        messages: list[dict[str, str]],
        llm: Any,
    ) -> list[dict[str, str]]:
        """摘要压缩旧消息，保留最近几轮完整对话。

        策略：
        - 保留 system prompt（如果有）
        - 旧消息用 LLM 压缩为一条摘要
        - 保留最近 KEEP_RECENT_TURNS 轮完整对话

        Returns:
            压缩后的消息列表
        """
        if len(messages) <= KEEP_RECENT_TURNS * 2:
            return messages

        # 分离 system prompt
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 旧消息 = 全部非system消息 - 最近几轮
        keep_count = KEEP_RECENT_TURNS * 2
        old_messages = non_system[:-keep_count] if len(non_system) > keep_count else []
        recent_messages = non_system[-keep_count:] if len(non_system) > keep_count else non_system

        if not old_messages:
            return system_msgs + recent_messages

        # 调 LLM 摘要旧消息
        conversation_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in old_messages
        )
        summary_prompt = f"""请将以下对话历史压缩为一条简洁的摘要，保留关键信息（用户偏好、已确认的行程参数、重要决策）：

{conversation_text}

只输出摘要内容，不要其他文字。"""

        try:
            response = await llm.ainvoke([{"role": "user", "content": summary_prompt}])
            summary_text = getattr(response, "content", str(response))
        except Exception as e:
            logger.warning("摘要压缩失败，保留原始消息: %s", e)
            return system_msgs + non_system

        summary_msg = {"role": "system", "content": f"[对话摘要] {summary_text}"}
        return system_msgs + [summary_msg] + recent_messages
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/core/memory/test_short_term.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: 类型检查 + lint**

```bash
mypy core/memory/short_term.py tests/core/memory/test_short_term.py
ruff check core/memory/short_term.py tests/core/memory/test_short_term.py
```

- [ ] **Step 7: Commit**

```bash
git add core/memory/ tests/core/memory/
git commit -m "feat: add Redis short-term memory with sliding window summarization"
```

---

## Task 2：长期记忆 — Mem0 集成

**Files:**
- Create: `core/memory/long_term.py`
- Test: `tests/core/memory/test_long_term.py`

长期记忆负责跨会话存储用户旅游偏好，通过 Mem0 的提取-更新流水线管理。

- [ ] **Step 1: 写失败测试**

创建 `tests/core/memory/test_long_term.py`：

```python
"""长期记忆（Mem0）测试。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.memory.long_term import LongTermMemory, MemoryFact


@pytest.fixture
def mock_mem0() -> MagicMock:
    client = MagicMock()
    client.search = MagicMock(return_value=[
        {"memory": "用户喜欢海鲜", "score": 0.95, "id": "mem_001"},
        {"memory": "用户偏好自然风光", "score": 0.88, "id": "mem_002"},
    ])
    client.add = MagicMock(return_value={"id": "mem_003"})
    return client


@pytest.fixture
def memory(mock_mem0: MagicMock) -> LongTermMemory:
    return LongTermMemory(mem0_client=mock_mem0, user_id="user_123")


def test_search_returns_memory_facts(
    memory: LongTermMemory, mock_mem0: MagicMock
) -> None:
    """search 应返回 MemoryFact 列表。"""
    facts = memory.search("用户喜欢什么食物")
    assert len(facts) == 2
    assert isinstance(facts[0], MemoryFact)
    assert facts[0].content == "用户喜欢海鲜"
    assert facts[0].score == 0.95


def test_search_returns_empty_on_error(memory: LongTermMemory) -> None:
    """Mem0 搜索失败时返回空列表（优雅降级）。"""
    memory._mem0.search = MagicMock(side_effect=Exception("connection error"))
    facts = memory.search("什么食物")
    assert facts == []


def test_search_filters_by_user_id(
    memory: LongTermMemory, mock_mem0: MagicMock
) -> None:
    """search 应按 user_id 过滤。"""
    memory.search("景点偏好")
    mock_mem0.search.assert_called_once()
    call_kwargs = mock_mem0.search.call_args
    assert "user_123" in str(call_kwargs)


async def test_extract_and_store_calls_mem0_add(
    memory: LongTermMemory, mock_mem0: MagicMock
) -> None:
    """extract_and_store 应调用 Mem0 的 add 方法。"""
    await memory.extract_and_store(
        conversation="用户: 我对海鲜过敏\n助手: 好的，我会避免推荐海鲜"
    )
    mock_mem0.add.assert_called_once()
    call_kwargs = mock_mem0.add.call_args
    assert "user_123" in str(call_kwargs)


async def test_extract_and_store_handles_error(
    memory: LongTermMemory, mock_mem0: MagicMock
) -> None:
    """Mem0 存储失败时不抛异常（优雅降级）。"""
    mock_mem0.add = MagicMock(side_effect=Exception("storage error"))
    # 不应抛异常
    await memory.extract_and_store(conversation="用户: 我喜欢爬山")


def test_format_facts_for_prompt(memory: LongTermMemory) -> None:
    """format_facts 应将记忆格式化为 prompt 可用文本。"""
    facts = [
        MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="1"),
        MemoryFact(content="预算敏感，通常500元以内", score=0.88, fact_id="2"),
    ]
    text = memory.format_facts_for_prompt(facts)
    assert "海鲜" in text
    assert "500" in text


def test_format_facts_empty_returns_empty_string(memory: LongTermMemory) -> None:
    """空记忆列表应返回空字符串。"""
    assert memory.format_facts_for_prompt([]) == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/memory/test_long_term.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现长期记忆**

创建 `core/memory/long_term.py`：

```python
"""长期记忆 — Mem0 跨会话用户偏好管理。

Mem0 提取-更新流水线：
1. 从对话中提取候选记忆事实（LLM 提取）
2. 向量检索 Top-K 相似记忆
3. LLM 决策 ADD/UPDATE/DELETE/NOOP
4. 写入 Qdrant 向量库

设计原则：
- Mem0 不可用时不阻断核心功能（优雅降级）
- 记忆范围限于旅游偏好（景点/预算/餐饮/住宿）
- 检索时新记忆权重高于旧记忆（时间衰减）
"""
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MemoryFact:
    """一条用户记忆事实。"""

    content: str
    score: float = 0.0
    fact_id: str = ""
    created_at: str = ""


class LongTermMemory:
    """Mem0-backed long-term user preference memory."""

    def __init__(self, mem0_client: Any, user_id: str) -> None:
        self._mem0 = mem0_client
        self._user_id = user_id

    def search(self, query: str, limit: int = 10) -> list[MemoryFact]:
        """检索与查询相关的用户记忆。

        Args:
            query: 检索查询（如"用户喜欢什么食物"）
            limit: 返回数量上限

        Returns:
            MemoryFact 列表，按相关度降序。Mem0 不可用时返回空列表。
        """
        try:
            results = self._mem0.search(query=query, user_id=self._user_id, limit=limit)
            facts: list[MemoryFact] = []
            if isinstance(results, list):
                for item in results:
                    if isinstance(item, dict):
                        facts.append(
                            MemoryFact(
                                content=str(item.get("memory", "")),
                                score=float(item.get("score", 0.0)),
                                fact_id=str(item.get("id", "")),
                                created_at=str(item.get("created_at", "")),
                            )
                        )
            return facts
        except Exception as e:
            logger.warning("Mem0 搜索失败，返回空列表: %s", e)
            return []

    async def extract_and_store(self, conversation: str) -> None:
        """从对话中提取记忆事实并存储到 Mem0。

        Mem0 内部会：
        1. LLM 提取候选记忆
        2. 向量检索相似记忆
        3. 决策 ADD/UPDATE/DELETE/NOOP

        Args:
            conversation: 对话文本
        """
        try:
            self._mem0.add(conversation, user_id=self._user_id)
        except Exception as e:
            logger.warning("Mem0 记忆存储失败，跳过: %s", e)

    @staticmethod
    def format_facts_for_prompt(facts: list[MemoryFact]) -> str:
        """将记忆事实格式化为可注入 prompt 的文本。

        Args:
            facts: 记忆事实列表

        Returns:
            格式化文本，如 "已知用户偏好: 用户喜欢海鲜; 预算敏感500元以内"
            空列表返回空字符串。
        """
        if not facts:
            return ""
        items = [f.content for f in facts if f.content]
        if not items:
            return ""
        return "已知用户偏好: " + "; ".join(items)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/memory/test_long_term.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy core/memory/long_term.py tests/core/memory/test_long_term.py
ruff check core/memory/long_term.py tests/core/memory/test_long_term.py
```

- [ ] **Step 6: Commit**

```bash
git add core/memory/long_term.py tests/core/memory/test_long_term.py
git commit -m "feat: add Mem0 long-term memory with preference extraction and search"
```

---

## Task 3：记忆提取与注入 — 记忆管理器

**Files:**
- Create: `core/memory/extractor.py`
- Test: `tests/core/memory/test_extractor.py`

记忆管理器统一管理短期和长期记忆的协调：每轮回复后异步提取记忆，规划前检索注入。

- [ ] **Step 1: 写失败测试**

创建 `tests/core/memory/test_extractor.py`：

```python
"""记忆提取与注入测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.extractor import MemoryManager
from core.memory.long_term import LongTermMemory, MemoryFact


@pytest.fixture
def mock_long_term() -> MagicMock:
    lt = MagicMock(spec=LongTermMemory)
    lt.search = MagicMock(return_value=[
        MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="1"),
        MemoryFact(content="预算500元以内", score=0.88, fact_id="2"),
    ])
    lt.extract_and_store = AsyncMock()
    lt.format_facts_for_prompt = MagicMock(return_value="已知用户偏好: 用户喜欢海鲜; 预算500元以内")
    return lt


@pytest.fixture
def manager(mock_long_term: MagicMock) -> MemoryManager:
    return MemoryManager(long_term=mock_long_term)


async def test_extract_memories_async_does_not_block(
    manager: MemoryManager, mock_long_term: MagicMock
) -> None:
    """异步提取记忆不应阻塞（即使 Mem0 慢也不影响主流程）。"""
    await manager.extract_memories_async(
        user_message="我喜欢海鲜",
        assistant_message="好的，会推荐海鲜餐厅",
    )
    mock_long_term.extract_and_store.assert_awaited_once()


async def test_extract_memories_async_handles_error(
    manager: MemoryManager, mock_long_term: MagicMock
) -> None:
    """提取失败不抛异常。"""
    mock_long_term.extract_and_store = AsyncMock(side_effect=Exception("error"))
    await manager.extract_memories_async("msg", "reply")  # 不应抛异常


def test_retrieve_for_planning_returns_prompt_text(
    manager: MemoryManager, mock_long_term: MagicMock
) -> None:
    """检索记忆应返回可注入 prompt 的文本。"""
    text = manager.retrieve_for_planning("杭州美食行程")
    assert "海鲜" in text
    assert "500" in text


def test_retrieve_for_planning_returns_empty_on_no_memory() -> None:
    """无记忆时返回空字符串。"""
    lt = MagicMock(spec=LongTermMemory)
    lt.search = MagicMock(return_value=[])
    lt.format_facts_for_prompt = MagicMock(return_value="")
    mgr = MemoryManager(long_term=lt)
    text = mgr.retrieve_for_planning("什么行程")
    assert text == ""


def test_retrieve_facts_returns_list(
    manager: MemoryManager, mock_long_term: MagicMock
) -> None:
    """retrieve_facts 应返回 MemoryFact 列表。"""
    facts = manager.retrieve_facts("用户偏好")
    assert len(facts) == 2
    assert facts[0].content == "用户喜欢海鲜"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/memory/test_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现记忆管理器**

创建 `core/memory/extractor.py`：

```python
"""记忆管理器 — 协调短期和长期记忆。

职责：
1. 每轮 Agent 回复后异步提取记忆（调 Mem0）
2. 规划前检索用户偏好并格式化为 prompt 文本
3. 统一错误处理，记忆系统故障不阻断核心流程
"""
import asyncio
import logging
from typing import Any

from core.memory.long_term import LongTermMemory, MemoryFact

logger = logging.getLogger(__name__)


class MemoryManager:
    """Coordinates short-term and long-term memory operations."""

    def __init__(self, long_term: LongTermMemory) -> None:
        self._long_term = long_term

    async def extract_memories_async(
        self,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """异步从对话中提取并存储记忆。

        在 Agent 回复后调用，不阻塞用户响应。
        Mem0 内部会用 LLM 提取记忆事实并决策 ADD/UPDATE/DELETE/NOOP。

        Args:
            user_message: 用户消息
            assistant_message: Agent 回复
        """
        conversation = f"用户: {user_message}\n助手: {assistant_message}"
        try:
            await self._long_term.extract_and_store(conversation)
        except Exception as e:
            logger.warning("异步记忆提取失败，跳过: %s", e)

    def retrieve_for_planning(self, query: str) -> str:
        """检索用户偏好，格式化为可注入规划prompt的文本。

        Args:
            query: 检索查询（如"杭州美食行程偏好"）

        Returns:
            格式化文本（如"已知用户偏好: 喜欢海鲜; 预算500元"）。
            无记忆时返回空字符串。
        """
        facts = self._long_term.search(query)
        return self._long_term.format_facts_for_prompt(facts)

    def retrieve_facts(self, query: str) -> list[MemoryFact]:
        """检索用户偏好记忆事实。

        Args:
            query: 检索查询

        Returns:
            MemoryFact 列表
        """
        return self._long_term.search(query)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/memory/test_extractor.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy core/memory/extractor.py tests/core/memory/test_extractor.py
ruff check core/memory/extractor.py tests/core/memory/test_extractor.py
```

- [ ] **Step 6: Commit**

```bash
git add core/memory/extractor.py tests/core/memory/test_extractor.py
git commit -m "feat: add MemoryManager for async extraction and planning injection"
```

---

## Task 4：记忆节点 — 集成到 LangGraph

**Files:**
- Create: `agents/memory_node.py`
- Modify: `agents/graph.py`（插入 memory_node）
- Modify: `core/state.py`（扩展 memory_facts 字段）
- Test: `tests/agents/test_memory_node.py`

在 graph 中 planning 前插入记忆检索节点，把用户偏好写入 state。

- [ ] **Step 1: 写失败测试**

创建 `tests/agents/test_memory_node.py`：

```python
"""记忆节点测试。"""
from unittest.mock import MagicMock

import pytest

from agents.memory_node import create_memory_node


@pytest.fixture
def mock_memory_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.retrieve_for_planning = MagicMock(return_value="已知用户偏好: 用户喜欢海鲜; 预算500元以内")
    mgr.retrieve_facts = MagicMock(return_value=[
        MagicMock(content="用户喜欢海鲜", score=0.95),
    ])
    return mgr


def test_memory_node_returns_memory_context(mock_memory_manager: MagicMock) -> None:
    """memory_node 应返回 memory_context 和 memory_facts。"""
    node = create_memory_node(mock_memory_manager)
    state = {
        "destination": "杭州",
        "preferences": ["美食"],
        "messages": [],
    }
    result = node(state)

    assert "memory_context" in result
    assert "海鲜" in result["memory_context"]
    assert "memory_facts" in result
    assert len(result["memory_facts"]) >= 1


def test_memory_node_queries_with_destination_and_preferences(
    mock_memory_manager: MagicMock
) -> None:
    """记忆检索查询应包含目的地和偏好。"""
    node = create_memory_node(mock_memory_manager)
    state = {
        "destination": "杭州",
        "preferences": ["美食", "自然风光"],
        "messages": [],
    }
    node(state)

    mock_memory_manager.retrieve_for_planning.assert_called_once()
    call_arg = mock_memory_manager.retrieve_for_planning.call_args.args[0]
    assert "杭州" in call_arg
    assert "美食" in call_arg


def test_memory_node_handles_empty_state(mock_memory_manager: MagicMock) -> None:
    """空状态时不崩溃，返回空记忆。"""
    mgr = MagicMock()
    mgr.retrieve_for_planning = MagicMock(return_value="")
    mgr.retrieve_facts = MagicMock(return_value=[])

    node = create_memory_node(mgr)
    result = node({"messages": []})

    assert result["memory_context"] == ""
    assert result["memory_facts"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/test_memory_node.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 扩展 TravelState**

在 `core/state.py` 中添加 `memory_context` 和 `memory_facts` 字段：

```python
# 在 TravelState 类中添加（在 budget_breakdown 之后、iteration_count 之前）：
    memory_context: str  # 格式化的用户偏好文本（注入planning prompt）
    memory_facts: list[dict[str, Any]]  # 原始记忆事实列表
```

- [ ] **Step 4: 实现记忆节点**

创建 `agents/memory_node.py`：

```python
"""记忆节点 — 在规划前检索用户偏好。

插入 graph 中 recommend→planning 之间，
把 Mem0 检索到的用户偏好写入 state.memory_context，
供 planning_node 注入 prompt。
"""
from collections.abc import Callable
from typing import Any

from core.memory.extractor import MemoryManager


def create_memory_node(
    memory_manager: MemoryManager,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """创建记忆检索节点。

    Args:
        memory_manager: 记忆管理器

    Returns:
        LangGraph 节点函数
    """

    def _node(state: dict[str, Any]) -> dict[str, Any]:
        destination = str(state.get("destination", ""))
        preferences = state.get("preferences", [])
        prefs_str = " ".join(preferences) if isinstance(preferences, list) else ""
        query = f"{destination} {prefs_str} 行程偏好".strip()

        memory_context = memory_manager.retrieve_for_planning(query)
        memory_facts = memory_manager.retrieve_facts(query)

        return {
            "memory_context": memory_context,
            "memory_facts": [
                {
                    "content": f.content,
                    "score": f.score,
                }
                for f in memory_facts
            ],
        }

    return _node
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/agents/test_memory_node.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: 更新 graph.py 插入 memory_node**

修改 `agents/graph.py`，在 recommend→planning 之间插入 memory_node：

```python
# build_graph 函数签名增加 memory_manager 参数
def build_graph(
    weather_client: Any,
    scenic_client: Any,
    planning_llm: Any,
    budget_llm: Any,
    memory_manager: Any = None,  # Phase 3 新增，None 时不插入记忆节点
) -> Any:
    # ... 现有代码 ...

    # 添加 memory 节点（如果提供了 memory_manager）
    if memory_manager is not None:
        from agents.memory_node import create_memory_node
        memory_node = create_memory_node(memory_manager)
        graph.add_node("memory", memory_node)
        # barrier: info + recommend → memory → planning
        graph.add_edge(["info", "recommend"], "memory")
        graph.add_edge("memory", "planning")
    else:
        # 无记忆时的原有路径
        graph.add_edge(["info", "recommend"], "planning")

    # ... 后续代码不变 ...
```

- [ ] **Step 7: 更新 planning_node 注入记忆**

修改 `agents/planning.py` 的 `planning_node`，在构造 prompt 时注入 `memory_context`：

```python
# 在 create_planning_prompt 函数签名增加 memory_context 参数
def create_planning_prompt(
    destination: str,
    dates: dict[str, str],
    budget_total: int,
    preferences: list[str],
    weather_info: dict[str, Any],
    recommendations: list[dict[str, Any]],
    memory_context: str = "",  # Phase 3 新增
) -> str:
    # ... 现有代码 ...

    # 在 prompt 中注入用户偏好记忆
    memory_section = f"\n用户历史偏好:\n{memory_context}" if memory_context else ""

    return f"""请为以下需求生成旅游行程：
    ...
    候选景点:
    {spots_desc}
{memory_section}
    请生成行程方案..."""
```

```python
# 在 planning_node 中读取 memory_context
async def planning_node(state: dict[str, Any], llm: Any) -> dict[str, Itinerary]:
    prompt = create_planning_prompt(
        ...
        memory_context=str(state.get("memory_context", "")),  # 新增
    )
    # ... 后续不变 ...
```

- [ ] **Step 8: 运行全部测试确认通过**

Run: `pytest tests/ -v --tb=short`
Expected: 全部 PASS（包括 Phase 1/2 的测试不回归）

- [ ] **Step 9: 类型检查 + lint**

```bash
mypy agents/memory_node.py agents/graph.py agents/planning.py tests/agents/test_memory_node.py
ruff check agents/memory_node.py agents/graph.py agents/planning.py tests/agents/test_memory_node.py
```

- [ ] **Step 10: Commit**

```bash
git add agents/memory_node.py agents/graph.py agents/planning.py core/state.py tests/agents/test_memory_node.py
git commit -m "feat: integrate memory_node into graph, inject user preferences into planning"
```

---

## Task 5：Mem0 客户端初始化

**Files:**
- Create: `core/memory/mem0_factory.py`
- Test: `tests/core/memory/test_mem0_factory.py`

创建 Mem0 客户端的工厂函数，从 Settings 读取配置。

- [ ] **Step 1: 写失败测试**

创建 `tests/core/memory/test_mem0_factory.py`：

```python
"""Mem0 客户端工厂测试。"""
from unittest.mock import patch, MagicMock

import pytest


def test_create_mem0_client_returns_client() -> None:
    """create_mem0_client 应返回 Mem0 客户端实例。"""
    with patch.dict("os.environ", {
        "QWEATHER_API_KEY": "test",
        "AMAP_API_KEY": "test",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
    }):
        from core.memory.mem0_factory import create_mem0_client
        with patch("core.memory.mem0_factory.Mem0Client") as mock_class:
            mock_class.from_config.return_value = MagicMock()
            client = create_mem0_client()
            assert client is not None
            mock_class.from_config.assert_called_once()


def test_create_mem0_client_returns_none_on_error() -> None:
    """Mem0 初始化失败时返回 None（优雅降级）。"""
    with patch.dict("os.environ", {
        "QWEATHER_API_KEY": "test",
        "AMAP_API_KEY": "test",
    }):
        from core.memory.mem0_factory import create_mem0_client
        with patch("core.memory.mem0_factory.Mem0Client") as mock_class:
            mock_class.from_config.side_effect = Exception("Qdrant not available")
            client = create_mem0_client()
            assert client is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/memory/test_mem0_factory.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 Mem0 工厂**

创建 `core/memory/mem0_factory.py`：

```python
"""Mem0 客户端工厂 — 从 Settings 创建 Mem0 实例。

Mem0 配置：
- 向量后端: Qdrant（Docker 已就绪）
- Embedding: BAAI/bge-large-zh-v1.5（中文语义）
- LLM: DeepSeek（记忆提取用轻量模型即可）

Mem0 不可用时返回 None，系统降级为无长期记忆模式。
"""
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def create_mem0_client() -> Any | None:
    """创建 Mem0 客户端实例。

    Returns:
        Mem0 客户端实例，初始化失败时返回 None。
    """
    try:
        from mem0 import Mem0Client

        qdrant_host = os.environ.get("QDRANT_HOST", "localhost")
        qdrant_port = os.environ.get("QDRANT_PORT", "6333")
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        deepseek_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": qdrant_host,
                    "port": int(qdrant_port),
                },
            },
            "llm": {
                "provider": "openai_structured",
                "config": {
                    "api_key": deepseek_key,
                    "base_url": deepseek_url,
                    "model": "deepseek-chat",
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": "BAAI/bge-large-zh-v1.5",
                },
            },
        }

        client = Mem0Client.from_config(config)
        logger.info("Mem0 客户端初始化成功")
        return client
    except Exception as e:
        logger.warning("Mem0 初始化失败，降级为无长期记忆模式: %s", e)
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/memory/test_mem0_factory.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 类型检查 + lint**

```bash
mypy core/memory/mem0_factory.py tests/core/memory/test_mem0_factory.py
ruff check core/memory/mem0_factory.py tests/core/memory/test_mem0_factory.py
```

- [ ] **Step 6: Commit**

```bash
git add core/memory/mem0_factory.py tests/core/memory/test_mem0_factory.py
git commit -m "feat: add Mem0 client factory with Qdrant backend and graceful degradation"
```

---

## Task 6：CLI 两轮对话记忆演示脚本

**Files:**
- Create: `scripts/demo_memory.py`

演示跨会话记忆：会话1说"喜欢海鲜"→会话2规划行程时推荐含海鲜。

- [ ] **Step 1: 实现演示脚本**

创建 `scripts/demo_memory.py`：

```python
"""TourSwarm 记忆系统演示 — 跨会话记忆验证。

用法：
    python -m scripts.demo_memory

演示流程：
1. 会话1: 用户说"我喜欢海鲜" → Agent回复 → Mem0提取记忆
2. 会话2: 用户说"帮我规划杭州行程" → Agent检索记忆 → 推荐含海鲜
"""
import asyncio
import sys

from dotenv import load_dotenv

from core.config.settings import get_settings
from core.llm import create_llm
from core.memory.extractor import MemoryManager
from core.memory.long_term import LongTermMemory
from core.memory.mem0_factory import create_mem0_client
from mcp_servers.scenic.client import ScenicClient
from mcp_servers.weather.client import WeatherClient


async def run_memory_demo() -> None:
    """运行跨会话记忆演示。"""
    load_dotenv()
    settings = get_settings()

    print("=" * 60)
    print("TourSwarm 记忆系统演示")
    print("=" * 60)

    # 初始化 Mem0
    print("\n[初始化] 连接 Mem0 + Qdrant...")
    mem0_client = create_mem0_client()
    if mem0_client is None:
        print("⚠️  Mem0 不可用，演示无法进行。请确保 Docker 中的 Qdrant 已启动。")
        return

    user_id = "demo_user_001"
    long_term = LongTermMemory(mem0_client=mem0_client, user_id=user_id)
    memory_manager = MemoryManager(long_term=long_term)

    # 初始化其他组件
    weather_client = WeatherClient(api_key=settings.qweather_api_key)
    scenic_client = ScenicClient(data_path="data/mock/scenic_spots.json")
    planning_llm = create_llm(settings.llm_model)

    # === 会话1: 用户表达偏好 ===
    print("\n" + "=" * 60)
    print("📱 会话1: 用户表达偏好")
    print("=" * 60)

    user_msg_1 = "我下周想去杭州玩，我个人特别喜欢海鲜，预算大概500块"
    print(f"\n用户: {user_msg_1}")

    # 模拟 Agent 回复
    assistant_msg_1 = "好的！杭州有很多不错的海鲜餐厅，我会为您推荐。500元预算可以安排2天的行程。"
    print(f"助手: {assistant_msg_1}")

    # 异步提取记忆
    print("\n[记忆提取] 正在从对话中提取用户偏好...")
    await memory_manager.extract_memories_async(user_msg_1, assistant_msg_1)
    print("✅ 记忆已存储到 Mem0")

    # === 会话2: 新会话，验证记忆 ===
    print("\n" + "=" * 60)
    print("📱 会话2: 新会话（验证跨会话记忆）")
    print("=" * 60)

    user_msg_2 = "帮我规划一个杭州的周末行程"
    print(f"\n用户: {user_msg_2}")

    # 检索记忆
    print("\n[记忆检索] 正在从 Mem0 检索用户偏好...")
    memory_context = memory_manager.retrieve_for_planning("杭州行程偏好")

    if memory_context:
        print(f"✅ 检索到记忆: {memory_context}")
    else:
        print("⚠️  未检索到记忆（可能 Mem0 提取延迟或 Qdrant 未就绪）")

    # 构造带记忆的规划 prompt
    from agents.planning import create_planning_prompt

    prompt = create_planning_prompt(
        destination="杭州",
        dates={"start": "2026-07-12", "end": "2026-07-13"},
        budget_total=500,
        preferences=["美食"],
        weather_info={"error": True},
        recommendations=[
            {"id": "1", "name": "西湖", "ticket_price": 0, "rating": 4.8},
            {"id": "2", "name": "河坊街", "ticket_price": 0, "rating": 4.3},
        ],
        memory_context=memory_context,
    )

    print("\n[规划Agent] 正在生成行程（注入了用户记忆）...")

    # 调 LLM 生成行程
    from agents.planning import PLANNING_SYSTEM_PROMPT
    response = await planning_llm.ainvoke([
        {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    content = getattr(response, "content", str(response))

    print(f"\n助手: {content[:500]}...")

    # 验证记忆是否影响了推荐
    if memory_context and "海鲜" in memory_context:
        has_seafood = "海鲜" in content or "海" in content
        print(f"\n{'✅' if has_seafood else '⚠️'} 记忆影响验证: "
              f"{'推荐中包含海鲜相关内容' if has_seafood else '推荐中未明显体现海鲜偏好'}")

    await weather_client.close()
    print("\n" + "=" * 60)
    print("演示完成")
    print("=" * 60)


def main() -> None:
    """入口。"""
    asyncio.run(run_memory_demo())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 手动验证（需要 Docker Qdrant 运行 + 真实 LLM Key）**

```bash
# 确保 Docker 服务运行
docker compose up -d

# 运行记忆演示
python -m scripts.demo_memory
```

预期输出：
- 会话1：用户说"喜欢海鲜"→ Mem0 提取记忆
- 会话2：检索到"用户喜欢海鲜"→ 规划行程中包含海鲜相关内容

- [ ] **Step 3: Commit**

```bash
git add scripts/demo_memory.py
git commit -m "feat: add CLI memory demo for cross-session preference verification"
```

---

## Task 7：里程碑 M3 验收

- [ ] **Step 1: 全量测试 + 类型检查 + lint**

Run: `bash scripts/check.sh`
Expected: ruff 无违规 / mypy 无错误 / pytest 全部 PASS

- [ ] **Step 2: 检查覆盖率**

Run: `pytest tests/ --cov=core/memory --cov=agents --cov-report=term-missing`
Expected: core/memory 模块覆盖率 ≥ 80%

- [ ] **Step 3: 记忆演示验证**

```bash
# 需要 Docker Qdrant 运行
docker compose up -d
python -m scripts.demo_memory
```
Expected: 会话1提取记忆 → 会话2检索到偏好 → 行程推荐体现偏好

- [ ] **Step 4: 确认 Phase 1/2 测试不回归**

Run: `pytest tests/agents/test_e2e.py tests/mcp_servers/ -v`
Expected: 全部 PASS（Phase 2 的 e2e 测试在 memory_manager=None 时应正常通过）

- [ ] **Step 5: 更新总路线图进度**

在 `docs/plans/00-MASTER-ROADMAP.md` 中勾选 Phase 3 完成。

- [ ] **Step 6: 撰写里程碑记录**

在 Obsidian 知识库 `03_项目/tourswarm/日报/` 下创建 M3 验收记录。

- [ ] **Step 7: Commit 里程碑**

```bash
git add docs/
git commit -m "milestone: M3 complete — memory system with Mem0 cross-session preferences"
```

---

## 2026-07-02 执行记录

### 已完成

- Redis 短期记忆、滑动窗口摘要压缩、TTL 30 分钟。
- Mem0 长期记忆封装，支持 list 与 `{results: [...]}` 响应格式，错误时优雅降级。
- MemoryManager：Agent 回复后异步提取，规划前检索并格式化用户偏好。
- `memory_node` 已插入 LangGraph：提供 `memory_manager` 时走 `info + recommend -> memory -> planning -> budget`；未提供时保持 Phase 2 路径。
- `planning_node` 已读取 `memory_context` 并注入 `用户历史偏好` prompt section。
- Mem0 factory 已接入 Qdrant、DeepSeek，并修复 Mem0 SDK 配置字段：
  - LLM base URL 使用 `openai_base_url`。
  - 显式设置 `top_p=1.0`，避免 DeepSeek 拒绝 Mem0 默认 `top_p=0`。
  - 默认 HuggingFace embedder；可通过 `MEM0_EMBEDDER_PROVIDER=openai` 切换 OpenAI-compatible embedding。
- `scripts/demo_memory.py` 已实现两轮跨会话记忆演示；Mem0 不可用时清晰降级并退出 0。

### 验收证据

- `bash scripts/check.sh`：通过，`138 passed`。
- `pytest tests/ --cov=core.memory --cov=agents --cov-report=term-missing`：通过，总覆盖率 93%，`core/memory` 覆盖率均超过 80%。
- `pytest tests/agents/test_e2e.py tests/mcp_servers/ -v`：通过，`44 passed`。
- `docker compose up -d qdrant`：通过，`tourswarm-qdrant` 状态 `healthy`。
- `python -m scripts.demo_memory`：
  - 默认 `BAAI/bge-large-zh-v1.5`：阻塞在 HuggingFace/Xet checkpoint 下载。
  - `HF_HUB_DISABLE_XET=1 MEM0_EMBEDDER_MODEL=BAAI/bge-small-zh-v1.5`：HuggingFace 连接超时。
  - `MEM0_EMBEDDER_PROVIDER=openai MEM0_EMBEDDER_MODEL=text-embedding-3-small`：Mem0 可初始化并进入真实调用层；当前 OpenAI Proxy 无该 embedding 通道。
  - Doubao embedding 探针：当前 key/base URL 返回模型能力不支持。

### 待复验

真实跨会话 demo 还缺一个可用 embedding 通道。补充以下任一方案后重跑：

```bash
docker compose up -d qdrant
MEM0_EMBEDDER_PROVIDER=openai \
MEM0_EMBEDDER_MODEL=<embedding-model> \
MEM0_EMBEDDER_API_KEY=<embedding-api-key> \
MEM0_EMBEDDER_BASE_URL=<openai-compatible-base-url> \
MEM0_EMBEDDER_DIMS=<embedding-dimension> \
python -m scripts.demo_memory
```

预期：会话 1 写入“喜欢海鲜”偏好；会话 2 检索到该偏好；规划输出或 memory context 中可见“海鲜”。

## Phase 3 完成标准（M3 验收清单）

- [x] Redis 短期记忆：会话消息存储 + TTL 30分钟 + 滑动窗口摘要压缩
- [x] Mem0 长期记忆：跨会话用户偏好提取 + 检索 + ADD/UPDATE/DELETE/NOOP
- [x] 记忆管理器：每轮异步提取 + 规划前检索注入
- [x] memory_node 集成到 graph（planning 前插入）
- [x] planning_node 读取 memory_context 注入 prompt
- [x] Mem0 不可用时优雅降级（不阻断核心功能）
- [ ] CLI 两轮对话脚本验证跨会话记忆（脚本已实现；真实运行待可用 embedding 通道）
- [x] Phase 1/2 测试不回归
- [x] mypy strict 无错误
- [x] ruff lint 无违规
- [x] core/memory 模块覆盖率 ≥ 80%
