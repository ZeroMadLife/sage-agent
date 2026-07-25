# Sage Harness Evidence v2 设计

> 日期：2026-07-25  
> 基线：`dev/sage-v7@577a744`  
> 状态：已确认，按三个可独立验证的纵向切片实施

## 1. 问题与目标

Sage 已有 Knowledge 检索、Container Sandbox 和 proposal-first Memory，但现有证据不足以支持更强的项目表述：

1. RAG 的 50 条查询仍指向旧语料结构，标注只有文档级 `relevant_sources`，历史多模型脚本也不在当前仓库；
2. Container Sandbox 已禁网并限制 CPU、内存和 PID，但容器内仍使用 root，且整个 workspace 可写；
3. Memory 能批准或拒绝 proposal，却不能对已批准事实做正式更正或撤回，Dream 也未形成多证据 consolidation；
4. 简历缺少由当前 commit、当前语料和当前测试生成的机器可读量化证据。

本阶段目标不是增加更多架构名词，而是让三项能力形成“实际问题 -> 受控机制 -> 可重复评测 -> 简历证据”的闭环。

## 2. 共同原则

- 机器可读报告必须记录 source commit、语料 revision、数据集 revision、Provider、参数、延迟和逐 case 结果；
- 评测集、评测器与离线基线可以提交，Provider key、模型 endpoint、本机绝对路径和缓存不得提交；
- 所有长期事实变化 append-only，不靠覆盖或物理删除伪造“最新状态”；
- 安全能力必须由攻击回归验证，不能只断言某个 Docker flag 已配置；
- 简历只引用当前分支可复现的结果，不继续使用缺少脚本或语料映射的历史数字。

## 3. 切片 A：RAG Benchmark v2

### 3.1 数据契约

提交 200 条查询，固定为以下构成：

| 类型 | 数量 | 验证目标 |
| --- | ---: | --- |
| 旧查询迁移 | 50 | 保留历史回归，不计作隐藏测试唯一证据 |
| 真实用户式问题 | 60 | 避免直接复制标题或章节名 |
| 改写、缩写、中英文混合 | 30 | 验证语义召回与词法降级边界 |
| hard negative | 20 | 区分相似概念、错误层级和邻近章节 |
| 多文档与版本问题 | 20 | 验证跨来源召回和 revision 判断 |
| 无答案问题 | 20 | 验证空召回或受控拒答 |

每条查询至少保存：

```text
id, query, category, split, answerable, provenance,
relevant_passages[{source, section, relevance}], required_claims, forbidden_claims
```

`relevant_passages` 在运行时解析为当前 corpus 的 chunk ID。语料 manifest 保存每个文件的 SHA-256；语料漂移时 benchmark fail closed，而不是静默重算标签。

### 3.2 指标与报告

- 检索：`Recall@K`、`Precision@K`、`MRR`、`NDCG@K`、`HitRate@K`、无答案准确率、P50/P95；
- 按 `category`、`split` 和 `answerable` 输出子集指标；
- 报告包含每个 case 的 retrieved chunk/source、rank、score 与判断；
- CI 运行离线 Hashing/FTS5 smoke，真实 Embedding 通过显式 Provider adapter 手动或定时运行；
- 阈值在首个冻结 baseline 后确定，禁止先写一个好看的目标再调数据集迎合目标。

### 3.3 非目标

- 本切片不把 HashingEmbedding 宣传成语义检索；
- 不提交任何第三方 key、endpoint 或本地缓存；
- 不用单一 LLM judge 替代人工核对的 qrels；
- 首版只建立 retrieval 与 citation-ready 证据，不伪造完整 answer correctness 数字。

## 4. 切片 B：Sandbox Level 1 Hardening

### 4.1 当前保留能力

保留 Rootless Docker daemon、`--network none`、只读 rootfs、独立 `/tmp`、PID/CPU/内存限制、工具超时和 workspace path containment。

### 4.2 新增最小权限

- daemon 必须运行在 Rootless Docker（本地允许 Docker Desktop VM），容器内 namespace root 映射为非特权宿主用户；`HOME` 固定到临时目录且不透传宿主环境；
- 显式 `--cap-drop ALL` 与 `--security-opt no-new-privileges`；
- 显式要求 Docker 默认或指定 seccomp profile，禁止 `unconfined`；
- 限制 swap、文件描述符和单文件大小；
- 镜像 digest 固定作为后续部署切片；本阶段验证容器实际 image reference 与配置一致，不把 tag 等同于不可变镜像；
- 不向容器传递宿主 Provider key、Docker socket或服务端环境；
- 每个 thread 独立容器，终态清理且重用前校验实际安全配置。

整个可写 workspace 是本切片最大的结构性风险。首版采用“只读源 + 受控可写 overlay/work 目录 + 宿主侧 diff 应用”需要兼容现有 Coding 文件工具，若无法在一个小版本内保持 API 契约，则先锁定最小权限 flags 与 mount admission，并将 overlay 作为下一独立切片，不能把它写成已交付。

### 4.3 攻击回归

至少覆盖：网络访问、容器 root 身份、capability、`no-new-privileges`、fork bomb、内存/文件写入、workspace 越界、symlink、跨 thread、超时残留进程、容器配置漂移和关闭后复用。

gVisor/Firecracker 属于 Level 2，不在本切片引入。

生产 workspace 通过 ACL 授权给 `sage-sandbox` 宿主用户。Rootless Docker 中容器内 UID 0
映射到该非特权宿主 UID；改用任意容器 UID 会映射到 subordinate UID，从而失去现有 workspace
写权限。首版因此保留 namespace root，并依赖 rootless daemon、`cap-drop ALL`、
`no-new-privileges`、seccomp 和只读 rootfs 叠加约束。后续若引入只读源 + overlay，可再把
overlay 所有权交给固定容器 non-root UID；当前简历不得宣称“容器内 non-root”。

## 5. 切片 C：Memory Consolidation 与 Retraction

### 5.1 事实模型

Working、Episodic、Semantic 与 Consolidation 保持不同生命周期。SQLite `MemoryStore` 是 canonical store，Markdown 仅为可恢复投影。

已批准事实增加以下生命周期：

```text
active -> superseded
active -> retracted
```

更正创建新事实 revision，并用 `supersedes` 指向旧事实；撤回保存 reason、actor、source ref 和 event。旧事实不物理删除。

### 5.2 Consolidation

Consolidation 从已完成 run 的有来源 episodic evidence 生成候选，依次执行：

```text
候选抽取 -> 精确去重 -> 冲突/替代检测 -> 证据评分 -> pending proposal
```

Consolidation 无权批准。首版采用确定性输入与可插拔 extractor，不把模型输出直接升级为事实，也不自动修改 procedural memory、Skill 或系统提示。

### 5.3 召回

- 只召回 `active` 事实；`pending`、`superseded` 和 `retracted` 默认不进入模型上下文；
- 冲突没有明确 supersession 时保留双方并标记 conflict；
- 排序至少记录 relevance、recency 和 provenance，不让 recency 静默覆盖批准状态；
- workspace/session 作用域与 remote-content 数据边界保持不变。

### 5.4 评测

提交确定性 Memory 场景集，覆盖相关事实召回、重复合并、冲突保留、更正生效、撤回隔离、workspace 隔离、proposal 未批准不可见、重启恢复和指令注入不提权。

输出总 case 数、通过率、每类通过率和失败 case。简历只使用该报告中的数字。

## 6. 交付与简历证据

三个切片分别形成职责清晰的 commit，并在同一 PR 中保留独立验证记录。阶段完成必须提供：

- 定向 pytest、相关后端回归、Ruff、mypy、生产构建与 `git diff --check`；
- `reports/` 下机器可读 benchmark 摘要或 CI artifact 生成命令；
- 当前能力、未完成边界和威胁模型；
- Obsidian `sage-learning` 收口记录。

简历应按“问题、机制、证据”表述，例如：

```text
针对 Shell/Fetch 等大工具结果反复进入 prompt 导致上下文膨胀，设计六级上下文压力控制，
将完整结果 offload 为 session/run scoped Artifact，仅按需 onload 有界片段；以压力回归记录
上下文 token、checkpoint 体积和恢复结果。
```

具体百分比和 case 数必须在本阶段报告生成后再填入。
