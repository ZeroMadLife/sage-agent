# pg_textsearch 隔离评测环境

该目录只用于 PostgreSQL 17 + pg_textsearch 1.3.1 的 BM25 对照，不改变 Sage 默认
`pgvector/pgvector:pg16` 镜像，也不作为生产部署依赖。

```bash
# 网络受限时只替换基础镜像，评测代码和扩展版本保持不变。
SAGE_PG_TEXTSEARCH_IMAGE='dockerproxy.net/pgvector/pgvector:pg17' \
  docker compose -f evals/pg_textsearch/docker-compose.yml up -d --build

SAGE_BOOK_BENCHMARK_POSTGRES_DSN='postgresql://sage_eval:sage_eval_dev@localhost:55432/sage_eval' \
  PYTHONPATH=. python scripts/benchmark_book_learning_retrieval.py \
  --backend postgres --postgres-sparse auto --skip-fetch
```

`auto` 会先探测 `pg_textsearch`：扩展可用时使用 BM25，不可用时自动使用 native
GIN + `ts_rank_cd`。`--postgres-sparse bm25` 是强制 BM25 的隔离实验；`native` 是强制
GIN 的对照路线。产品默认仍是已通过长书 Gold 的 native hybrid，不因扩展存在就自动升级。

默认宿主端口是 `55432`；冲突时可在两个命令中同时设置
`SAGE_PG_TEXTSEARCH_PORT=55433`，并把 DSN 端口改为 `55433`。

评测完成后可以停止容器；容器不挂载持久卷，评测数据可从公开书籍重新构建。

BM25 索引使用 PostgreSQL `simple` text configuration。中文长书是否需要 `zhparser` 或
其他分词器，必须由同一 Gold 的 Recall/MRR/NDCG 对照确认，不能仅凭 BM25 名称推断有效。
