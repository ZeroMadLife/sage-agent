# Memory Lifecycle v1

## 1. 解决的问题

旧 MemoryStore 只管理 proposal 的 `pending -> approved/rejected`，事实一旦批准就没有正式的
更正和撤回状态。Markdown 投影又是 append-only；如果只在文件里追加一条相反内容，模型会
同时看到新旧事实，无法判断哪一条仍有效。

schema v2 将事实生命周期放回 canonical SQLite：

```text
pending proposal -> approved -> active fact
active fact -> superseded
active fact -> retracted
```

- 更正先创建带 `supersedes_content_hash` 的 pending proposal，批准时才原子替代旧事实；
- 撤回要求 fact revision CAS，保存 reason、actor、revision 和 append-only event；
- 召回只读取 active facts，并用 SQLite 全状态集合屏蔽 Markdown 中的旧投影；
- 显式 remember 也进入 SQLite 审计链，Dream 只从 lifecycle-filtered active facts 取数；
- consolidation 仅接收带 evidence refs 的 episodic evidence，精确去重后生成 pending proposal，
  无权自动批准。

REST 闭环提供事实列表、事实事件、撤回和更正 proposal；更正仍复用既有人工审批接口。

## 2. 复现与结果

```bash
python scripts/evaluate_memory_lifecycle.py \
  --output tmp/sage-memory-lifecycle-v1.json
```

clean source commit `0e21fda` 的确定性评测为 40/40：

| 类别 | Case | 通过 |
| --- | ---: | ---: |
| pending proposal 隔离 | 8 | 8 |
| retraction 与 active recall 隔离 | 8 | 8 |
| supersession 原子替代 | 8 | 8 |
| consolidation 去重与证据门禁 | 8 | 8 |
| 重启恢复与 workspace 隔离 | 8 | 8 |

功能切片同时通过 752 项 Coding 相关回归。机器摘要位于
`evals/reports/memory_lifecycle_v1_2026-07-25.json`。

## 3. 证据边界

40 条是确定性生命周期场景，不是“长期记忆准确率 100%”。它证明状态机、CAS、作用域和
proposal-only 不变量，没有评估模型从自然对话抽取事实的质量。

首版 consolidation 接收已经抽取且带来源的 evidence；自动 Fact Extraction、语义聚类、
重要度/TTL、跨事实冲突分类仍是后续切片。没有显式 `supersedes` 的语义冲突会被同时保留，
系统不会让 recency 静默覆盖旧事实。
