# Sage RAG 多 Provider 与 PostgreSQL 收束执行计划

> 设计：`docs/superpowers/specs/2026-07-28-sage-rag-provider-benchmark-v2-design.md`
> 目标分支：`codex/rag-provider-benchmark-v2`
> 合入目标：`dev/sage-v7`

## Slice 1：角色化 Embedding 合同

- 行为：索引正文走 document embedding，检索问题走 query embedding；旧对称 Provider 行为不变。
- 公共验证面：SQLite/PostgreSQL ingestion 与 search 测试、Provider cache/批处理测试。
- 兼容：保留 `embed`/`prepare` 过渡入口，不改变 chunk、citation 和 RRF 合同。
- 非目标：不改变数据库 schema，不并存多份 active embedding。

## Slice 2：百炼与豆包 Provider

- 行为：DashScope 原生适配器发送 `text_type=query/document`；OpenAI-compatible 适配器支持豆包
  2048 维端点；两者都验证响应数量、顺序、有限值和固定维度。
- 公共验证面：mock HTTP 合同测试，以及不回显 Key 的真实单向量 smoke。
- 依赖：Slice 1。
- 非目标：不把云 Provider 失败静默降级为 Hashing。

## Slice 3：PostgreSQL 同集 A/B 与 Gate

- 行为：selection 阶段依次运行 FastEmbed、百炼、豆包，只用 dev/calibration 选择一个候选；
  final 只对该候选读取冻结 test；输出 route-specific policy 和 provider comparison。
- 公共验证面：版本化 80-case Eval、真实 PostgreSQL、clean source SHA、结构化报告校验。
- 依赖：Slice 2。
- 非目标：不在 frozen test 上选 Provider，不把 Eval 指标称为线上指标。

## Slice 4：BM25 隔离决策

- 行为：记录 PostgreSQL 16 原生 GIN 与 BM25 扩展的版本、迁移、中文 tokenizer、许可和恢复门禁；
  仅在独立 PostgreSQL 17 容器可复现时运行质量对照。
- 公共验证面：环境探测、同一 sparse cases、可销毁容器和决策报告。
- 依赖：无；不阻塞 Slice 1-3。
- 非目标：不升级当前应用 PostgreSQL volume，不把候选写成默认。

## Slice 5：产品配置与求职证据

- 行为：本机 `.env` 启用 PostgreSQL、selection 胜出的语义 Provider、对应 Gate 与 recovery；完成
  migration、启动和检索 smoke。随后更新仓库复盘、Obsidian 面试答辩和简历前两个 RAG bullet。
- 公共验证面：配置存在性检查、PostgreSQL projection summary、检索 smoke、报告 SHA、完整质量门禁。
- 依赖：Slice 3；简历数字只能来自已提交报告。
- 非目标：不覆盖 PDF，不部署，不接飞书，不合入 `main`。

## 收口门禁

1. 定向 pytest、完整后端检查、Ruff、mypy、前端测试与两套 build、`git diff --check` 通过。
2. 审查共享 Provider/API/store 影响，报告用户原有未提交文件但不修改。
3. 代码与 Eval 证据用职责清晰的 commit 固定，通过中文 PR 合入 `dev/sage-v7`。
4. Obsidian 记录 source/merge commit、真实指标、关闭风险和下一阶段边界。

