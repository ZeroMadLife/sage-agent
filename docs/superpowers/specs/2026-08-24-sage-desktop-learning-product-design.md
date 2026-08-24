# Sage 桌面端长期学习产品设计

> 状态：产品方向已确认，实施尚未开始。首发平台为 macOS Apple Silicon；Windows x64 保留打包契约。本文已确定首版关键协议，具体实现仍须按 M0-M3 验证后晋级。

## 1. 目标

Sage 是一个本地优先、可恢复的长期学习陪伴。用户从一个自然目标开始，经过诊断、计划、外部事实检索、练习、反馈和复盘，下一次可以从上次的稳定状态继续，而不是重新开始。

首个纵向场景是“学习 Sage 自身的 Task DAG、Checkpoint 与 Resume，并完成一次闭卷解释”。该场景使用仓库已有源码、规格和测试作为资料，能够同时验证 Goal、Learning Intent、RAG、Harness、Artifact、Practice、Mastery、Memory 和 Resume。

后续可复用同一流程学习金融、投资、历史、编程等主题。外部主题需要 Web 或用户提供的知识源；编程实践需要 Practice Engine 和 Sandbox。

## 2. 范围边界

### 包含

- macOS 一键启动的 Tauri 2 桌面壳；
- Vue/Vite 学习工作台；
- Python/FastAPI/Harness sidecar 的生命周期管理；
- Goal → Plan → Learn → Practice → Evidence → Evolve 学习闭环；
- RAG、Memory、Goal、Mastery、Artifact、Timeline、Checkpoint 的职责分离；
- sidecar 崩溃、窗口关闭、断线和 Provider 失败后的可恢复体验；
- Local/Cloud 首次启动选择、Provider 配置、能力诊断和安全存储。

### 不包含

- 无限递归或自由并发的多 Agent 系统；
- 将第三方 `deeptutor_skills` 的 Prompt、HTML、CSS、JS 或素材直接复制到 Sage；
- 在主应用中直接执行模型生成的 HTML/JS；
- 用阅读时长、点击次数或模型自评直接判定掌握；
- 把 RAG 内容当作用户长期记忆；
- 未经提案和批准把模型输出写入 active memory；
- 首版强制安装 Docker、PostgreSQL、Redis 或 Web Search。

## 3. 产品主线

```text
用户学习目标
  -> 诊断已有基础与缺口
  -> 生成有限学习计划
  -> 按单元检索事实并生成可引用材料
  -> 练习 / 回忆 / 真实实践
  -> 写入 Mastery Evidence
  -> 生成总结与下一步建议
  -> 记忆提案，用户批准后才进入长期状态
```

M2 交付后，每个学习单元必须有稳定 ID、Goal 绑定、source/revision 引用和 Artifact 引用。新建的 LearningPlan 使用独立 `learning_plan_id/revision/learning_plan_hash`；现有 TurnContextPlan 保留 `turn_context_plan_hash`，Task DAG 保留 `dag_hash`。恢复分别校验三种身份，不能重新解释输入后生成另一张学习计划或执行图。

## 4. 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Tauri 2 Desktop (macOS arm64)                              │
│  Vue 工作台 · Supervisor · 动态 loopback · Keychain        │
│  signed package · OAuth loopback · 结构化日志 · 诊断包     │
└──────────────────────────────┬───────────────────────────────┘
                               │ readiness / health handshake
┌──────────────────────────────▼───────────────────────────────┐
│ FastAPI + Sage Harness sidecar                              │
│ Auth/Owner · Intent Gate · TurnContextPlan · Task DAG        │
│ Permission/Policy/Approval/Sandbox · Timeline · Checkpoint   │
│ Artifact Store · EvidenceBundle · Resume                    │
└──────────────┬─────────────────────┬─────────────────────────┘
               │                     │
       ┌───────▼────────┐    ┌───────▼─────────┐
       │ Knowledge / RAG │    │ Memory / Goal   │
       │ facts + citation│    │ state + history │
       └───────┬────────┘    └───────┬─────────┘
               └──────────────┬──────┘
                              ▼
                     Practice / Mastery
```

桌面壳不复制 Harness、RAG、Memory 或会话状态。Vue 只投影服务端 Timeline、Artifact、Goal、Mastery 和运行状态。

## 5. 模块职责

| 模块 | 负责 | 明确不负责 |
| --- | --- | --- |
| Tauri Desktop | 安装、sidecar、动态端口、Keychain、更新、日志、崩溃熔断 | 学习编排、权限判定、第二套会话状态 |
| Vue 工作台 | Plan/Learn/Practice/Timeline 视图与用户动作 | 猜测运行状态、保存权威状态、展示 CoT |
| FastAPI/Harness | Owner、Intent admission、上下文计划、DAG、工具、安全、Checkpoint、Resume | 让模型绕过服务端权限 |
| Skill Registry | 教学策略、知识单元结构和练习策略 | 扩大 tool scope、写 active memory |
| Knowledge/RAG | 来源、revision、检索、证据、citation | 保存用户偏好、掌握度或学习历史 |
| Memory | 用户长期愿望、偏好、约束、获批事实和学习经历引用 | 复制 Goal 完成条件/进度，或取代 RAG/Mastery |
| Goal | 学习目的、能力项、完成条件、revision | 直接执行工具 |
| Mastery Ledger | 基于练习证据重算能力状态 | 接受模型直接写入的百分比 |
| Artifact Store | 当前已有 run-scoped 工具结果；M2 扩展学习产物合同 | 作为事实权威或权限来源 |
| Timeline | 可审计事件顺序和状态投影 | 保存完整 Prompt、CoT 或私密正文 |
| Checkpoint | 恢复所需的动态图状态和引用 | 承担长期用户记忆 |

## 6. 一次学习 Turn

```text
1. Tauri 启动 sidecar，完成动态端口与 readiness handshake
2. 校验登录身份、owner、workspace 和会话归属
3. TaskIntent + LearningIntent 收窄模式、来源和工具候选
4. 服务端冻结 TurnContextPlan（scope、revision、预算、capability、turn_context_plan_hash）
5. Retrieval Gate 按需读取 semantic/episodic memory、Knowledge/RAG 或 Web
6. 简单问题直接回答；复杂目标提出受限 LearningPlan，必要时再生成 Task DAG
7. Research 收集证据；Synthesize 生成 Learn Artifact；Practice 只负责练习与回执
8. 每个工具动作依次经过 Permission → Policy → Approval → Sandbox
9. 服务端组装 EvidenceBundle，生成带 citation 的 Artifact
10. 用户完成练习；确定性 code_test 经服务端验证后进入 Mastery Evidence，开放题先保留 Judge Artifact
11. Timeline 输出进度，Checkpoint 保存恢复状态
12. 中断后分别校验 learning_plan_hash、turn_context_plan_hash、dag_hash、owner、scope 和 Skill/MCP revision
13. 生成总结和下一步建议；模型推导的长期用户事实只产生 Memory Proposal
```

意图识别只做 admission，不授予权限。LearningPlan 是产品学习路线，TurnContextPlan 是单轮冻结输入，Task DAG 是本轮执行图，Checkpoint 只保存动态恢复状态和不透明引用；四者不能共用一个 hash 或互相替代。

## 7. DeepTutor 思想的 Sage 映射

只借鉴 `jackley-dev/deeptutor_skills` 在提交 `f2721313875a8ecfca9b11ea5386be6e96cfde59` 中体现的教学结构：先规划知识点，再按单元生成，最后总结。该仓库实际是一个 Skill、提示词摘要和静态 Transformer HTML 示例集合，没有可验证的 runtime、RAG、Memory、Checkpoint、Resume 或评测实现；仓库也没有发现 LICENSE/NOTICE/COPYING 文件。

| 教学概念 | Sage 映射 | 边界 |
| --- | --- | --- |
| DesignAgent | 父 Agent 提出受限 LearningPlan，必要时派生 Task DAG | 两类合同分别校验 revision、预算和 hash |
| `knowledge_points` | Goal 绑定的 `KnowledgeUnit` 候选 | 必须绑定 source revision 和 citation |
| Research | 只读 child 收集 revision-bound hits、citations 和 source refs | 不直接生成 Mastery，不扩大 tool scope |
| Synthesize | 主运行或有界 child 基于 EvidenceBundle 生成 Learn Artifact | 不编造证据，不强制每单元都创建 child |
| Practice | 运行练习并返回 PracticeReceipt/Mastery candidate | 不承担教学内容检索或写 active memory |
| GuideManager | Timeline/UI 对 DAG、Artifact 和当前单元的投影 | 不新增第二套状态机 |
| SummaryAgent | 基于 Timeline、Practice receipt、Evidence 和 Mastery projection 总结 | 不以模型自评替代掌握证据 |

LearningPlan、KnowledgeUnit 和长期 Learning Artifact Store 都是 M2 新合同，不是当前现成能力。Learning Artifact 至少包含 `artifact_id/kind`、Goal/Plan/Unit 绑定、`content_hash`、媒体类型、evidence refs、source revisions、状态、幂等键和保留策略；Checkpoint 只保存不透明引用。生成内容首版使用 Markdown 或受控卡片，任意 HTML/JS 如未来开放，必须先经过隔离渲染、资源白名单、哈希和 Sandbox 门禁。

## 8. 记忆、RAG 与 Mastery 分层

```text
Working       当前 Turn 请求与观察的只读临时投影
Episodic      EpisodeReference 或获批的 DurableEpisodeSummary
Semantic      用户确认的长期愿望、偏好、稳定约束和事实
Procedural    独立治理的 versioned Skill / Procedure
Goal          当前目标、能力项和完成条件
Mastery       可由练习证据重算的能力账本
Checkpoint    运行恢复状态
RAG           外部事实、来源 revision、retrieval hits 和 citation
EvidenceBundle Harness 统一组装的 RAG/Memory/Web/Practice 证据
```

### 写入

只有显式 `remember`、run 结束后的 episode consolidation，以及模型推导的长期用户事实进入 Memory proposal/audit 链。`pending → approved → active` 继续使用 revision CAS；纠错通过 supersession/retraction，不允许 recency 静默覆盖冲突。

Goal 使用独立 Goal Store 和 revision/CAS；Mastery 使用 `PracticeReceipt → 服务端验证与 criterion 绑定 → durable outbox → MasteryLedger`，不绕道 Memory Proposal。Semantic Memory 最多保存长期愿望或 `goal_id + revision` 引用，不复制 Goal 完成条件、进度或 Mastery 投影。

Working Memory 不是权限或冻结计划的权威；Permission/Policy/Approval、TurnContextPlan 和 LearningPlan 仍以各自服务端合同为准。Timeline 临时投影生成 `EpisodeReference`；只有经过 proposal 审阅的受限摘要可成为 `DurableEpisodeSummary`。删除需区分可删除派生内容与按审计策略保留的最小事件字段。

Procedural 能力由 versioned Skill/Procedure 承载，使用独立 proposal、policy、激活范围、版本和效果评测，不进入普通 Memory facts。

### 召回

Retrieval Gate 先判断是否需要 Memory，再按 semantic、episodic 和 Goal gap 分开预算。后续可评估 lexical、dense、entity、temporal 和 recency 的融合，但所有召回都要返回 provenance、原因、命中、token 和 latency receipt。RAG、Memory、Web 和 Practice 分别产出引用或 receipt，最终 EvidenceBundle 只能由 Harness/Context Assembly 统一组装。

### 纠错与遗忘

用户可搜索、批准、拒绝、纠正、撤回、导出和删除。删除必须级联到 embedding、索引、缓存和 episode projection，并验证删除内容不再被召回。完整控制面是后续产品切片，不把当前 40/40 生命周期评测包装成自然语言记忆准确率。

## 9. 桌面端生命周期

### 9.1 进程与握手协议

首版采用 Tauri 2 Rust Host + Vue/Vite + PyInstaller one-dir Python sidecar。M0 先做 packaging spike，验证 Python 3.12、Sage Harness、SQLite/checkpoint、TLS、`cryptography`、`psycopg` 可选依赖、资源文件和 hidden imports；验证通过后才进入发行壳。sidecar 作为 Tauri `externalBin`，与 App 使用同一版本、同一签名和同一更新包。

sidecar 自行绑定 `127.0.0.1:0`，通过继承的匿名 pipe 向 Rust 报告 `{pid, port, instance_id, api_version, build_sha, nonce}`。Rust 校验进程身份、版本和 nonce 后，才把 endpoint 交给 Vue。HTTP、SSE 和 WebSocket 都校验每次启动生成的短期 bearer、Host 和 Origin，生产默认禁用 CORS；增加 single-instance lock、orphan sidecar 清理和版本不兼容 fail closed。短期 bearer 只存在内存，不写日志、环境变量、SQLite 或 URL。

### 启动

```text
迁移锁/备份检查
  -> 启动 API sidecar
  -> 动态 loopback 端口
  -> readiness/health handshake
  -> capability 检查
  -> 加载 session/timeline
  -> WebSocket/SSE reconnect
```

Supervisor 负责 stdout/stderr、优雅退出、退避重启和连续失败熔断。首版不拆独立 Worker，沿用现有 API lifespan。健康合同拆为：`/health/live` 仅表示进程存活；`/health/ready` 返回 API/build/schema/storage/checkpoint 兼容性；`/capabilities` 返回 Provider、Knowledge、Sandbox 等 `ready/degraded/blocked + reason_code + action`，且不包含凭据或用户正文。

### 首次启动

```text
启动 Sage
  -> 检测数据目录与迁移
  -> 选择 Local Workspace 或 Cloud
  -> Provider 配置（Keychain）
  -> API/Harness/Knowledge/Sandbox 能力诊断
  -> 选择学习空间
  -> 开始学习目标
```

Local 默认 SQLite canonical/default；PostgreSQL projection、Container Sandbox 和 Web Search 显示为可选能力。Local Provider key 由 Rust 写入 macOS Keychain，SQLite 只保存 `key_ref/key_hint`；sidecar 通过继承 pipe 获取一次启动所需的内存副本，禁止长期环境变量传递。Cloud Provider key 只保存在服务器。Keychain 锁定、拒绝访问、轮换、删除、注销和备份排除都必须有显式错误语义。

M1 的 Cloud 登录固定采用“系统浏览器 + PKCE + 一次性 state + Rust 临时 loopback callback”。回调必须校验 state、过期时间和一次性消费；Rust 用授权码换取桌面会话凭据并存入 Keychain，Vue 不接触 access/refresh token，sidecar 作为受限 broker 调用 Cloud API。首版不同时维护 deep-link 回调，避免两套认证路径。

### 关闭与崩溃

关闭时停止接收新 run，等待 grace period 后终止 sidecar。恢复按以下矩阵执行；审批和有副作用写操作绝不自动批准或无提示恢复。Crash budget 为 10 分钟内最多 3 次，持久化到 Desktop Host 状态；超过后熔断并进入诊断页。

| 故障 | sidecar | run 状态 | 恢复策略 |
| --- | --- | --- | --- |
| 窗口隐藏/托盘 | 保持运行 | 不变 | 仅重连 UI |
| WebSocket/SSE 断开 | 保持运行 | 不变 | 从 sequence cursor 重放 |
| sidecar 崩溃 | 重启 | interrupted | 校验身份与 Checkpoint，用户确认后 Resume |
| 用户显式退出 | 优雅停止 | interrupted/cancelled 按契约 | 下次启动展示可恢复项，不自动继续 |
| 机器重启 | 不存在 | 由 durable state 重建 | 完成迁移/版本校验后由用户确认 Resume |

### 9.2 Tauri 最小权限与 macOS 发行

Tauri capability 只允许固定 external binary 和固定参数，不暴露通用 shell；文件访问限制在用户明确选择的 workspace 和应用数据目录。生产禁用任意远程导航和 devtools，CSP 默认拒绝外部资源，OAuth loopback 只接受预期 path/state。越权命令、恶意页面调用和跨 workspace 访问必须失败。

M0 发行门禁包含 Developer ID 签名、notarization、stapled DMG、Gatekeeper 和 quarantine 首启；Tauri updater 签名不等于 macOS 公证。M3 再实现签名 updater、channel/manifest、下载中断重试、签名拒绝和迁移后健康门禁。在存在独立 bootstrap 或 previous bundle 恢复协议前，不承诺自动回滚。

## 10. 工作台信息架构

```text
学习任务
  ├─ Plan：目标、知识单元、进度、下一步
  ├─ Learn：解释、引用、反例、受控互动
  ├─ Practice：闭卷解释、判断题、代码测试
  └─ Timeline：检索、审批、工具、Artifact、Checkpoint、失败原因
```

右侧事实栏只展示会改变用户下一步决策的内容：当前 Goal、运行状态、Evidence、待审批 Proposal、阻塞原因和恢复入口。内部 Prompt、CoT、密钥、完整证据正文和私有路径不进入 UI。

## 11. 降级与错误语义

| 异常 | 用户状态 | 恢复动作 |
| --- | --- | --- |
| Provider 失败 | 当前单元暂停 | 重试或切换 Provider，保留 Timeline |
| sidecar 崩溃 | 任务中断，可恢复 | 退避重启并校验 Checkpoint |
| 端口占用 | 启动诊断 | 动态换端口 |
| DB 迁移失败 | 数据迁移未完成 | 保留备份，阻止运行并提供恢复入口 |
| 单元失败 | 当前 child failed | 只重试该 child，复用成功单元 |
| 审批未完成 | 等待审批 | 不自动执行下一步 |
| 证据不足 | 拒答或请求补充来源 | 不把猜测写成事实 |

能力状态统一为 `ready / degraded / blocked`，每个降级都要给出原因和用户可执行动作。

首版能力矩阵：

| 环境 | 可用 | 明确阻止 |
| --- | --- | --- |
| 无 Docker | 对话、SQLite RAG、闭卷练习、无副作用评分 | Shell、写文件、代码执行 |
| Docker Sandbox ready | 经 Permission/Policy/Approval 的受控实践 | 未授权网络、越界路径、绕过审批 |
| PostgreSQL 未配置 | SQLite canonical/default | 宣称长书 PostgreSQL 检索已启用 |
| Web Search 未配置 | 本地资料学习 | 依赖最新外部事实的无证据回答 |

`local_workspace` 只能描述本地工作区能力，不能冒充 Container Sandbox。

## 12. 分段交付

### M0：桌面壳与 sidecar

先完成 PyInstaller one-dir packaging spike，再由 Tauri 打开现有 Vue build。交付安全 loopback 握手、短期 bearer、single-instance、orphan 清理、liveness/readiness/capability、退出与恢复矩阵、退避重启和诊断日志；同时完成 signed/notarized/stapled DMG 和 Gatekeeper 首启门禁。

### M1：首次启动与 Provider

Local/Cloud 选择、Rust-owned Keychain、Provider 添加/探测/默认模型、固定 loopback OAuth、能力诊断和 workspace 选择。

### M2：学习恢复纵向闭环

新增 LearningPlan/KnowledgeUnit/Learning Artifact 合同，完成 Goal → Plan → Research → Synthesize → Practice → Mastery → Resume 的 Sage 源码学习场景。首版只有服务端验证的 code_test pass/fail 自动进入 Mastery；闭卷解释保留为带 rubric/model revision 的 Judge Artifact，不单独判定 mastered。

### M3：记忆控制面、诊断与更新

Proposal/Memory/Recall receipt、纠错/撤回/删除、诊断 ZIP、签名 updater 和完整 macOS smoke；同步建立 Windows x64 打包契约。30-50 条学习 Gold 和更完整的自然语言 Memory benchmark 作为独立 Eval 切片，不阻塞 M0/M1。

## 13. 验收门禁

### 功能与恢复

- 干净 macOS Apple Silicon 安装后无需 Python、Node、uv 或 Docker 即可进入向导；
- macOS 真机覆盖最低系统版本、离线首启、中文/空格路径、Keychain 锁定、sleep/wake、机器重启、多实例、磁盘满、只读数据目录、升级保留数据和卸载策略；
- sidecar 崩溃、窗口隐藏、显式退出、连接断开和机器重启分别符合恢复矩阵；
- Resume 分别校验 `learning_plan_hash`、`turn_context_plan_hash` 和 `dag_hash`；
- 成功 child、Artifact、Evidence 不因重试或 Resume 重复；
- Skill/MCP catalog、scope、owner 或 checkpoint 漂移时 fail closed；
- Provider、数据库、Knowledge、Sandbox 的 ready/degraded/blocked 状态可解释。

### 证据与安全

- 可引用事实全部来自当前 EvidenceBundle，过期 citation 为零；
- 跨 workspace Artifact、篡改 hash 和未授权 memory ref 全部拒绝；
- Provider key、cookie、OAuth code、用户正文不进入日志或诊断包；
- loopback HTTP/SSE/WS 的 bearer、Host、Origin、instance 和 nonce 校验不可绕过；
- 主应用不执行不可信生成 JS，外部资源默认不加载；
- 桌面通知、托盘或 Resume 不能绕过 Permission → Policy → Approval；Sandbox 不可用时副作用工具必须 blocked。

### 学习质量与成本

- LearningPlan schema、单元 ID、Goal/source/revision/citation 绑定为 100%；
- code_test receipt 的服务端验证和幂等写入为 100%，跳过练习不产生 Mastery Evidence；
- 开放题 Judge Artifact 必须记录 model、rubric revision、evidence refs 和可失效状态，不能单独判定 mastered；
- 独立 Eval 切片建立 30-50 条 review Gold，固定 dev/calibration/test split；
- 以 Claim Evidence Coverage、Answer Correctness、Correct Abstention、False Acceptance、Goal continuity 和 token/latency 为阶段指标，不把候选阈值包装成生产 SLA；
- 每个 child、token、Artifact、citation、停止原因和恢复结果均可审计。

## 14. 已实现与待开发边界

### 已实现且默认可复用

- Vue/FastAPI Web Shell、Goal/Intent admission、Task DAG、SSE/WebSocket、Timeline、Approval、Checkpoint/Resume；
- SQLite canonical/default、proposal-first Memory 生命周期和分层 Eval 原语。

### 已实现但受配置或运行环境控制

- PostgreSQL 检索投影、Container Sandbox、Web Search、Cloud Provider；
- Goal、Mastery、Artifact、Context Assembly 等底层原语，其中部分默认仍是 shadow、可选或尚未形成学习产品纵向接线。

### 本设计新增且尚未交付

Tauri 宿主、Python sidecar 打包与监督、安全动态端口、Keychain、桌面 OAuth、首次启动向导、桌面诊断与更新、LearningPlan/KnowledgeUnit/Learning Artifact、完整 Memory 控制面，以及 Plan/Learn/Practice/Timeline 统一工作台。

## 15. 参考来源与事实边界

- Sage 当前仓库：`README.md`、`docs/superpowers/specs/`、`docs/evals/`、`core/`、`packages/sage_harness/`、`api/`、`frontend/`；代码与测试是当前行为的最终依据。
- DeepTutor Skills：`jackley-dev/deeptutor_skills@f2721313875a8ecfca9b11ea5386be6e96cfde59`，只读研究；该仓库无可验证运行时和明确许可证文件。
- 桌面路线参考：Tauri 2 sidecar/updater/deep-link 文档，Electron autoUpdater/safeStorage 文档，Jan/GPT4All/AnythingLLM 的公开仓库结构。
- Memory 研究参考：Mem0 的公开记忆抽取/融合召回与 benchmark 资料，OpenViking 的 context database、L0/L1/L2 和 session 沉淀设计。它们只提供可借鉴思想，不改变 Sage 的 proposal-first、owner 隔离、RAG 事实边界和 Mastery 账本。

借鉴边界：

| 参考 | 借鉴 | 不采用 |
| --- | --- | --- |
| Mem0 | 多信号召回、公开长期记忆 benchmark | 模型自动写入 active memory |
| OpenViking | L0/L1/L2 按需加载、可观察检索轨迹、episode 沉淀 | 用统一 Context Database 覆盖 RAG、Memory、Goal、Mastery、Skill 的 canonical 边界 |
