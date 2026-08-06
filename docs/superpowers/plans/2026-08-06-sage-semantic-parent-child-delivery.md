# Sage 语义边界与描述增强 Parent-Child 实施计划

> 日期：2026-08-06
> 状态：Slice A-C 已实现并通过本地门禁；Slice D 未开始
> 基线：`codex/book-learning-rag-design@204fcef`
> 前置设计：`docs/superpowers/specs/2026-08-05-sage-book-learning-rag-design.md`

## 1. 交付目标

为 Sage 增加一个可复现、可回滚的长文档切分实验，证明以下工程取舍：

```text
长 parent 保留完整语义和 citation
  + 句级语义边界减少主题混杂
  + 小 child 提高 PostgreSQL GIN/ts_rank_cd 与 pgvector candidate recall
  + parent description 让 child 在词面不重合时仍有可检索主题入口
  -> 通过 RRF、引用、忠实度和成本门禁决定是否值得启用
```

本计划不把该实验直接设为默认，不实现完整 TXT/EPUB 摄取，不实现在线 SFT/RL，不在本切片
复制新的 Agent runtime。

## 2. 术语与指标

- `sparse`：PostgreSQL `tsvector` + GIN 倒排候选，`ts_rank_cd(..., 33)` 排序；不是 Sage
  代码中可直接宣称的 BM25。
- `dense`：pgvector cosine exact，分数为 `1 - (embedding <=> query)`。
- `RRF`：只融合 sparse/dense 的 rank，不把 RRF 分数当概率。
- `parent`：可引用的原始 parser block 或语义 parent 文本。
- `child`：用于 sparse/dense 输入的小窗口，保留 parent block identity。
- `description`：revision-bound、extractive 的检索投影，不是 canonical evidence。
- `context recall/precision`：gold evidence 的覆盖与返回候选相关性。
- `citation support`：返回的 citation 是否解析到当前 source/page revision 和 content hash。
- `faithfulness`：可枚举回答声明被 EvidenceBundle 支持的比例；首版以 claim/evidence contract
  和 forbidden/required claim proxy 验证，不使用单一 LLM judge。
- `answer relevance`：回答是否覆盖问题意图；与 faithfulness 分开报告。
- `retrieval sufficiency`：证据是否达到继续回答的 gate；低 sufficiency 必须扩大检索、升级
  Agent 或拒答。

## 3. 垂直切片

### Slice A：描述增强 chunk contract

**行为**

- 新增独立实验策略 `described_parent_child`，旧 `parent_child` 行为保持不变；
- 超过 semantic threshold 的 block 先按句向量距离切语义 parent，再按 bounded child 切分；
- 每个 parent 只生成一个确定性的 extractive description；每个 child 的检索文本为
  `description + child`；
- `KnowledgeChunk.text` 仍是 parent 正文，`retrieval_text` 仅用于 sparse/dense，搜索结果
  按 parent 去重后返回 citation。

**公共测试 seam**

- `chunk_document()`：语义断点、最小/最大长度、description 稳定性和 content hash；
- `index_text()` / `embedding_text()`：description 只进入检索输入，不改变引用正文；
- `postprocess_search_hits()`：同一 parent 的多个 child 只返回一个 hit。

**验收证据**

- synthetic long block 触发至少两个不同主题的语义 parent；
- 同一 source revision + policy revision 重建得到相同 chunk IDs、description 和 retrieval input；
- description provider 失败时回退到 parent-child，不丢失原文；
- baseline/parent_child/described_parent_child 三种策略的旧测试全部通过。

**非目标**

- 不在这一 slice 接入外部 LLM 摘要；
- 不改变 active 默认策略；
- 不改变已有 citation ID 的历史算法。

### Slice B：SQLite index round-trip

**行为**

- description 的生成器 ID/revision 和内容作为派生索引元数据保存；
- force rebuild、旧 schema additive migration 和 stale revision 行为可重复；
- SQLite 搜索返回 parent 正文，且 description 不会成为 citation excerpt。

**公共测试 seam**

- `LocalKnowledgeIndex.ensure_schema/sync_revision/search`；
- existing index integration tests + new long-block fixture。

**验收证据**

- fresh schema 与旧 schema migration 均通过；
- 重启/force rebuild 后 citation、chunk count、embedding input hash 一致；
- 搜索输入可观察到 description，返回正文保持 parent。

**依赖**

- Slice A 的 chunk contract。

### Slice C：PostgreSQL compatibility and ablation metadata

**行为**

- PostgreSQL schema additive migration 保存 description metadata；
- `KnowledgeAblationPolicy`、eval runner 和 report 支持独立 `described_parent_child`；
- selection 只能使用 dev/calibration，frozen test 只验收。

**验收证据**

- 无 DSN 时 deterministic contract tests 仍运行；
- 有 DSN 时运行 PostgreSQL selection/final，报告 chunk/storage/P95/cost；
- strategy attribution 不与 contextual、parent-child、semantic boundary 合并。

**依赖**

- Slice B；现有 PostgreSQL exact retrieval schema。

### Slice D：Agentic RAG quality gate

**行为**

- 用同一 benchmark case 记录 retrieval sufficiency、context recall/precision、citation support、
  faithfulness proxy、answer relevance 和 false acceptance；
- 只有单次 Hybrid RAG 不足时才升级 Leader/Research；升级后记录改写、child 数、关系扩展、
  token、延迟和停止原因；
- EvidenceBundle 不足时 fail closed，不用 Agent 自评 confidence 代替证据。

**验收证据**

- Agentic 路径相对 baseline 的 context/faithfulness 改善与成本、P95 一起报告；
- no-answer false acceptance 不增加；
- child 越权、无 citation claim、过预算和递归委派测试通过。

**依赖**

- Slice C 的稳定 chunk/citation 输入；现有 Subagent/EvidenceBundle runtime。

## 4. 实施顺序与回滚

1. 先写 Slice A 的失败测试，再实现纯函数和数据对象；
2. Slice A 通过后再做 SQLite round-trip，避免先做数据库迁移却没有稳定算法契约；
3. PostgreSQL 只新增可回滚的列和策略值，旧策略继续可用；
4. 评测报告明确 `eligible_block_count`，没有真实长 block 时不对 semantic strategy 下结论；
5. 任一质量/成本门禁失败时保持 `baseline` 默认，保留实验数据和报告，不删除历史结果。

## 5. 验证命令

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python -m pytest \
  tests/core/knowledge/test_retrieval_ablation.py \
  tests/core/knowledge/test_index_factory.py \
  tests/core/knowledge/test_postgres_index.py -q

PYTHONPATH=packages/sage_harness:. .venv/bin/python -m ruff check \
  core/knowledge/retrieval.py core/knowledge/index.py core/knowledge/postgres_index.py

git diff --check
```

有 PostgreSQL 测试环境时，再运行现有 `scripts/evaluate_knowledge_ablation.py` 的 selection/final
命令，报告必须带 source commit、dataset hash、provider revision 和 test 未参与校准声明。

## 6. 交付边界

当前小版本完成后，只能对外描述为“实现了可回滚、可评测的语义边界/描述增强 Parent-Child
候选，并完成 synthetic contract 验证”，不能写成“已上线”“一定提高准确率”或
“Agentic RAG 自动保证忠实度”。简历数字只有在 clean source、冻结数据集、独立 split、
可复现报告和收口审查完成后才可使用。

## 7. 2026-08-06 小版本收口

已完成：

- Slice A：`described_parent_child`、句向量语义 parent、bounded child、确定性 extractive
  description 和 provider/revision 绑定；
- Slice B：SQLite additive migration、索引 round-trip、description 仅用于 sparse/dense input、
  parent 正文保持为 citation evidence；
- Slice C 的代码合同：PostgreSQL additive migration、ablation v2 协议、strategy attribution 和
  synthetic long-block integration tests；
- 本地全仓门禁：Ruff、format、mypy 与 pytest 通过；前端主应用和公开站生产构建通过。

仍未完成：

- 未配置 `SAGE_TEST_POSTGRES_DSN`，因此 PostgreSQL live schema/insert/search 用例未执行；
- 尚未加入版权与来源可审计的真实长书/TXT/EPUB benchmark；
- 尚未产生 v2 PostgreSQL selection/frozen-test 报告，不存在可写入简历的增益数字；
- Slice D Agentic RAG、claim-level faithfulness 与 retrieval sufficiency 升级策略仍是下一阶段。
