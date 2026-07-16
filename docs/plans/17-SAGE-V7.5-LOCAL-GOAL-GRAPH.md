# Sage V7.5 本地目标驱动知识图谱开发计划

## 1. 产品目标

V7.5 将 Sage 已批准的 Git Wiki 投影为本地、可重建、可追溯的知识图谱，并允许用户在 `purpose.md` 中声明学习目标，例如“成为具备生产交付能力的全栈 AI 应用工程师”。

图谱用于回答三个问题：

1. 我已经学会了什么，证据来自哪一版文档？
2. 已有知识之间如何连接，哪些节点承担桥接作用？
3. 距离目标能力还缺什么，下一步应该研究什么？

主对话 Harness、Agent Loop、Memory/Dream 和工具执行由独立开发线负责。本阶段只提供 Knowledge Graph 和后续研究 Agent 可消费的结构化输入。

## 2. 事实边界

- `Sage-knowledge` Git Wiki 是唯一可发布事实源。
- SQLite Graph Projection 是派生缓存，可以删除并从 Wiki 全量重建。
- 每个节点绑定 `page_revision` 或 `source_revision`。
- 每条关系必须绑定 `chunk_id`、`citation_id`、抽取器版本和置信度；没有证据不得进入正式图谱。
- 公网资料只能先进入候选来源和 research proposal，经过现有解析、引用和批准链后才能进入 Wiki，再由本地图谱消费。
- 本阶段不引入 Neo4j。单用户本地投影使用 SQLite；云端多租户阶段迁移到 PostgreSQL 仍保持同一领域契约。

## 3. 分阶段交付

### V7.5.0：确定性图谱投影

- 从当前 Wiki revision 生成 `Page/Source/Project/Concept/Decision/Tool` 节点。
- 生成 `WIKILINK/EVIDENCED_BY/SHARES_SOURCE` 确定性关系。
- 未解析 `[[wikilink]]` 生成 `missing=true` 的 Concept 缺口节点。
- 使用 `wiki_watermark + projector_version + config_hash` 生成稳定 `graph_revision`。
- 重建失败保留错误状态，不留下半张图；成功后保留旧快照用于回放。
- 提供 overview、status、rebuild、node detail 和 bounded neighborhood API。
- Graph API 支持类型/文本过滤、节点分页、边数量上限、指定历史 revision 和 ETag。

### V7.5.1：目标、社区与缺口

- 将 `purpose.md` 扩展为结构化目标契约，保存目标、能力维度、验收标准和版本。
- 对本地确定性图使用固定 seed、resolution 和实现版本的 Louvain 社区检测。
- 计算 community cohesion、isolated node、bridge node、unresolved concept 和 target capability gap。
- 提供 communities、insights、goal alignment 和 research proposal 输入 API。
- 不在这个版本执行 Web Search，只输出受控研究问题、证据缺口和建议关键词。

### V7.5.2：Graph Workspace

- Knowledge Workspace 增加真实 Graph Tab；无 API 数据时显示明确空状态，不使用演示假数据。
- 使用 Sigma.js + Graphology 渲染服务端返回的布局与社区数据。
- 支持类型/社区视图、筛选、搜索、节点 Inspector、证据跳转和稳定位置缓存。
- 桌面展示完整图谱；平板降级部分标签；手机默认列表和局部邻域，不强行渲染全图。

### V7.6：联网研究闭环

- Research Agent 根据本地 gap 生成检索计划，受域名、预算、超时和引用策略约束。
- 掘金、官方文档、GitHub、飞书等连接器只负责收集候选资料。
- 候选资料经过解析、去重、引用、proposal 和批准后写入 Git Wiki。
- 新 Wiki revision 触发索引和 Graph Projection，形成“目标 → 缺口 → 研究 → 审核 → 沉淀 → 重新评估”的可回滚循环。

## 4. V7.5.0 API 契约

| 接口 | 职责 |
| --- | --- |
| `GET /api/v1/knowledge/graph/status` | 返回未构建、构建中、就绪、失败或过期状态 |
| `POST /api/v1/knowledge/graph/rebuild` | 从当前 Wiki revision 重建图谱快照 |
| `GET /api/v1/knowledge/graph` | 返回有界节点、内部边、快照元数据和分页游标 |
| `GET /api/v1/knowledge/graph/nodes/{node_id}` | 返回节点及其 revision 绑定 |
| `GET /api/v1/knowledge/graph/nodes/{node_id}/neighbors` | 返回有界一跳邻域和关系证据 |

`GET /graph` 的 ETag 由 `graph_revision + filters + pagination` 计算。同一 Wiki 水位和投影配置产生同一 revision；页面更新产生新 revision，旧 revision 仍可显式读取。

## 5. clean-room 参考边界

V7.5 只参考 `nashsu/llm_wiki` 的产品行为：Wiki 链接成图、社区视图、关系筛选、节点详情和图谱洞察。由于上游采用 GPLv3，Sage 不复制其 Rust/TypeScript 源码、组件、样式、资源或内部数据结构，所有投影算法、API、测试和界面均按 Sage 现有 revision/citation 契约重新实现。

## 6. 验收门禁

- 同一水位重复重建结果稳定，页面更新后旧快照可回放。
- 任意正式关系至少包含一个当前 citation；旧快照证据保持旧 revision，不漂移到新内容。
- 失败重建不覆盖最后一个成功快照，也不留下部分节点或边。
- Graph API 不泄露绝对路径、密钥、原始私有内容或未批准来源。
- 后端全量测试、ruff、mypy、`git diff --check` 全部通过。
- V7.5.2 完成前端全量测试、生产构建和三个视口真实浏览器验收。

## 7. 当前边界

V7.5.0 不包含 LLM 实体关系抽取、Louvain、Web Search、GraphRAG 路径扩散和个人助手 Harness。它交付的是可靠图谱底座，而不是用视觉图伪装完成的智能能力。
