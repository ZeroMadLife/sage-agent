# 长短记忆与 Dream：长期事实必须经过提案与批准

> Last verified against: `codex/harness-evidence-v2@0e21fda` (2026-07-25)

记忆系统最重要的能力不是“记得多”，而是区分临时线索、已批准事实和模型提案，避免一次幻觉长期污染后续任务。

![记忆必须可回滚](assets/08-memory-dream.png)

## 三种记忆有三种信任等级

| 层 | 生命周期 | 来源 | 写入语义 |
| --- | --- | --- | --- |
| Working Memory | 当前 run | Session 历史与运行状态 | 每次重建，不持久化 |
| Durable Memory | 跨 session、workspace scoped | 显式 remember 或已批准 proposal | SQLite canonical + 文件投影 |
| Dream Proposal | pending 到 approved/rejected | 模型或 Harness 候选 | 批准前不得成为事实 |

Memory 也不是 Context Summary 或 Knowledge Wiki。

Summary 服务当前任务交接，Knowledge 保存可引用知识，Memory 保存与用户工作方式相关的长期事实。

## 第一层：Working Memory 是运行投影

`WorkingMemory.from_session` 反向扫描历史，提取最近任务、最后错误和最近文件。

它还携带 permission mode、plan mode 与当前上下文预算。

渲染结果只在本轮作为 `<working-memory>` 注入，不写回 Transcript。

当前实现仍有两个明确边界：

- `task_summary` 取历史中最近用户消息，调用时机不当会落后一轮；
- `recent_files.hash` 仍为空，不能据此证明文件内容未变化。

因此 Working Memory 是便捷线索，不是新鲜度或事实证明。

## 第二层：Durable Memory 保存用户拥有的事实

Durable Memory 以规范 workspace path 的摘要作为 workspace id。

不同 worktree 或目录移动可能产生不同 id，这是当前身份模型的限制。

文件层包含 daily log、topic JSONL 和可重建的 `MEMORY.md` 索引。

`MemoryManager.remember` 先进入 SQLite proposal/fact/event 审计链，再写入文件投影，并记录
topic、source 与 source_ref。直接使用底层 `DurableMemory` 仍属于兼容投影路径，不是 Harness
长期事实入口。

它代表显式记忆意图，但仍经过工具审批；自动模式不会隐式批准。

Context 注入目前从 `MEMORY.md` 开头按字符预算截取，不是完整相关性召回。

## 第三层：MemoryStore 管理 proposal 状态机

SQLite `MemoryStore` 保存 proposal、批准后的 fact 记录与 memory event。

```mermaid
stateDiagram-v2
    [*] --> pending: create proposal
    pending --> approved: approve + revision check
    pending --> rejected: reject + revision check
    approved --> projected: durable file projection complete
    rejected --> [*]
    projected --> [*]
```

Proposal 创建时记录 `base_revision`，实际等于当时已批准事实数量。

批准前会比较当前事实数量，基线过期就拒绝，避免并发提案建立在旧状态上。

Proposal 自身也有 revision，重复同一 transition 幂等返回，冲突 transition 则 fail closed。

批准与 SQLite fact/event 写入在一个事务内；Markdown 投影失败时保留 `projection_status=pending`，重启后重放。

schema v2 还为 fact 增加 `active / superseded / retracted`、revision、更新时间、替代关系和
撤回原因。Proposal CAS 与 Fact CAS 分开：前者保护审批，后者保护已批准事实的撤回。

## Dream 的真实边界：proposal-only 已实现，反思质量链仍有限

`dream` 工具不会直接写长期记忆。

它调用 `MemoryManager.propose_dream`，把候选持久化为 pending proposal，并发出 proposal-ready 事件。

用户通过 API 批准或拒绝后，批准内容才进入 SQLite facts 与文件投影。

当前 Legacy `DurableMemory.propose_dream` 主要把 lifecycle-filtered active facts 复制成
proposed 状态，并非完整的独立 Reflection Agent。

Harness 的 memory adapter 可以提交带 run/reflection provenance 的候选，但同样无权自行批准。

所以本章可以确认的是“提案不能自动变事实”，不能宣称复杂的多证据反思策略已完整上线。

## 批准后仍要区分 canonical 与 projection

MemoryStore 的 approved fact 是 proposal 工作流的事务记录。

DurableMemory 文件是当前模型读取和人类查看的投影。

投影成功后标记 `projection_status=complete`。

如果进程在事务提交后、文件写入前崩溃，启动时 `_replay_projections` 会补写。

这个顺序避免“文件写了一半但数据库认为未批准”的分裂状态。

文件层保持 append-only，不删除 superseded 或 retracted 的历史行。召回时以 SQLite 全状态
集合屏蔽旧投影，避免被撤回内容从 Markdown 重新进入模型。

## 召回必须把事实当数据

Harness memory query 只返回 approved memory 与可归因的 episodic references，并受每来源 token budget 限制。

相互冲突的事实可以同时返回并标记 conflict，而不是静默选一个覆盖另一个。

Durable context middleware 会把 memory reference 放在不可信数据边界中。

这是必要的：用户批准某段文本成为“事实”，不代表其中的句子获得 system instruction 权限。

Memory 可以影响推理，但不能改写当前用户请求或安全策略。

## 更正与撤回：保留历史，不让旧事实继续召回

更正不会原地覆盖旧事实。系统创建带 `supersedes_content_hash` 的 pending proposal；用户批准
后，新事实与旧事实的 `superseded` 状态在同一事务内落库。撤回则要求 expected fact revision，
保存 reason、actor 与 append-only fact event。

Consolidation 首版接收已有 evidence refs 的 episodic evidence，执行精确去重和证据门禁，
只生成 pending proposal。它不自动批准，也不会在没有显式 target 时推断语义替代。

40 条确定性 lifecycle 场景覆盖 proposal 隔离、撤回、替代、consolidation 和重启/作用域，
结果为 40/40。这个数字证明状态机不变量，不是自然对话事实抽取准确率。

## 为什么不是最小聊天摘要文件

最小记忆常在每轮结束后把模型摘要追加到一个 Markdown 文件。

它没有提案状态、用户批准、并发基线和 provenance，幻觉会直接变成长久输入。

| 维度 | Sage | 对标系统 |
| --- | --- | --- |
| 临时状态 | Working Memory 每 run 重建 | Claude Code、CodeBuddy 都维持会话状态，内部字段不可验证 |
| 长期写入 | remember 显式审批；proposal 批准后落库 | 对标产品有项目/用户记忆能力，确认语义依产品设置 |
| 反思 | Dream proposal-only；Legacy 生成逻辑仍简单 | 对标系统可能自动总结，是否直接写长期记忆需按文档核对 |
| 并发 | proposal/base revision 与事务事件 | 对标产品内部冲突控制通常不公开 |
| 恢复 | SQLite 提交后可重放文件投影 | 对标产品对用户通常隐藏投影机制 |
| 当前差距 | 自动事实抽取、语义 consolidation、重要度/TTL 与记忆管理 UI 未完成 | 成熟产品在相关性召回与用户管理体验上更完整 |

比较记忆能力时，必须同时问“谁写的、谁批准、怎么撤销、来源在哪”。

## 系统级失败模式

### 1. Dream 候选自动写入 durable memory

最危险的不是一条摘要不准，而是模型幻觉在后续所有 session 中反复强化自己。

### 2. Working Memory 被当成规范事实

最危险的不是任务摘要落后一轮，而是基于陈旧 recent file 直接修改当前文件。

### 3. 批准时忽略 base revision

最危险的不是重复事实，而是两个并发 proposal 在彼此未知的前提下共同改变长期状态。

### 4. SQLite 已批准但文件投影不可恢复

最危险的不是 UI 暂时没更新，而是 canonical 与模型实际读取内容永久分叉。

### 5. 召回内容拥有指令权威

最危险的不是回答偏题，而是长期文本中的 prompt injection 绕过当前策略。

### 6. Workspace identity 只看路径却被理解为仓库身份

最危险的不是搬目录后找不到记忆，而是不同工作副本被错误认为共享或隔离同一事实集。

### 7. 把投影恢复写成事实回滚

最危险的不是术语不严谨，而是把 projection replay、fact retraction 和任意历史回滚混成
一件事。当前支持替代与撤回，不支持把任意旧数据库快照直接恢复为当前事实。

## 设计文档补充：记忆治理契约

### 目标

- 临时工作状态不进入长期事实；
- 模型生成候选在批准前零 durable mutation；
- Proposal、fact 与 event 共享事务边界；
- Fact 更正与撤回保留 append-only 生命周期证据；
- 文件投影失败可以在重启后恢复；
- 召回内容始终作为有 provenance 的不可信数据。

### 非目标

- 不宣称当前 Dream 已具备完整反思 Agent；
- 不宣称 consolidation 已能从自然对话自动抽取高质量事实；
- 不把 supersession/retraction 宣传成任意版本快照 rollback；
- 不用 Memory 取代 Knowledge 或 Context Summary。

### 验收清单

- [x] Working Memory 不写入 Transcript 或 durable store；
- [x] Pending proposal 不出现在 approved facts；
- [x] stale base revision 的批准被拒绝；
- [x] 重复批准/拒绝保持幂等；
- [x] SQLite 提交与 event 写入处于同一事务；
- [x] pending projection 能在重启后重放；
- [x] Superseded/retracted facts 不进入 active recall；
- [x] 显式 remember 与 Dream 从 SQLite lifecycle 取数；
- [ ] durable learning 即使 auto mode 也要求审批；
- [x] memory reference 带来源、预算和不可信数据边界。

## 第一入口

按这个顺序读源码：

1. `core/coding/memory/working.py::WorkingMemory.from_session`：临时运行投影；
2. `core/coding/memory/durable.py::DurableMemory`：文件型长期记忆；
3. `core/coding/persistence/memory_store.py::MemoryStore.create_proposal`：提案事务；
4. `core/coding/persistence/memory_store.py::MemoryStore._transition`：批准、替代与 revision；
5. `core/coding/persistence/memory_store.py::MemoryStore.retract_fact`：事实 CAS 撤回；
6. `core/coding/memory/consolidation.py::consolidate_evidence`：证据门禁与精确去重；
7. `core/coding/memory/manager.py::MemoryManager.approve`：事务到文件投影；
8. `core/coding/tools/memory_tools.py::dream`：proposal-only 工具入口；
9. `core/harness/memory_adapter.py::CodingMemoryPort`：Harness 召回与提案适配。

验证证据集中在 `test_memory.py`、`test_memory_store.py`、`test_memory_adapter.py` 与 memory proposal API 测试。

## 面试里可以这样收束

Sage 把 Working Memory、Durable Memory 和 Dream Proposal 分成不同信任层：运行线索每轮
重建，显式 remember 进入 SQLite 审计链，模型反思和 consolidation 只能先生成 proposal。
已批准事实可以通过 CAS 撤回或经新 proposal 替代，旧投影保留但不会继续召回；40 条确定性
场景验证了生命周期不变量，同时自动事实抽取与语义 consolidation 仍是后续工作。

下一章：[Knowledge 与 RAG 检索：知识必须可验证](09-knowledge-rag-retrieval.md)
