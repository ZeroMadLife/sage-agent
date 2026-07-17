# Sage V7.5.4A Source Connector Foundation 设计

> 日期：2026-07-16
> 集成分支：`dev/sage-v7`
> 前置版本：`9722954`、`6858518`
> 状态：待用户审阅

## 1. 目标

V7.5.4A 在不改变现有 Obsidian 使用体验的前提下，将本地目录扫描和读取能力收进统一的 `KnowledgeSourceAdapter` 契约，为 GitHub、飞书和显式保存的网页来源建立稳定接入边界。

本版本只完成 **Adapter 契约、Registry、Filesystem/Obsidian 兼容迁移、持久化扫描状态和契约测试**。不接入真实 GitHub/飞书凭据，不修改主 Chat Harness，也不重写 V7.5.3 已验证的 manifest、sync plan、durable job、Wiki、Index 或 Graph 状态机。

```text
Configured Source Root
  -> KnowledgeSourceRegistry
  -> KnowledgeSourceAdapter.scan()
  -> deterministic SourceDescriptor[]
  -> V7.5.3 manifest + sync plan
  -> durable job
  -> KnowledgeSourceAdapter.fetch(expected_revision)
  -> immutable artifact
  -> existing parse / proposal / Wiki / Index / Graph pipeline
  -> adapter acknowledgement + committed checkpoint
```

## 2. 当前问题

当前能力已经能够安全扫描和读取白名单本地目录，但来源访问仍散落在三个位置：

- `core/knowledge/jobs/scanner.py` 直接从 `KnowledgeStore.source_roots` 获取绝对路径并递归扫描；
- `KnowledgeJobService` 直接调用 scanner、`store.load_source()` 和本地 revision 复核；
- `KnowledgeStore` 同时承担来源读取、canonical knowledge、Git Wiki 和投影职责。

这种结构对 Obsidian 足够，但接入 GitHub commit、飞书 document revision 或分页 API 时会出现以下问题：

1. 远端来源没有本地 `Path`，无法复用直接文件读取；
2. provider cursor、remote revision 与 Sage manifest watermark 容易混用；
3. 网络重试、限流和分页恢复可能污染已提交 manifest；
4. Chat Harness 可能被迫感知来源实现细节；
5. 后续每加一个来源都会修改 Job Service 和 Store 主流程。

## 3. 设计原则

1. **Canonical truth 不变**：Raw/Wiki Git revision 与数据库审计记录继续是事实来源；检索和图谱仍可重建。
2. **Adapter 只负责外部 IO**：扫描、读取、revision 校验和确认；不负责 Wiki 综合、审批、索引或图谱。
3. **状态语义分离**：Sage watermark、provider checkpoint、resume cursor 是三个不同概念。
4. **渐进迁移**：先用 FilesystemAdapter 复现当前行为，通过全部旧测试后再删除直接调用。
5. **浏览器无凭据**：浏览器只看到 `source_root_id`、kind、label 和安全状态，不看到绝对路径、token、cursor 原文或 provider 错误正文。
6. **主 Chat 只消费检索契约**：`POST /api/v1/knowledge/search` 保持稳定，不让 Harness 依赖 Adapter。

## 4. 核心契约

### 4.1 领域类型

```python
@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    source_key: str
    source_revision: str
    media_type: str
    size_bytes: int | None
    modified_at: datetime | None

@dataclass(frozen=True, slots=True)
class SourceScanPage:
    items: tuple[SourceDescriptor, ...]
    next_cursor: str | None
    target_checkpoint: str | None
    complete: bool

@dataclass(frozen=True, slots=True)
class ImmutableSourceArtifact:
    source_key: str
    source_revision: str
    media_type: str
    content: bytes
    metadata: Mapping[str, str]

class KnowledgeSourceAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    async def scan(
        self,
        source: KnowledgeSourceRoot,
        scope: str,
        *,
        checkpoint: str | None,
        cursor: str | None,
        limit: int,
    ) -> SourceScanPage: ...

    async def fetch(
        self,
        source: KnowledgeSourceRoot,
        descriptor: SourceDescriptor,
    ) -> ImmutableSourceArtifact: ...

    async def acknowledge(
        self,
        source: KnowledgeSourceRoot,
        descriptor: SourceDescriptor,
        outcome: Literal["completed", "skipped", "retracted"],
    ) -> None: ...
```

`acknowledge()` 对 FilesystemAdapter 为无操作；远端 Adapter 可用它记录消费水位，但失败不得回滚 Sage 已完成的 canonical commit。Adapter 不接受自由绝对路径，只接受服务端已配置的 `KnowledgeSourceRoot` 和规范化相对 scope/source key。

### 4.2 Registry

`KnowledgeSourceRegistry` 按 `source.kind` 映射 Adapter：

```text
obsidian -> FilesystemAdapter
markdown -> FilesystemAdapter
github   -> 未配置时 fail closed
feishu   -> 未配置时 fail closed
```

未知 kind、重复注册、adapter version 缺失或 provider 未配置必须在服务启动/prepare 阶段产生明确错误，不允许静默退回本地文件读取。

### 4.3 三类状态

| 状态 | 含义 | 何时推进 |
| --- | --- | --- |
| `watermark` | Sage 已原子提交的 manifest 版本 | 整批 job 成功后 |
| `adapter_checkpoint` | provider 已稳定观察到的远端版本 | 与 manifest 同一事务提交 |
| `resume_cursor` | 当前分页扫描的中间位置 | 每页保存；成功/放弃后清空 |

扫描失败只能更新诊断状态和 `resume_cursor`，不得推进 watermark、checkpoint 或 manifest。重新启动后可以从 cursor 续扫；如果 Adapter 声明 cursor 已失效，服务清空 cursor 并从最后 committed checkpoint 重新扫描。

## 5. Filesystem/Obsidian 兼容实现

`FilesystemAdapter` 复用现有安全约束：

- 只访问配置好的 root；
- 相对路径规范化并拒绝 `..`、反斜杠和绝对路径；
- root 与子路径中的符号链接均拒绝；
- 支持 `.md/.markdown/.html/.htm/.xhtml/.pdf`；
- 单文件最大 20 MiB，单次最多 10,000 个文件；
- 按相对路径排序并以 SHA-256 作为 revision；
- fetch 时重新计算 revision，不一致返回 `source_conflict`；
- 绝对路径永不进入 API、数据库诊断、timeline 或日志。

本地扫描首版为单页：`next_cursor=None`、`complete=True`。接口仍保留分页语义，以便未来 GitHub/飞书 Adapter 在不修改 Job Service 的情况下接入。

## 6. 持久化设计

### 6.1 `knowledge_source_sync` 扩展

新增：

- `adapter_id VARCHAR(64)`；
- `adapter_version VARCHAR(64)`；
- `adapter_checkpoint TEXT NULL`；
- `resume_cursor TEXT NULL`；
- `scan_status VARCHAR(24)`：`idle/scanning/failed`；
- `last_error_code VARCHAR(64) NULL`；
- `last_error_message VARCHAR(500) NULL`；
- `last_scan_started_at TIMESTAMPTZ NULL`；
- `last_scan_completed_at TIMESTAMPTZ NULL`。

迁移必须兼容现有 SQLite/PostgreSQL 数据库：旧 source 默认映射到 `filesystem@1.0.0`，watermark 与 manifest hash 原样保留，不触发全量重摄取。

### 6.2 `knowledge_sync_plans` 扩展

计划必须绑定：

- `adapter_id`、`adapter_version`；
- `base_checkpoint`、`target_checkpoint`；
- 现有 `base_watermark`、`manifest_hash` 和 changes。

执行阶段发现 Adapter version、source scope、checkpoint 或 revision 不匹配时返回 `409 source_conflict`，不得偷偷重新扫描并替换用户已经预览的计划。

### 6.3 错误码

内部错误统一映射为有限集合：

- `source_not_configured`
- `source_scope_invalid`
- `source_unavailable`
- `source_rate_limited`
- `source_cursor_expired`
- `source_conflict`
- `source_too_large`
- `source_unsupported`

API/前端只展示安全中文说明和错误码。provider URL、绝对路径、token、响应正文和原始异常不对浏览器返回。

## 7. 服务迁移顺序

### Slice A：契约与兼容 Adapter

1. 新增 `core/knowledge/sources/`，包含 types、protocol、registry、filesystem；
2. 用共享 contract tests 固定扫描排序、revision、fetch 校验和错误语义；
3. 保留 `scanner.py` 的兼容包装，内部转调 FilesystemAdapter，避免一次性修改所有调用者。

### Slice B：Job Service 接线

1. `KnowledgeJobService` 注入 Registry；
2. `preview_sync()` 经 Adapter 分页扫描，产出与 V7.5.3 相同的 `ScannedKnowledgeFile`；
3. worker 经 Adapter fetch，并把 immutable artifact 交给 Store 的新窄入口；
4. job 成功时原子提交 manifest、watermark 和 adapter checkpoint；
5. adapter ack 在 canonical commit 后执行，失败只写告警，不重复 Wiki 投影。

### Slice C：单文件入口与状态

1. `POST /api/v1/knowledge/ingest` 也经 Registry fetch；
2. `GET /api/v1/knowledge` 的 source summary 增加安全状态字段；
3. Knowledge 页面只显示“已连接/扫描中/需重试”和最后成功时间；
4. 不增加凭据录入、任意路径输入或 provider 配置页面。

## 8. 与 Chat Harness 的并行边界

### Knowledge 线独占

- `core/knowledge/sources/**`
- `core/knowledge/jobs/**`
- `core/knowledge/store.py` 中 adapter artifact 窄入口
- `api/knowledge.py` 与 Knowledge schema
- `frontend/src/api/knowledge.ts`
- `frontend/src/types/api.ts` 中 Knowledge 类型
- `frontend/src/views/KnowledgeView.vue`

### Chat Harness 线独占

- `frontend/src/harness/**`
- `frontend/src/components/harness/**`
- `frontend/src/components/coding/chat/**`
- `frontend/src/views/CodingView.vue`
- Coding session/timeline/store
- 主对话的检索判断、上下文组装和 citation 展示

### 冻结公共契约

- Chat Harness 只调用 `POST /api/v1/knowledge/search`；
- 返回结果继续包含 bounded evidence、citation ID、source/page revision 与 token budget；
- Harness 不调用 Adapter、不读取 source cursor、不修改 manifest；
- Knowledge 线不修改主 Chat 事件类型、Composer 或 Coding Store；
- 如需修改 `frontend/src/types/api.ts`，由 Knowledge 线提交，Harness 线只消费；
- 两个会话提交前先同步 Git index，精确暂存各自文件。

## 9. 安全与故障处理

1. Source config 和凭据只存在于服务端配置/Secret；数据库只保存 browser-safe 元数据和 opaque checkpoint。
2. Adapter 返回的名称、正文和 metadata 均视为不可信输入，进入 parser 前继续执行大小、类型、路径和解析策略校验。
3. 同一 source preview/execute 继续使用 V7.5.3 keyed lock、数据库行锁和 plan 唯一约束。
4. 进程在分页中断时保留 cursor；在 fetch/parse 中断时继续使用现有 lease/retry/dead-letter 恢复。
5. Adapter timeout 或限流不得产生删除 diff。只有一次完整 scan `complete=True` 才能判断缺失来源并生成 deletion tombstone。
6. Provider 暂时不可用时保留旧 Wiki/Index/Graph，不把“无法观察”解释为“来源已删除”。

## 10. 测试与验收

### Contract tests

- Registry 对 kind 的确定性路由与 fail-closed；
- Filesystem scan 排序、扩展名、最大文件数和大小限制；
- 绝对路径、`..`、反斜杠、文件/目录符号链接拒绝；
- fetch revision 匹配与冲突；
- opaque cursor/checkpoint 不泄漏到 browser-safe schema。

### Service/repository tests

- 旧数据库 migration 保留 watermark/manifest；
- adapter version/checkpoint 绑定 sync plan；
- 分页扫描重启续跑；
- cursor 过期从 committed checkpoint 重扫；
- 中途失败不产生 deletion、不推进 manifest；
- 并发 preview/execute 只形成一个有效 job；
- fetch revision 漂移返回 conflict；
- canonical commit 成功但 ack 失败不重复 Wiki revision；
- 不同 source root 的 cursor/checkpoint 严格隔离。

### 回归与真实演练

- 后端全量 pytest、ruff、受影响模块 mypy、`git diff --check`；
- 前端全量测试、`vue-tsc` 和 Vite build；
- 真实 `sage-learning` 首次扫描结果与当前 manifest 一致；
- 无变化复扫为 0 项；新增一个测试 Markdown 只产生 1 个 added；删除后只产生 1 个 tombstone；
- 服务重启后状态恢复，旧 Wiki、Index、Graph 仍可查询。

## 11. 非目标

- 真实 GitHub App、installation token、clone 或 webhook；
- 真实飞书应用、文档/知识库 API 或 cc-connect 修改；
- 文件系统 watcher、定时自动扫描或自动 push Knowledge Repository；
- 浏览器上传、任意服务器路径、MinerU/Vision 新能力；
- 图谱布局、社区折叠和 Citation Dock；
- 主 Chat Harness、Agent 决策、Memory/Dream 或公开 HR Agent；
- Kubernetes 和生产多租户部署。

## 12. 后续版本

1. **V7.5.4B Source Status UX**：连接状态、最后同步、错误恢复和手动重试。
2. **V7.5.4C GitHub Source**：使用 GitHub App/短期 installation token，身份 OAuth scope 不扩张。
3. **V7.5.4D Feishu Source**：只读文档/Wiki snapshot；开发机器人仍由 cc-connect 独立负责。
4. **V7.5.5 Graph UX**：社区折叠、布局保存、详情联动和大图谱性能。
5. **V7.6 Server Canary**：Docker Compose、HTTPS、后台 worker、备份和恢复。

服务器 canary 的最佳时间点是 V7.5.4A 与 Chat Harness citation 检索联调都通过之后。此时本地来源、对话消费和持久任务三条链路完整，部署才能验证真实产品闭环，而不只是展示页面。
