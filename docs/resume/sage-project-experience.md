# Sage 项目经历简历稿

> 证据更新时间：2026-07-28。当前仅提供源码，不开放在线演示。

## 建议版

### Sage｜个人 AI 学习与实践工作台｜个人项目｜2026.04 - 至今

**项目简介**：面向开发者的本地优先 AI 学习与实践工作台，在统一 Agent Harness 中组织
个人知识检索、受控工具调用与代码实践，并将任务过程沉淀为可恢复、可复核的运行证据。

**技术栈**：Python、FastAPI、LangGraph、LangChain、Pydantic、SQLite FTS5、PostgreSQL/pgvector、
Redis、Vue 3、TypeScript、Docker

**项目链接**：[源码](https://github.com/ZeroMadLife/sage-agent)｜
[技术博客](https://blog.sagecompanion.top/)

**核心设计与实现**：

- **RAG 评测与精确混合检索**：基于 LangGraph、PostgreSQL、pgvector、FastAPI 的 9 份官方
  快照建立 80 条 `dev/calibration/frozen-test` 版本化评测集，将 SQLite 原型迁移为 PostgreSQL
  `GIN + ts_rank_cd + pgvector exact + RRF`；在同一 Corpus/Eval 上比较 FastEmbed 384、百炼
  1024、豆包 2048 维 Embedding，依据 selection 排序与成本证据选择百炼进入 frozen final，
  相对 Hashing hybrid 将 Recall@10 从 0.889 提升至 1.000、MRR 从 0.683 提升至 0.806。
- **上下文预算与 Artifact 按需装载**：针对 Shell、Fetch 等大工具结果反复进入 prompt 导致
  上下文膨胀，将超过 16 KiB 的完整结果 offload 到 session/run scoped Artifact Store，模型
  仅保留最多 200 行、12,000 字符预览与 `artifact_ref`；结合六级压力控制、turn-boundary
  compaction 和 emergency 阻断，实现完整证据与有界模型视图分离，授权宿主路径可按作用域读取全文。
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

- 构建 9 份官方快照、80 条分层 Eval 的 PostgreSQL hybrid RAG，使用
  `GIN + ts_rank_cd + pgvector exact + RRF`；对比 FastEmbed、百炼、豆包三类 Embedding 后，
  百炼在 frozen test 相对 Hashing hybrid 将 Recall@10 `0.889 -> 1.000`、MRR
  `0.683 -> 0.806`，并以 selection/test 门禁决定语义 Provider、Cross-Encoder 与 HNSW
  均不默认启用。
- 面向 Shell/Fetch 大结果导致的 prompt 膨胀，设计六级上下文压力控制与 Artifact offload：
  超过 16 KiB 的全文按 session/run 保存，模型仅消费 200 行/12,000 字符预览和稳定引用，
  授权宿主路径可 scoped read，配合 turn-boundary compaction 与 emergency 阻断。
- 将 Pydantic 参数校验、permission、policy、approval、Sandbox 与结构化 Timeline 串成统一
  Harness；结合 checkpoint、lease/fencing 和 persist-then-push 支持断线重放、审批恢复与
  过期写入拒绝，不暴露模型原始 CoT。
- 强化 Rootless Container Sandbox 并实现 Memory lifecycle：Sandbox live audit 10/10；长期
  事实支持 CAS 撤回/替代，consolidation 保持 proposal-only，40/40 场景验证生命周期与隔离。

## 投递边界

- `0.889 -> 1.000` 与 `0.683 -> 0.806` 是 20 条 frozen test（18 条可回答）上百炼
  `text-embedding-v4` 相对 Hashing hybrid 的离线结果；test 没有 `semantic_paraphrase` case，
  candidate 因此保持 opt-in；
- Eval/Gold 由 AI/Codex 辅助构造，通过冻结官方快照、source anchor、Schema、Hash 与
  `leakage_group` 自动校验，没有独立人工逐条审核；
- 100k exact P95 `93.906 ms` 来自 384 维 synthetic fixture 的本机规模门禁，不是生产 SLA；
  正式结果没有触发 HNSW；
- `10/10` 是 Docker Desktop Level 1 live audit，不等同于内核级逃逸证明；
- `40/40` 是确定性 Memory 生命周期场景，不是自然对话记忆准确率；
- 当前不开放在线演示，正式投递只提供源码与技术博客；
- 不写“完整安全 CoT”：Sage 保存结构化运行证据，不保存或展示模型私有推理链。
