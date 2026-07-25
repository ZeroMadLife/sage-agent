# Sage 项目经历简历稿

> 证据更新时间：2026-07-25。正式投递前将临时 HTTP 演示地址替换为备案后的 HTTPS 域名。

## 建议版

### Sage｜个人 AI 学习与实践工作台｜个人项目｜2026.04 - 至今

**项目简介**：面向开发者的本地优先 AI 学习与实践工作台，在统一 Agent Harness 中组织
个人知识检索、受控工具调用与代码实践，并将任务过程沉淀为可恢复、可复核的运行证据。

**技术栈**：Python、FastAPI、LangGraph、LangChain、Pydantic、SQLite FTS5、PostgreSQL、
Redis、Vue 3、TypeScript、Docker

**项目链接**：[源码](https://github.com/ZeroMadLife/sage-agent)｜
[在线演示](http://121.40.185.188/)｜
[技术博客](https://blog.sagecompanion.top/)

**核心设计与实现**：

- **混合检索与可复现评测**：针对旧 50 条文档级查询无法映射当前语料的问题，重建 200 条
  分层 Benchmark 与 section 级 graded qrels，以 SHA-256 固定 16 份语料和 879 个 chunks；
  通过 FTS5 BM25 + `text-embedding-v4` 双路召回与 RRF 融合，使 Recall@10 从 0.578 提升至
  0.814（+40.9%）、NDCG@10 从 0.444 提升至 0.695（+56.3%），并用 20 条无答案题识别出
  abstention accuracy 为 0 的真实缺口。
- **上下文预算与 Artifact 按需装载**：针对 Shell、Fetch 等大工具结果反复进入 prompt 导致
  上下文膨胀，将超过 16 KiB 的完整结果 offload 到 session/run scoped Artifact Store，模型
  仅保留最多 200 行、12,000 字符预览与 `artifact_ref`，并提供同 session、单次最多 16 KiB 的
  UTF-8 安全分页回载；保留六级硬窗口安全层，新增 `32k/12k` LangGraph `before_model` 工作集压缩，
  保护最新用户意图和完整工具调用对。12 条确定性长工具任务中，累计输入估算下降 14.50%、
  checkpoint 消息内容下降 31.50%，扣除 4.94% 摘要成本后净 token 下降 9.57%。
- **Harness 治理与可恢复运行证据**：将参数校验、permission、policy、approval 与 sandbox
  固定在副作用前执行；通过 LangGraph checkpoint、SQLite Timeline、run lease/fencing 和
  persist-then-push 重放支持断线恢复及过期 writer 拒绝，并用结构化 stage/tool/result 事件
  展示执行依据，而不是保存或暴露模型原始 CoT。
- **Container Sandbox 最小权限**：针对已启动容器可能以旧配置被无条件复用的问题，增加
  Rootless/Desktop + seccomp admission、`cap-drop ALL`、`no-new-privileges`、只读 rootfs、禁网、
  CPU/RAM/PID/ulimit 与 mount 漂移校验；Docker live audit 10/10，通过非零 Shell 结构化错误
  接入统一 Timeline，同时明确 writable workspace overlay 与 image digest 仍待生产收口。
- **长期记忆生命周期**：以 SQLite schema v2 建立 `active -> superseded/retracted` 事实状态机，
  通过 revision CAS、append-only event 和显式 `supersedes` 防止 Markdown 旧投影重新进入召回；
  consolidation 对带来源的 episodic evidence 去重后仅生成 pending proposal，40/40 确定性场景
  覆盖审批隔离、撤回、替代、重启恢复和 workspace 隔离。

## 更短的四条版

- 构建 FTS5 BM25 + 语义 Embedding + RRF 混合检索，重建 200 条 section 级 Benchmark；在
  16 份语料/879 chunks 上将 Recall@10 从 0.578 提升至 0.814，NDCG@10 从 0.444 提升至
  0.695，并以无答案集显式暴露 abstention 缺口。
- 面向 Shell/Fetch 大结果导致的 prompt 膨胀，设计硬窗口 + 32k 图工作集双层预算和 Artifact
  offload/分页回载；12 条确定性长工具任务中累计输入估算下降 14.50%、checkpoint 消息内容
  下降 31.50%，扣除摘要成本后净 token 下降 9.57%，当前意图与工具调用对保留率 100%。
- 将 Pydantic 参数校验、permission、policy、approval、Sandbox 与结构化 Timeline 串成统一
  Harness；结合 checkpoint、lease/fencing 和 persist-then-push 支持断线重放、审批恢复与
  过期写入拒绝，不暴露模型原始 CoT。
- 强化 Rootless Container Sandbox 并实现 Memory lifecycle：Sandbox live audit 10/10；长期
  事实支持 CAS 撤回/替代，consolidation 保持 proposal-only，40/40 场景验证生命周期与隔离。

## 投递边界

- `+40.9%` 与 `+56.3%` 是真实语义 Provider 相对 Hashing 离线基线，不是相对上一生产版本；
- `10/10` 是 Docker Desktop Level 1 live audit，不等同于内核级逃逸证明；
- `40/40` 是确定性 Memory 生命周期场景，不是自然对话记忆准确率；
- Context 的 `14.50% / 31.50% / 9.57%` 来自 provider-neutral 确定性机制评测，
  不等同于真实 Provider 账单、摘要语义质量或线上延迟；
- 当前公开演示为普通 HTTP，正式投递应优先使用备案后的 HTTPS；
- 不写“完整安全 CoT”：Sage 保存结构化运行证据，不保存或展示模型私有推理链。
