# v1.1.0 Changelog

## Added

- 版本化 Corpus/Eval 契约、40/20/20 split、Hash 与 leakage-group 校验。
- PostgreSQL `GIN + ts_rank_cd` sparse、pgvector exact cosine dense 与 RRF 融合检索。
- FastEmbed、百炼 DashScope 与豆包 OpenAI-compatible Embedding Provider 合同及同集比较。
- revision-aware Gate、HMAC retrieval trace、`retrieval_runs` 失败分类与有界 recovery。
- Parent-Child、语义切分、Contextual Chunk、Cross-Encoder 的独立消融入口。
- L1/L2 多模态 evidence contract 与 100k synthetic HNSW scale gate。

## Changed

- Hashing dense 明确降为确定性回归 control，不再称为真实语义召回。
- 简历与复盘指标更新为最终百炼 frozen-test 结果：可回答子集 Recall@10 `0.889 -> 1.000`、
  MRR `0.683 -> 0.806`、NDCG@10 `0.701 -> 0.852`，false rejection `2 -> 0`，
  false acceptance 保持 `0`。
- Eval 与 Gold 口径改为 AI/Codex 辅助构造，移除独立人工审阅暗示。

## Not Included

- 不部署、不开放公网、不恢复飞书，也不修改用户本机 `.env`。
- 不将 PostgreSQL `ts_rank_cd` 称为 BM25；BM25 扩展实验仍受 PostgreSQL 17/18 与 tokenizer
  门禁约束。
- 不接入 Dataset v2、HiCBench/Qasper、RAGAS、真实 LLM generation 或真实视觉检索。
- 不默认启用 HNSW、Parent-Child、Cross-Encoder 或语义切分。
