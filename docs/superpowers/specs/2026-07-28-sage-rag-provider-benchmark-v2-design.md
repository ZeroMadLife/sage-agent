# Sage RAG PostgreSQL 与多 Embedding Provider 收束设计

> 日期：2026-07-28
> 基线：`dev/sage-v7@85187ea`
> 状态：已完成，保留为历史设计记录
> 实现范围：PR #125、#126；最终发布边界见 `release/v1.1.0/`

## 1. 问题

Sage 已实现 PostgreSQL `GIN + ts_rank_cd + pgvector exact + RRF`，但本地运行配置仍默认
SQLite + Hashing。FastEmbed 是唯一进入当前版本化 Eval 的真实语义 Provider；现有
OpenAI-compatible 适配器又只有对称 `embed(text)`，不能表达百炼推荐的 query/document
非对称向量。因而当前工程证据无法回答两个面试追问：为什么产品主链路没有使用 PostgreSQL，
以及不同云厂商 Embedding 在同一语料与 Gate 协议下如何取舍。

## 2. 结果

- 运行时已装配 PostgreSQL exact hybrid 与三类语义 Provider，可由显式配置启用；本阶段没有
  修改用户本机 `.env`。默认 SQLite + Hashing 保留为离线回归、首次启动和 canonical
  Knowledge/Wiki 状态，不再作为语义质量候选。
- 同一版本化 Corpus/Eval 下比较 FastEmbed、百炼 `text-embedding-v4` 和豆包
  `doubao-embedding-vision`，报告同时记录模型身份、维度、协议角色、质量、延迟和可得成本。
- 百炼使用原生 DashScope query/document 协议；豆包使用其 OpenAI-compatible Embeddings
  协议；两者凭据只来自进程环境，不进入代码、报告或 trace。
- 原生 PostgreSQL GIN 是默认 sparse baseline。BM25 扩展只在隔离 PostgreSQL 17 环境做
  候选实验，不直接升级当前 PostgreSQL 16 应用数据库。

## 3. 接口决策

Embedding 合同显式区分：

```text
prepare_documents(texts)
embed_document(text)
embed_query(text)
```

Hashing、FastEmbed 和通用 OpenAI-compatible Provider 的 query/document 行为可以对称，但也
必须实现同一合同。DashScope Provider 为 query 和 document 使用独立 cache，原生请求分别传
`text_type=query/document`；query 可绑定固定英文 instruct。模型身份必须包含 provider、model、
配置 revision、dimensions 和 role policy，任一变化都使既有 Gate policy 与投影失配并触发重建。
云端配置 revision 用于本地复现与失配保护，不等同于供应商提供的不可变权重 commit。

当前 PostgreSQL 投影继续只保存每个 workspace 的一份 active embedding。运行时切换 Provider
通过 revision-aware rebuild 完成，不在产品表中并存三套向量。A/B Eval 使用隔离 workspace
顺序重建，因此可以公平对照且不会增加日常存储。

## 4. 评测决策

- Provider 选择只使用 `dev + calibration`；冻结 test 只对 selection 决定的候选执行一次 final。
- 固定 PostgreSQL、sparse、RRF、Top-K、Corpus revision 和 Eval case，只改变 dense Provider。
- 比较 Recall@10、MRR、NDCG@10、semantic-paraphrase 子集、Gate false rejection/acceptance、
  P50/P95、索引构建耗时、维度和外部请求成本。
- 每个 Provider 单独生成 relevance policy；模型、维度或 query-role policy 变化时不复用阈值。
- Hashing 只作为确定性回归 control，不再作为产品 dense 候选。

## 5. PostgreSQL 与 BM25 边界

当前 PostgreSQL 16 + pgvector exact 已通过 100k synthetic scale gate。`pg_textsearch` 需要
PostgreSQL 17/18 和 `shared_preload_libraries`，因此先在独立容器对同一 sparse cases 做
`GIN + ts_rank_cd` 与 BM25 对照。只有 Recall/MRR/NDCG 有可解释提升，且升级、恢复、许可证和
中文 tokenizer 门禁全部通过，才另开数据库迁移 PR。没有证据时继续使用原生 GIN，不把
`ts_rank_cd` 称为 BM25。

## 6. 兼容与失败策略

- 旧 Provider 的 `embed(text)` 在迁移期保留兼容入口，内部调用 document 语义；所有检索查询
  改用 `embed_query`。
- 云 Provider 对传输异常、429 和 5xx 最多执行 3 次指数退避重试；认证/请求 4xx、响应数量、响应顺序
  或维度变化立即显式失败，不静默回退 Hashing。
- API 错误只保留异常类型与有界摘要，不保存请求正文、响应向量或凭据。
- 需要禁止外发时，由运行配置显式选择 FastEmbed；本阶段未新增 workspace 级外发策略开关。
  FastEmbed 是不出网回退，不是云调用失败后的静默降级。

## 7. 非目标

- 本阶段不开发 Dataset v2、RAGAS、真实生成质量、视觉向量或 HNSW。
- 本阶段不迁移 Knowledge canonical truth，不部署，不恢复飞书；实现先经 `dev/sage-v7`
  集成验证，随后只按本地自用版本门禁合入 `main`。
- 不根据 frozen test 在多个 Provider 之间二次选优。
