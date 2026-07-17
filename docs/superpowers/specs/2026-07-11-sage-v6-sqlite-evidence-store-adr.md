# Sage V6 SQLite 证据存储架构决策

> 状态：已批准
> 日期：2026-07-11
> 适用范围：V6.6 Context、V6.7 Memory、V6.8 Dream 的工作区级持久状态

## 1. 决策

Sage 使用 SQLite 作为每个隔离工作区的 canonical transcript 和 harness 状态库。JSONL 不再承担在线事务、并发判重或崩溃恢复，只作为审计导出和人工排查格式。

大型工具结果继续保存为独立 artifact 文件。SQLite 仅保存 artifact 引用、来源和摘要，不把大文件正文写入 transcript 表。

## 2. 原因

原 JSONL 方案需要自行实现跨进程锁、唯一性、异常重试、文件和目录 `fsync`、坏尾恢复、软硬链接防护以及未确认持久状态。连续故障注入证明这些逻辑已经接近一个不完整的数据库实现，不适合作为 Sage 的长期基础设施。

SQLite 提供：

- 事务提交与崩溃恢复；
- `UNIQUE` 约束保证 `message_id` 幂等；
- 多进程并发控制和 `busy_timeout`；
- WAL 模式下的读写并发；
- 标准备份、迁移和一致性检查能力；
- Python 标准库支持，不增加运行时依赖。

## 3. 工作区数据库

每个 Sage 工作区拥有独立状态目录和数据库：

```text
<state-root>/evidence/<session-id>/transcript.sqlite3
<state-root>/evidence/<session-id>/exports/transcript.jsonl
<state-root>/evidence/<session-id>/runs/<run-id>/tool-results/<call-id>.txt
```

`state-root` 必须由 Sage 服务端管理，并与模型可操作的代码工作区分离。工具层不得获得该目录的 shell、文件或 MCP 写权限。

数据库文件、WAL、SHM、JSONL 导出和 tool artifact 使用私有权限。路径 ID 继续拒绝空值、`.`、`..` 以及正反斜线。

## 4. Transcript 表

```sql
CREATE TABLE transcript_items (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    call_id TEXT NOT NULL DEFAULT '',
    artifact_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
```

读取顺序固定为 `sequence ASC`。`append()` 使用事务内的 `INSERT ... ON CONFLICT(message_id) DO NOTHING`，以实际插入行数返回 `True` 或 `False`。

数据库初始化和连接策略：

```text
PRAGMA journal_mode = WAL
PRAGMA synchronous = FULL
PRAGMA foreign_keys = ON
PRAGMA busy_timeout = 5000
```

每次写操作使用短事务，不跨模型调用、工具执行或网络请求持有数据库事务。

## 5. JSONL 导出

JSONL 是数据库快照的派生产物，不是第二写入源：

1. 在 SQLite 一致性读取事务中按 `sequence` 导出；
2. 写入同目录唯一临时文件；
3. `flush`、文件同步、私有权限后原子替换目标文件；
4. 导出失败不得影响 canonical transcript；
5. 禁止从 JSONL 自动覆盖已有 SQLite 数据库。

旧版 JSONL 仅允许一次性显式迁移。迁移必须使用 `message_id` 唯一约束，成功提交后保留原文件作为只读备份，并记录迁移版本。

## 6. 云端边界

SQLite 的职责是单个隔离工作区或单个 Agent sandbox 的本地状态。V7 可以让每个云工作区挂载自己的 SQLite 状态卷，但不得让多个应用实例通过网络文件系统共享同一个 SQLite 文件。

以下数据进入中心 PostgreSQL：

- 用户、组织、权限、计费和配额；
- 工作区目录、运行索引和跨工作区检索元数据；
- HR 问答、企业知识库和需要多实例访问的 RAG 文档；
- pgvector 向量、租户过滤字段和审计索引。

SQLite 可以承担本地 RAG 缓存、离线索引和单工作区 chunk metadata；PostgreSQL/pgvector 承担线上多租户检索。两者通过明确同步任务连接，不共享事务假象。

## 7. 验收条件

- 两个进程并发插入同一个 `message_id`，数据库中只有一行；
- 未提交事务在进程退出后不可见，已提交事务在重启后可读；
- SQLite busy/locked 错误有明确超时，不无限等待；
- schema 版本可读取，并支持向前迁移；
- JSONL 导出顺序稳定、Unicode 无损、失败不修改数据库；
- DB、WAL、SHM、导出和 artifact 不可通过 symlink 逃逸；
- `PRAGMA integrity_check` 返回 `ok`；
- Runtime 和 Context 只依赖 `TranscriptStore` 公共接口，不直接执行 SQL。

## 8. 后续版本

- V6.6：SQLite canonical transcript、tool artifact 引用、JSONL 手动导出。
- V6.7：同库增加 revisioned memory 表或独立 memory SQLite，先保持模块边界。
- V6.8：Dream proposal 使用事务状态机，审批与 memory revision 原子提交。
- V7：每云工作区独立状态卷；中心 PostgreSQL 管理租户和运行索引。
- V8：Local Companion 使用本地 SQLite；Code RAG/知识图谱根据规模选择 SQLite cache 与 PostgreSQL/pgvector 中心索引。
