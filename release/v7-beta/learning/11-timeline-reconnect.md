# 11 - 持久 Timeline 与断线重连

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

> 本章目标：能讲清 SessionEventJournal 的 schema 设计、RunCoordinator 的服务端持有 run 机制、无缝重放算法（先重放后订阅无竞态）、fencing token 防幽灵写入、以及为什么"WebSocket 断开 ≠ 取消 run"。

## V6 的根本问题

V6 时期"切换会话回来工具调用消失"是根本性体验问题。根因是 run 事件有三个问题：

1. **推一次就没了**：事件推给 WebSocket 前端，前端收到就丢了。没有持久化的事件源。
2. **lease 只是内存字符串**：`active_run_id` 是内存对象，WebSocket 断线就丢，进程重启就丢。
3. **切换会话只看到 role+content**：`loadSessionMessages()` 只映射 role 和 content，丢工具调用元数据。

V6.9 的 SessionEventJournal + RunCoordinator 彻底解决了这些问题。

## SessionEventJournal：事件事实源

### SQLite schema

```sql
CREATE TABLE session_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,  -- 全局递增，重连的 cursor
    event_id TEXT NOT NULL UNIQUE,                -- 事件唯一 ID，幂等去重
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,                           -- user/assistant/model/tool/approval/...
    status TEXT NOT NULL,                         -- pending/running/done/completed/cancelled/...
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL                     -- 完整事件内容
);

CREATE UNIQUE INDEX session_events_terminal_idx ON session_events(run_id)
WHERE kind = 'terminal';                          -- 一个 run 只有一个终态

CREATE TABLE active_run_lease (
    lease_key INTEGER PRIMARY KEY CHECK (lease_key = 1),  -- 单例锁
    run_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,                                -- 进程实例 ID
    owner_pid INTEGER NOT NULL,
    owner_process_start TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,                        -- 防幽灵写入
    acquired_at TEXT NOT NULL
);
```

### 三个关键约束

**① `sequence` 全局递增**：所有事件按顺序编号。重连时 `WHERE sequence > last_seen` 增量拉取。

**② `event_id` UNIQUE**：重复写入被 `ON CONFLICT DO NOTHING` 拒绝。网络重发同一条消息不会产生重复记录。这是幂等的基础。

**③ `active_run_lease` 单例行**：`lease_key = 1` 的 CHECK 约束保证一个 session 只有一个活跃 run。带 `owner_id` + `owner_pid` + `owner_process_start` + `fencing_token`。

### terminal 唯一索引

```sql
CREATE UNIQUE INDEX session_events_terminal_idx ON session_events(run_id)
WHERE kind = 'terminal';
```

这个部分唯一索引保证**一个 run 只有一个终态事件**。如果尝试给同一个 run 写两个 terminal，第二个会被拒绝。这防止了"run 已经 completed 又被写成 cancelled"的矛盾。

## RunCoordinator：服务端持有 run

### 核心设计：WebSocket 断开 ≠ 取消

V6 的 run 绑定 WebSocket：WebSocket 断了 run 就没了。V6.9 的 RunCoordinator 把 run 解耦：

```python
class RunCoordinator:
    _active_run_id: str | None
    _active_task: asyncio.Task | None
    _active_fencing_token: int | None     # 防幽灵写入
    _subscribers: set[asyncio.Queue]      # 多 WebSocket 可同时订阅
```

**run 由服务端持有，不绑 WebSocket**。用户切换会话只改变前端订阅目标，不取消旧 session 的执行。多个 WebSocket 可以同时订阅同一个 session（比如用户在两个标签页打开同一个 session）。

### 核心方法

```python
async def start_run(run_id, event_stream) -> asyncio.Task
    # 开始消费事件流作为 session 的活跃 run

async def cancel(run_id) -> bool
    # 取消运行（不影响 subscribers）

async def subscribe(after=0) -> AsyncIterator[SessionEvent]
    # 重放历史 + 实时推送（无竞态窗口）

async def recover_interrupted_runs() -> tuple[str, ...]
    # 进程重启后恢复中断的 run
```

### fencing token 防幽灵写入

每次 `begin_run` 生成递增的 fencing_token。后续所有 `append` 都带这个 token：

```python
async def _persist(self, *, run_id, event, fencing_token):
    if event.kind == "terminal":
        stored = await asyncio.to_thread(
            self.journal.append_terminal_and_release,
            run_id=run_id,
            status=event.status,
            payload=event.payload,
            lease_owner_id=self.owner_id,
            fencing_token=fencing_token,  # 必须匹配
        )
```

**为什么需要**：假设 run A 被取消，但取消前 A 已经发起了一个工具调用，工具结果还在飞。如果没有 fencing token，这个迟到的工具结果会写入已取消的 run，导致状态矛盾。有了 fencing token，取消后 token 不匹配，迟到的写入被拒绝。

## 无缝重放算法（核心创新）

这是 V6.9 最精妙的设计。问题是：重连时怎么既不漏事件也不重复？

### 朴素方案的竞态

```python
# 朴素方案 1：先重放再订阅
events = journal.replay(after=last_seen)  # 读历史
for event in events:
    yield event
# ← 竞态窗口：这里新事件可能已经写入但还没注册 subscriber
subscribe(queue)  # 开始订阅
async for event in queue:
    yield event
```

竞态：在 `replay` 返回和 `subscribe` 注册之间，可能有新事件写入 journal 但没进 queue，漏掉了。

```python
# 朴素方案 2：先订阅再重放
subscribe(queue)
events = journal.replay(after=last_seen)
for event in events:
    yield event
async for event in queue:
    yield event
```

竞态：queue 里可能有 `replay` 已经返回的事件，重复了。

### RunCoordinator 的方案

```python
async def subscribe(self, *, after: int = 0):
    queue = asyncio.Queue(maxsize=256)

    # 1. 在 publish_lock 内注册 subscriber + 读 high_water
    async with self._publish_lock:
        high_water = await asyncio.to_thread(self.journal.latest_sequence)
        self._subscribers.add(queue)

    try:
        # 2. 重放 after 之后到 high_water 的历史
        cursor = after
        while cursor < high_water:
            page = await asyncio.to_thread(
                self.journal.replay, after=cursor, limit=500
            )
            items = tuple(item for item in page.items if item.sequence <= high_water)
            if not items:
                break
            for event in items:
                cursor = event.sequence
                yield event

        # 3. 切换到实时订阅
        while True:
            live_event = await asyncio.wait_for(
                queue.get(), timeout=self._poll_interval_seconds
            )
            if live_event.sequence <= cursor:
                continue  # 去重（重放已经 yield 过的）
            # 检查是否有 gap（queue 里跳过了某些 sequence）
            target = live_event.sequence
            if target > cursor + 1:
                # 有 gap，从 journal 补读
                while cursor < target:
                    page = await asyncio.to_thread(
                        self.journal.replay, after=cursor, limit=500
                    )
                    for repaired in page.items:
                        cursor = repaired.sequence
                        yield repaired
            cursor = live_event.sequence
            yield live_event
    finally:
        self._subscribers.discard(queue)
```

**关键**：
1. **publish_lock 原子性**：注册 subscriber 和读 high_water 在同一个锁内。这保证不会有事件在"注册之后、读 high_water 之前"写入 journal 但没进 queue。
2. **重放到 high_water**：只重放到注册时刻的 high_water，不重放之后的（之后的走 queue）。
3. **queue 去重**：queue 里可能有 `sequence <= cursor` 的事件（重放已经 yield 过的），跳过。
4. **gap 检测**：如果 queue 里跳过了某些 sequence（比如 high_water 之后又写了 3 条但 queue 只收到第 3 条），从 journal 补读。

这个算法保证**不漏事件也不重复**。

## persist-then-push 契约

每个事件必须按这个顺序：

```python
async def _persist_and_broadcast(self, event):
    # 1. 先写 SQLite（持久化）
    stored = await asyncio.to_thread(self.journal.append, ...)
    # 2. 再 broadcast 到 subscribers
    self._broadcast(stored)
    # 3. 再推 WebSocket（由调用方做）
```

**为什么这个顺序**：如果先推 WebSocket 再持久化，推成功但持久化失败时，前端看到了事件但服务器没记录。断线重连时重放不到这个事件，前端会出现"事件消失"的幻觉。

persist-then-push 保证：**推失败只让客户端暂时落后，不丢事件**。客户端重连时从 `last_sequence` 重放，一定能看到所有已持久化的事件。

## recover_interrupted_runs

进程重启后，上一个进程的 lease 还在数据库里。这个方法处理：

```python
async def recover_interrupted_runs(self):
    recovered = []

    # 1. 恢复 lease（上一个进程的 lease 标记为 interrupted）
    async with self._publish_lock:
        lease_event = await asyncio.to_thread(
            self.journal.recover_run_lease,
            recovery_owner_id=self.owner_id,
        )
        if lease_event is not None:
            self._broadcast(lease_event)
            recovered.append(lease_event.run_id)

    # 2. 把所有未完成的 run 标记为 interrupted
    run_ids = await asyncio.to_thread(self.journal.unfinished_run_ids)
    for run_id in run_ids:
        event = RunEvent(
            kind="terminal",
            status="interrupted",  # retryable
            payload={"event": "run_interrupted", "retryable": True},
        )
        await self._persist(run_id=run_id, event=event)
        recovered.append(run_id)

    return tuple(recovered)
```

**关键**：
- **不尝试恢复执行**。中断的 run 不继续跑，只标记 `interrupted`。
- **不伪造自动恢复成功**。用户看到"run 已中断，可重试"，不是"run 已恢复"。
- `interrupted` 是 `retryable=True`，用户可以手动重新发起。

## 为什么 V6 切换会话丢工具调用

V6 的 `loadSessionMessages()` 只映射 role + content：

```typescript
// V6 的前端代码
async function loadSessionMessages(targetSessionId) {
    const res = await fetchCodingSessionMessages(targetSessionId)
    return res.messages.map((message) => ({
        role: message.role,        // 只取 role
        content: message.content,  // 只取 content
    }))
}
```

后端 `session_store.messages()` 也只返回 user/assistant 角色，过滤掉 tool 消息。所以切换会话回来只看到纯文本对话，工具调用过程全部消失。

V6.9 的修复：
- 后端 timeline API 返回完整事件（含 tool 事件）
- 前端 `codingTimeline.ts` 把事件投影成 `TimelineTurn`（含 tools/approvals/model events）
- `CodingMessageTurn` 统一渲染 user/assistant/tool/approval

## 事件有三个受众

一个 RunEvent 会被写到三个地方：

| 受众 | 存储 | 用途 | 字段 |
| --- | --- | --- | --- |
| 机器调试 | `trace.jsonl` | 开发/benchmark | 完整字段 + 调试元数据 |
| 用户审计 | `timeline.sqlite3` | replay/刷新 | 完整事件 JSON |
| 实时 UI | WebSocket | 前端增量更新 | 同 timeline |

**`text_delta` 只推 WebSocket 不写 trace**（避免 trace 膨胀）。其他事件三个受众都写。

## 外部参考的使用边界

CLI、桌面与 Web 产品面对的连接生命周期不同，不能用 UI 观察推断外部系统是否存在
持久化或 fencing。Sage 采用 durable timeline 是自身 Web 运行模型的要求；可靠性应由
重放竞态、重复 terminal、进程恢复和 lease ownership 测试证明。

## 第一入口

按顺序打开：

1. `core/coding/persistence/session_event_journal.py::SessionEventJournal` - 事件 journal
2. `core/coding/run_coordinator.py::RunCoordinator` - 运行协调器
3. `core/coding/run_coordinator.py::RunCoordinator.subscribe` - 无缝重放
4. `core/coding/run_coordinator.py::RunCoordinator.recover_interrupted_runs` - 重启恢复
5. `api/coding_runs.py::CodingRunRegistry` - 应用级注册表
6. `frontend/src/stores/codingTimeline.ts` - 前端 timeline 投影
7. `frontend/src/router/index.ts` - URL 深链接与 session route

## 测试证据

- `tests/core/coding/test_session_event_journal.py` - journal schema + 幂等
- `tests/core/coding/test_run_coordinator.py` - coordinator + fencing token
- `tests/core/coding/test_runtime_run_lifecycle.py` - run 生命周期
- `tests/api/test_coding_timeline_routes.py` - timeline REST API
- `frontend/src/stores/codingTimeline.test.ts` - 前端投影

## 当前边界

> [!warning] 持久 Timeline 有几个已知边界
> - 审批的 LangGraph durable interrupt 未实现（同进程 ApprovalManager 兜底，服务器重启丢 pending approval）
> - 长会话（1000+ 事件）的 timeline 全量加载性能未优化（当前分页 500/次）
> - fencing token 在单进程内递增，多进程部署需要数据库序列
> - `recover_interrupted_runs` 只标记 interrupted，不尝试恢复执行（设计如此）
> - 旧 session（V6 之前）没有 timeline.sqlite3，需要兼容回退到 trace.jsonl

## 自测

1. V6 切换会话丢工具调用的根因是什么？V6.9 怎么解决的？
2. SessionEventJournal 的三个关键约束（sequence/event_id/lease）各自解决什么问题？
3. 无缝重放算法怎么保证不漏事件也不重复？publish_lock 的作用？
4. fencing token 防什么攻击？如果不带 token 会怎样？
5. persist-then-push 契约为什么必须先持久化再推送？
6. `recover_interrupted_runs` 为什么不尝试恢复执行，只标记 interrupted？
7. 为什么 terminal 事件要有唯一索引？

下一章：[安全审计与防注入](12-security-audit.md)
