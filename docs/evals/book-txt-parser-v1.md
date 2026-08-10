# Sage 长书 TXT Parser v1 评测报告

> 日期：2026-08-06
> 数据集：`book-learning-public-domain-v1@2026-08-06.1`
> 状态：本地真实长文本 parser 评测；不是 Retrieval/Agentic/生成质量评测

## 结论

Sage 已能把 UTF-8/BOM TXT 作为正式 Knowledge 来源摄取，并按章节标题与空行段落形成稳定
block。每个未拆分 block 保留 1-based 行区间和 0-based half-open 字符/UTF-8 字节区间；
locator 已穿透 parse artifact、SQLite/PostgreSQL 检索投影、Knowledge API、Harness Knowledge
evidence 和 Research EvidenceBundle。

第一版对非 UTF-8 文本 fail closed，不做 `errors=replace`。无法识别的超长 block 仍由现有
chunk 策略处理；若一个原始 block 被拆成多个正文 chunk，当前不会伪造子 chunk 的精确
offset，locator 返回 `None`，后续再增加 split-aware span mapping。

## 合法语料

| 文档 | 来源 | 版权状态 | SHA-256 | 评测角色 |
| --- | --- | --- | --- | --- |
| 《西遊記》 | Project Gutenberg 23962 | Gutendex `copyright=false` | `af3c9e...fc1f58` | 中文章节、人物关系、跨回目多跳 |
| *The Wealth of Nations* | Project Gutenberg 3300 | Gutendex `copyright=false` | `e91d52...3ae58d` | 理论概念、跨章节比较、长段落 |

仓库只保存 manifest、来源 URL、许可引用和校验哈希。原文下载到 `.coding/corpora/` 本地
忽略缓存，不进入 Git。来源不明的《凡人修仙传》《仙逆》TXT 未下载、未提交、未用于评测。

## Parser 指标

| 指标 | 《西遊記》 | *The Wealth of Nations* | 合计 |
| --- | ---: | ---: | ---: |
| 原始大小 | 2,264,069 B | 2,468,951 B | 4,733,020 B |
| block 数 | 3,028 | 2,346 | 5,374 |
| 识别章节/Book/Chapter | 100 | 89 | 189 |
| locator block 覆盖 | 3,028 / 3,028 | 2,346 / 2,346 | 1.000 |
| locator 原始字节回切一致 | 3,028 / 3,028 | 2,346 / 2,346 | 1.000 |
| 非空原文行保留 | 23,697 / 23,697 | 32,415 / 32,415 | 1.000 |
| 单次本机 parse 时间 | 32.506 ms | 48.904 ms | 仅方向性 |

时间来自一次本机单进程运行，不是 P50/P95，也不是生产 SLA。正文保留与 locator 指标是
确定性检查；章节数只证明规则在这两本书上识别出结构，不代表任意 TXT 都有相同效果。

## 复现

```bash
PYTHONPATH=packages/sage_harness:. .venv/bin/python \
  scripts/fetch_book_learning_corpus.py
PYTHONPATH=packages/sage_harness:. .venv/bin/python \
  scripts/evaluate_book_txt_parser.py \
  --output .coding/evals/book-txt-parser-v1.json
```

## 尚未完成

- 尚未建立两本书的 query/gold citation 集，因此没有 Recall/MRR/NDCG/multi-hop AllRecall；
- 尚未运行 PostgreSQL live corpus benchmark；
- 证据充分性合同已实现，但尚未接入主对话的服务端 Leader coordinator；
- Research/Synthesize 多 Agent 仍使用现有底座，尚未形成自动
  `single-pass -> assess -> delegate -> reassess -> answer/abstain` 运行闭环；
- 尚未评测 claim-level faithfulness、answer relevance、citation support、成本和 P95。
