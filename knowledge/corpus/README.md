# Versioned Corpus v1

该目录保存从官方来源筛选并冻结、可复现的 RAG 语料资产，而不是搜索缓存。筛选与快照整理
由 AI/Codex 辅助完成，没有独立人工逐条审核。

- `manifest.jsonl`：每行一个官方来源快照；同时绑定 upstream raw hash 与本地 snapshot hash。
- `manifest.schema.json`：交换格式；运行时最终约束由 `core.knowledge.datasets` 执行。
- `snapshots/`：用于评测的有界 extractive Markdown，不替代官方文档。

首批只冻结 LangGraph、PostgreSQL/pgvector 和 FastAPI 的 9 个核心主题，其中包括 7 份官方
文档/README 与 2 份官方源码模块。每个条目必须使用
固定 commit URL、明确许可证和 `review_status=approved`。这里的 `approved` 表示通过项目的
来源、Schema、Hash 与许可证门禁，不表示经过独立人工审阅。网页搜索结果、博客、CSDN 或
未通过这些门禁的下载不得直接加入该 manifest。

| 项目 | 官方上游 | 固定 commit | 许可证 | 快照数 |
| --- | --- | --- | --- | ---: |
| LangGraph 文档 | [langchain-ai/docs](https://github.com/langchain-ai/docs) | `1517035cdd5f3f0dbd5dc09d24c7ad930d6e710a` | MIT | 2 |
| LangGraph 源码 | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | `30c4d58db86455128e42ddec96b1ba53c553ba22` | MIT | 1 |
| PostgreSQL | [postgres/postgres](https://github.com/postgres/postgres) | `7090c696cc9ea96a278e679e8bbfe9b051740105` | PostgreSQL | 2 |
| pgvector | [pgvector/pgvector](https://github.com/pgvector/pgvector) | `a6420355c5d1c08f4c5fbd5112fc17e4cf3b5eb5` | PostgreSQL | 1 |
| FastAPI | [fastapi/fastapi](https://github.com/fastapi/fastapi) | `e9980492f2d35ea309bc2dea54a2836aae0a4dc4` | MIT | 3 |

更新上游资料时必须新增 dataset revision，并重新计算 raw/snapshot/cases hash；不得覆盖旧数字
仍宣称是同一个语料 revision。
