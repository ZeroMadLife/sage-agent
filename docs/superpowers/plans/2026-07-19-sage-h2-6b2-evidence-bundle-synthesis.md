# Sage H2.6B2 Evidence Bundle 与受限综合实施契约

> 日期：2026-07-19
>
> 状态：发布门禁已通过，待 PR 合入 `dev/sage-v7`
>
> 基线：`dev/sage-v7@e7fc8d8`

## 1. 目标

在 H2.6B1 的 Research 并行与共享预算底座上，建立一个只读、父 run 绑定、token 有界的 Evidence Bundle 端口，并开放服务端静态 `synthesize` profile。Synthesize 只能读取当前父 run 中成功 Research child 已落账的证据，不得重新访问网络、本地文件、Shell、写入工具或其他子代理。

同时增加 run 级查询和来源指纹，阻止同一轮重复 Web/Knowledge 查询和重复页面抓取。指纹不得保存原始查询文本，且不能跨轮永久阻止用户刷新时效性证据。

## 2. Evidence Bundle 契约

- 调用方必须同时提交 `thread_id`、当前 `parent_run_id`、成功 Research child IDs 和已落账 evidence refs。
- adapter 只读取 Sage `RunStore` 中对应 child 的 durable trace，不接受模型直接提供正文、URL 或 revision 作为授权依据。
- child trace 必须包含绑定当前父 run 的 `subagent_started(subagent_type=research)` 与 `subagent_terminal(status=succeeded)`。
- 只解析服务端产生的 `knowledge_search`、`search_web`、`fetch_web` 成功结果；本地文件内容、child 文本和失败工具结果不能成为证据。
- Knowledge evidence 保留 citation、page/source revision；Web evidence 保留 citation、canonical URL 和 content hash。
- 同一来源优先保留 Fetch 正文，其次 Knowledge，最后 Search 摘要；结果再次按 `token_budget` 截断。
- 私有完整 Web artifact 不直接读取或注入 child；首版只使用已经写入 tool trace 的有界 excerpt。

## 3. Synthesize Profile

- profile 名称固定为 `synthesize`，唯一工具为 `read_evidence_bundle`。
- profile 仅在 Research 必需端口和 Evidence Bundle 端口都可用时注册。
- 发起任务时，父状态冻结当前 evidence refs、成功 Research child IDs 和去重指纹到 `SubagentRequest`。
- Synthesize 必须至少成功读取一次非空 Evidence Bundle；跳过读取即以 `evidence_bundle_not_read` 失败。
- 输出只能综合 bundle 中返回的 citation，明确区分一致结论、冲突和缺失证据。
- Synthesize 不拥有 Web、文件、Shell、Memory、Knowledge proposal、持久化或递归委派能力。

## 4. 重复检索 Breaker

- `knowledge_search` 与 `search_web` 记录 `parent_run_id + tool + normalized query` 的 SHA-256 指纹。
- Web/Knowledge 来源记录 `parent_run_id + stable source identity` 的 SHA-256 指纹。
- durable state 仅保存有界 opaque hashes，不保存原始查询、网页正文或敏感参数。
- 同一 child 和后续同父 run child 会跳过已执行查询；同一来源不会再次 Fetch。
- 新父 run 使用新的指纹域，因此允许相同查询获取更新证据。
- reducer 对并行 child 的完成顺序保持确定性，最多保留 256 个查询/来源指纹。

## 5. 恢复与安全边界

- child identity 继续由 `thread + parent run + tool call` 派生，checkpoint 重放复用原 child terminal receipt。
- Evidence Bundle 读取要求当前 parent run 仍为 active；跨 thread、跨 run、非 Research 或非成功 child 全部 fail-closed。
- Synthesize terminal trace 记录实际 citation refs、查询/来源指纹与 token/model/tool 实耗，不记录完整 child transcript。
- 本切片不修改前端 wire schema，不伪造 stage、thinking、RAG receipt 或长期沉淀状态。
- Wiki、Knowledge Unit、Memory、Mastery 与 Plan 仍只能通过用户批准的 proposal 流程进入长期状态。

## 6. 本切片不交付

- Node Research Task/Research Branch 持久化实体及 `primary_goal_id` 绑定。
- 提交时冻结的 graph/page/source `surface_context` 服务端 receipt。
- 实际 RAG 使用的 chunk/revision/trimming/token-budget receipt。
- Practice Profile、Mastery Evidence 与自动 Goal 续跑。
- 自动写入 Wiki、Knowledge Unit、Memory、Mastery 或 Plan。

## 7. 验收

1. 只有当前父 run 的成功 Research child receipt 能被 Evidence Bundle 读取。
2. 同源 Search/Fetch 证据稳定去重，优先保留 Fetch，有界预算不会溢出。
3. Synthesize 运行时只有 `read_evidence_bundle`，且不读 bundle 不能成功。
4. 同一 run 的重复查询被拦截，新 run 可以重新查询。
5. checkpoint 恢复保留 evidence refs、child IDs 和指纹，不重复执行已完成 child。
6. 后端定向与全量测试、Ruff、mypy、前端回归、生产构建和 `git diff --check` 通过后，才允许 PR 合入 `dev/sage-v7`。

## 8. 下一切片

H2.6C 设计受限 `practice` profile：Practice 只能产生结构化 Mastery Evidence 候选，掌握度必须来自可验证证据与确定性权重，不能由模型自评分；任何长期更新继续走用户批准的 proposal。

## 9. 发布门禁记录

- 定向 Evidence/Subagent/State 回归：`46 passed`。
- Harness、Coding 与 API 关联回归：`315 passed`。
- 后端全量回归：`1644 passed`；仅保留既有 tokenizer 与线程回收 warning。
- Ruff：全仓 `ruff check` 通过。
- mypy：`264 source files` 通过，包含可复用 `sage_harness` 包。
- 前端回归：59 个文件、436 个测试通过。
- 前端生产构建通过；仅保留既有 chunk size warning。
- `git diff --check` 通过，隔离 worktree 无未提交文件。
