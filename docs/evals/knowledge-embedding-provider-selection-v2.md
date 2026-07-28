# Sage Knowledge 多 Embedding Provider Selection v2

> 日期：2026-07-28
> source commit：`ae3672e1baae2e13164e1caa0ccbc9abee58379b`
> 数据集：`sage-official-agent-fullstack-v1@2026-07-27.1`
> split：`dev=40 + calibration=20`，未读取 frozen test
> backend：PostgreSQL GIN + pgvector exact + RRF

## 结论

同一 PostgreSQL、Corpus、Top-K、RRF 与 Gate 协议下，FastEmbed 384、百炼
`text-embedding-v4` 1024 和豆包 `doubao-embedding-vision` 2048 的 hybrid Recall@10 都从
Hashing baseline 的 `0.940` 提升到 `1.000`。三者差异主要落在排序、Gate、延迟、维度和成本
可观测性，而不是 Recall。

百炼在 selection 的 MRR/NDCG@10 最高，且估算成本低于冻结门限，因此选为唯一 final 候选。
豆包的 false rejection 最少，但 Coding Plan 没有逐 token 成本口径，本轮成本 Gate 按 unknown
fail closed；FastEmbed 保留为不出网回退。

## Selection 对照

| Provider | 维度 | Recall@10 | MRR | NDCG@10 | Abstain F1 | false rejection / acceptance | P95 | 准备耗时 | 估算费用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FastEmbed MiniLM | 384 | 1.000 | 0.791 | 0.831 | 0.286 | 4 / 1 | 14.474 ms | 1266.947 ms | $0 |
| 百炼 text-embedding-v4 | 1024 | 1.000 | **0.817** | **0.854** | 0.333 | 3 / 1 | 21.541 ms | 3001.606 ms | $0.000536 |
| 豆包 embedding-vision | 2048 | 1.000 | 0.803 | 0.839 | **0.500** | **1 / 1** | 19.974 ms | 2242.022 ms | unknown |

三条 candidate 的 semantic-paraphrase Recall delta 均为 `+0.083333`，overall Recall delta 均为
`+0.060345`，citation support delta 均为 0。表中费用是 Eval 级估算，不是账单：百炼按固定
`$0.0001 / 1k tokens` 的保守记账参数计算；豆包包月额度无法换算为逐 token 美元成本，因此不
填 0。

## 协议差异

- FastEmbed 使用固定 Hugging Face snapshot 与 FastEmbed 0.8.0 mean pooling，query/document
  对称，不出网。
- 百炼通过原生 DashScope API 分别发送 `text_type=document/query`，query 绑定固定英文
  instruct；document/query cache 相互隔离。官方文档说明 `text-embedding-v4` 支持 64-2048
  维，通用场景推荐 1024 维，并建议检索任务区分 query/document。
- 豆包通过 Coding Plan 的 OpenAI-compatible `/embeddings` 调用
  `doubao-embedding-vision-250615`。端点实测返回 2048 维；因接口为对称协议，本轮没有人为添加
  query 前缀。

官方依据：

- [阿里云百炼 Embedding](https://help.aliyun.com/en/model-studio/embedding)
- [火山方舟 Coding Plan Embedding](https://developer.volcengine.com/articles/7628812787703087110)

## 防泄漏与 Gate

三份 selection 都绑定 clean source commit、相同 dataset/cases hash 和同一 PostgreSQL exact
backend。Provider 与 Gate 只使用 dev/calibration；冻结 test 未参与三选一。百炼 candidate
policy 为 `krp_20e8c339cd06a8b0`，同时绑定 model、role policy、dimensions、corpus revision 与
Top-K；任一变化都拒绝复用。

## PostgreSQL 与 BM25 决策

日常主链路使用当前 PostgreSQL 16 的 `GIN + ts_rank_cd` sparse 与 pgvector exact dense。
`ts_rank_cd` 仍不称为 BM25。许可宽松的 `pg_textsearch` 提供 BM25，但当前仅支持 PostgreSQL
17/18，并要求 `shared_preload_libraries` 与重启；直接启用会把一个可重建检索投影实验扩大成
应用数据库升级。

因此 BM25 保持隔离候选：未来在独立 PostgreSQL 17 容器中用同一 sparse cases 对照 Recall、
MRR、NDCG、P95、中文 tokenizer 与 rebuild；通过后再单独设计数据库迁移。当前不因为技术名词
更像搜索引擎就替换已验证的原生 GIN。

官方依据：[pg_textsearch](https://github.com/timescale/pg_textsearch)。

## 证据文件

- `evals/reports/knowledge_embedding_fastembed_postgres_selection_v2_2026-07-28.json`
  - SHA-256：`79e0b6e695b5b09766b7c04ecdd8c875731b283734353de9b25a2a618a7134be`
- `evals/reports/knowledge_embedding_bailian_postgres_selection_v2_2026-07-28.json`
  - SHA-256：`d637cd16d94b3b4f3f8f67d6bab7a4f748a4f36755bfab36056c51e83505030f`
- `evals/reports/knowledge_embedding_doubao_postgres_selection_v2_2026-07-28.json`
  - SHA-256：`df7e4f8da3478633cddfea8d72b8f20be2e344e52034fb07ad5e4ba7845ded66`

## 当前边界

- selection 只有 60 条，且 corpus 只有 49 个 parser blocks；不能推导线上 SLA。
- 三家 Recall 相同不代表模型等价，当前差异已出现在 ranking 与 Gate。
- 百炼成本使用可审计的保守换算参数；豆包成本未知，不比较价格优劣。
- 本阶段未修改 frozen test、未评真实生成质量、未部署、未接飞书、未合入 `main`。
