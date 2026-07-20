# 09 - Knowledge Platform

> Last verified against: `dev/sage-v7@23a0090` (2026-07-20)

> 本章目标：沿一条真实本地来源链路理解 source、parse artifact、proposal、page revision、
> index 和 citation，并区分当前 SQLite baseline 与未来生产检索后端。

## Knowledge 解决的不是“多存一些文本”

Sage 需要让一条回答可以回到个人材料，也需要阻止模型把未经验证的总结直接变成长期
事实。因此 Knowledge 的核心不是向量数据库，而是三条边界：

1. **来源边界**：只能读取服务端明确配置的 source root，保存不可变 revision。
2. **写入边界**：来源解析先形成 proposal，经 policy/approval 后才能投影为 Wiki 页面。
3. **引用边界**：检索结果携带 page/source revision 和 content hash，可以回到实际片段。

## 当前事实层

```text
configured KnowledgeSourceRoot
  -> immutable SourceDescriptor / source_revision
  -> parsed document + parse artifact
  -> KnowledgeProposal
  -> policy decision / user approval
  -> KnowledgePageRevision + Git wiki files
  -> LocalKnowledgeIndex
  -> KnowledgeSearchHit + citation_id
```

| 对象 | 主要职责 | 关键字段 |
| --- | --- | --- |
| `KnowledgeSourceRoot` | 限定可读取的文件系统根目录 | `root_id`、`kind`、`path` |
| `SourceDescriptor` | 描述一次不可变来源版本 | `source_key`、`source_revision`、`media_type` |
| `KnowledgeProposal` | 保存待审阅的 Wiki 变化 | `base_page_revision`、`target_path`、`status` |
| `KnowledgePageRevision` | 保存已投影页面版本 | `revision_id`、`content_hash`、`git_commit` |
| `KnowledgeSearchHit` | 暴露检索片段与稳定引用 | `citation_id`、rank、chunk provenance |

不要把 source proposal 与 Wiki proposal 混为一谈：前者决定某份外部材料能否进入受控
来源根，后者决定解析后的内容能否改变长期 Wiki。

## Git-backed Wiki 与 SQLite 元数据

Knowledge workspace 初始化时创建：

```text
knowledge-workspace/
├── purpose.md
├── schema.md
├── overview.md
├── index.md
├── log.md
├── raw/
│   └── sources/
└── pages/
```

- `purpose.md` 保存学习目标，并通过 Git 记录变更。
- `raw/sources/` 保存按 digest 定位的来源快照。
- `pages/`、`overview.md` 是批准后的知识投影。
- `index.md` 和 `log.md` 提供导航与 append-only 操作记录。
- SQLite 保存 proposal、revision、policy decision、解析结果、索引和图谱元数据。

文件便于人阅读和 Git 审查，数据库负责查询、并发与状态机。两者通过 revision、hash 和
commit 关联，不能用“只要 Markdown 写成功”代替数据库事务与投影状态。

## 当前来源连接器

默认 `KnowledgeSourceAdapterRegistry` 只注册 `FilesystemKnowledgeSourceAdapter`。它在
配置好的 source root 中扫描 Markdown、HTML 和文本型 PDF 等文件，并执行以下限制：

- 相对路径必须留在 root 内；
- 不跟随 symlink；
- 单文件大小和单次扫描数量有上限；
- revision 使用内容 hash；
- scan/fetch 分离，并在 fetch 时再次核对 revision；
- PDF 优先读取文本层，OCR/视觉解析是单独、默认关闭的信任边界。

`source_kind` 中出现 `obsidian`、`markdown` 或 `web` 不代表已经实现远程网站抓取。当前
`web` 仍由受控文件系统 adapter 提供本地 HTML；GitHub clone、任意 URL 和 zip 上传也
不是默认 connector。

## 从来源到 Wiki Proposal

```text
scan authorized root
  -> SourceDescriptor
  -> fetch exact revision
  -> ParserRegistry 选择 Markdown / HTML / PDF parser
  -> PreparedKnowledgeSource
  -> raw snapshot + parse artifact
  -> KnowledgeProposal(status=pending)
  -> KnowledgePolicyDecision
  -> approve / reject / rollback
  -> page revision + index/log + Git commit
```

这条链路解决两个竞态：

- scan 后文件被替换：fetch revision 不一致时拒绝；
- proposal 创建后页面已变化：`base_page_revision` 冲突时拒绝覆盖。

批准也不是把模型文本直接写文件。Store 会验证 source revision、parse provenance、目标
路径、proposal revision 和当前 page revision，再投影文件、更新索引并记录事件。

## 当前检索后端

当前默认是 `LocalKnowledgeIndex`：

```text
SQLite FTS5 sparse ranking
  + deterministic hashing embedding
  + Reciprocal Rank Fusion (RRF)
  -> ranked KnowledgeSearchHit
```

`backend_id` 为 `sqlite-fts5+hashing`。Hashing embedding 是无外部依赖的本地 baseline；
`supports_semantic_recall=false` 时，dense 结果只在 sparse 命中集合中辅助排序。因此不能把
当前默认实现描述成 PostgreSQL + pgvector 的完整语义检索。

PostgreSQL/pgvector 是未来生产后端的契约方向。替换后端时仍必须保留相同的 workspace、
visibility、source/page revision、citation 和 deterministic test 语义。

## RRF 为什么存在

全文排序擅长函数名、路径和版本号；dense 相似度擅长相近表达。RRF 使用各自的名次而非
不可直接比较的原始分数：

```python
score(document) = sum(1 / (k + rank_i(document)))
```

当前 local baseline 的 dense 不是通用语义模型，因此不要过度解释其相似度。它的价值是
固定接口、确定性与本地可运行；检索质量需要 golden query 和真实语料继续验证。

## Citation 如何保持稳定

一个检索 chunk 至少保留：

- `chunk_id` 和 `content_hash`；
- `page_id` 与 `page_revision`；
- `source_id` 与 `source_revision`；
- 来源相对路径、标题、heading/page 等定位信息；
- `visibility` 与 proposal/artifact 关联。

`citation_id` 由稳定 provenance 生成。Knowledge adapter 将这些字段转为 Harness
`KnowledgeEvidence`，前端展示引用。回答文本里的任意 `[source]` 字符串不自动成为有效
citation；服务端必须能解析回当前索引中的真实 hit。

## Source Proposal 与 Agent 边界

Agent 可以基于已获取 artifact 提出 Knowledge source proposal，但服务端会绑定：

- `owner_id`、workspace、thread 和 run；
- artifact ref、content hash 与 evidence refs；
- target root 与 relative path；
- revision、decision actor 与 event sequence。

批准后才进入 apply/job 状态。Cloud production 中 `_require_store()` 当前直接返回 503，
直到 repository 和 metadata store 完成 tenant scope。这是明确的 fail-closed 边界。

## Knowledge、Memory 与 Learning Evidence

| 类型 | 保存什么 | 写入方式 |
| --- | --- | --- |
| Knowledge | 可引用的来源与 Wiki 事实 | source + proposal + approval |
| Memory | 用户确认的长期偏好或经验 | 显式写入或 memory proposal |
| Learning Evidence | 某次实践支持的能力判断 | 引用 run/artifact/citation |
| Context Summary | 当前任务如何继续 | 运行时压缩，可重建 |

一次 Practice 成功不自动修改 Knowledge；一次检索命中也不证明用户已经掌握。跨事实层的
变化需要显式 proposal 和可复核 evidence。

## 外部参考的使用边界

外部 Wiki、RAG 和个人知识产品可以提供数据建模思路，但不能只凭产品界面判断其来源、
revision 或审批语义。本章对 Sage 的所有结论都应回到本仓库的 store、index、adapter 和
测试；引用外部方法时需要单独标注来源、版本和许可证。

## 第一入口

1. `core/knowledge/store.py::KnowledgeStore` - 事实层与 proposal 投影
2. `core/knowledge/index.py::LocalKnowledgeIndex` - 本地检索后端
3. `core/knowledge/retrieval.py` - chunk、RRF 与 citation
4. `core/knowledge/sources/registry.py` - connector fail-closed registry
5. `core/knowledge/sources/filesystem.py` - 受控文件系统来源
6. `core/knowledge/jobs/service.py` - 异步摄取状态机
7. `core/harness/knowledge_adapter.py` - Harness 检索适配
8. `api/knowledge.py` - REST/WebSocket 边界

## 测试证据

- `tests/core/knowledge/test_store.py` - proposal、revision 与投影
- `tests/core/knowledge/test_retrieval.py` - chunk、hashing embedding 与 RRF
- `tests/core/knowledge/sources/test_filesystem.py` - 来源路径与 revision
- `tests/core/knowledge/jobs/test_service.py` - job、lease、重试与恢复
- `tests/core/knowledge/source_proposals/test_service.py` - source proposal 状态机
- `tests/core/harness/test_knowledge_adapter.py` - Harness evidence 适配
- `tests/api/test_knowledge_routes.py` - API 与 production fail-closed

## 当前边界

> [!warning] Knowledge 当前是 local-first beta
> - 默认检索后端是 SQLite FTS5 + hashing baseline，不是 PostgreSQL/pgvector
> - 默认 connector 只读取服务端授权的文件系统 root，不支持任意 GitHub/URL/zip 导入
> - 文本 PDF 有受限 parser；外部 OCR/视觉解析默认关闭且未大规模验证
> - Cloud production 在 tenant scope 完成前禁用 Knowledge workspace
> - 检索质量仍受 chunk、语料和 embedding provider 影响，需要持续 benchmark
> - Wiki Lint 仍是设计方向，当前主操作是 ingest/query/proposal/review

## 理解检查

1. 为什么 source revision、page revision 和 content hash 不能只保留一个？
2. 当前 `web` source kind 为什么不等于远程网页 connector？
3. `sqlite-fts5+hashing` 与 PostgreSQL/pgvector 的能力边界是什么？
4. Proposal approval 需要防住哪两类并发或来源变化？
5. Citation 为什么必须由服务端真实 hit 生成？
6. Cloud production 为什么在 tenant scope 完成前返回 503？

下一章：[受限子代理](10-subagents-research.md)
