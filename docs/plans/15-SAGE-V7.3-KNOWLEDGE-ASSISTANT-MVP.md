# Sage V7.3 可商用知识助手 MVP 开发计划

## 目标

把现有“导入 + 审核列表”升级为可演示的知识助手闭环：来源自动沉淀、Hybrid RAG、稳定引用、知识问答和可撤销自治更新。

## 小版本

### V7.3.0 历史审核债务收口

- 对历史 pending proposal 做只读风险重评，不直接批量批准旧数据。
- 当前来源未变化且可由受信本地 parser 确定性解析时，重新生成带 ParseArtifact 的新 proposal，并按 Policy v1 自动应用。
- 来源 revision 已过期或来源已删除时归档旧 proposal；需要 OCR、外部 parser 或人工判断时继续留在异常队列。
- 迁移预览返回稳定 `plan_id`；执行时重新计算，计划变化返回 conflict。
- UI 提供一次性整理入口，审核列表改为“异常与需确认”。

### V7.3.1 Revision-aware Hybrid Retrieval

- 建立 `KnowledgeChunk`、embedding 和 index revision 契约。
- 本地使用 SQLite FTS + 可插拔 deterministic dense baseline，云端目标为 PostgreSQL FTS + pgvector。
- 实现 sparse/dense/RRF、workspace/source/revision/visibility 过滤，以及增量索引、删除、重建和检索 benchmark。

### V7.3.2 知识问答与引用

- 提供检索 API 和 `RetrievalBundle`，citation 绑定 page/source revision 与 block/chunk ID。
- Knowledge Workspace 支持来源树、Wiki 页面、正文、引用 Inspector、任务和知识问答。
- Coding/Assistant Harness 增加只读 `knowledge_search` 工具。

### V7.3.3 自治 Wiki Loop

- 来源变化触发解析、理解、索引和受影响 Wiki 更新。
- private、schema 合法、引用覆盖完整且置信达标的更新自动应用并可撤销。
- 外部 OCR、低置信、敏感或越界内容进入草稿或阻断。

## 非目标

本阶段不实现 Neo4j、GraphRAG、Louvain、HR 公共门面、飞书 Bot、Kubernetes 或公网部署。

## 发布门禁

- 每个小版本完成后端/前端定向测试、全量回归、类型检查、生产构建和 `git diff --check`。
- 每个小版本直接提交并推送到 `dev/sage-v7`，同时更新中文 Obsidian 复盘。
- V7.3.2 完成后才具备本地可演示垂直闭环；V7.3.3 完成后进入服务器私测部署阶段。
