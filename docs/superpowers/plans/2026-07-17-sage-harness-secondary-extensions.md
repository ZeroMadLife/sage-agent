# Sage Harness 二次拓展实施计划

> 日期：2026-07-17
>
> 状态：执行中；H2.5C 已合入，H2.6A 发布门禁已通过，待 PR 合入
>
> 基线：`dev/sage-v7@b001441`
>
> 上游参考：`bytedance/deer-flow@1ae02913ea94d4de29e8bb30b6de36916cdb9192`、`ShenSeanChen/waku-agent@cfb5e96f5a78b6289c8b50a21932cb79c794e07a`
>
> 前置设计：`docs/superpowers/specs/2026-07-16-sage-chat-harness-v2-design.md`、`docs/superpowers/specs/2026-07-15-sage-v7-personal-assistant-knowledge-evolution-design.md`

## 1. 计划结论

Sage 下一阶段不再复制 DeerFlow 的 Wave 0-6，也不新建第二套 Agent Runtime。当前 `deerflow_v2` 已经成为新会话默认值，真实 MCP Manager、deferred tools、Skill 激活、Subagent、Memory proposal、Knowledge retrieval/citation 和 evidence learning 已存在。

二次拓展的目标是把这些离散能力组成一个面向个人成长的闭环：

```text
用户设定长期学习目标
  -> Harness 冻结本轮目标与 Surface Context
  -> Retrieval Gate 判断需要 Memory / Knowledge / Web / Practice 中的哪些证据
  -> Capability Registry 选择 Tool、MCP、Skill 或 Subagent
  -> Agent 循环执行、观察并继续，直到本轮目标满足或出现明确 blocker
  -> 产生回答、引用、练习结果和 Mastery Evidence
  -> 提出 Wiki / Memory 增量提案
  -> 按证据、风险与用户确认规则沉淀
  -> 增量更新索引和学习目标进度
```

固定顺序：

1. `H2.4 Capability Registry`：统一所有能力的发现、权限和审计语义；
2. `H2.5 Web Evidence`：让网页搜索形成受控、可引用的证据链；
3. `H2.6 Research Subagents`：让长任务能够受限并行检索和综合；
4. `H2.7 Goal and Mastery`：把一次对话接入长期学习目标与可验证进度；
5. `H2.8 Retrieval and Memory`：按需召回语义、情节和程序记忆；
6. `H2.9 Knowledge Evolution`：将可靠证据转成增量 Wiki，并闭环索引与知识缺口。

每一阶段必须是可独立发布的小版本，不等待后续阶段才能恢复当前能力。

## 2. 当前事实基线

### 2.1 已交付，直接复用

| 能力 | 当前实现 | 二次拓展中的位置 |
| --- | --- | --- |
| 原生 Agent 循环 | `packages/sage_harness`、`core/harness/runtime_adapter.py` | 唯一主运行时，不再另建 Loop |
| durable timeline | `SessionEventJournal`、Harness event adapter | 所有 UI、审计和恢复的事实源 |
| Tool / Policy / Approval | Coding tools、policy、LangGraph interrupt/resume | Capability 执行底座 |
| deferred tools | `sage_harness/deferred_tools.py` | Tool、MCP 和 Web 能力按需提升 Schema |
| Skills | `SkillActivationMiddleware`、`allowed_tools` | 程序记忆和显式工作流 |
| MCP | `McpManager`、`ScopedMcpSessionPool`、`LangChainMcpTransport` | 外部能力接入，不再只做目录展示 |
| Sandbox | local/container sandbox、生产门禁 | Shell、抓取器和子代理隔离 |
| Subagent | `task` 工具、Explore child run、生命周期事件 | 扩展静态 Research/Profile 和受限并发 |
| Context / Compact | Durable Context、Context Controller、checkpoint | 长会话预算与恢复 |
| Memory | workspace fact、proposal store、Memory Port | 保持 proposal-only，增加按需召回 |
| Knowledge RAG | hybrid retrieval、stable citation、revision 校验 | 内部可信证据来源 |
| Knowledge Evolution | evidence learning、Wiki proposal、policy | 接收经验证的学习证据 |
| Learning Goal | Git-backed `purpose.md` 和图谱目标对齐 | 长期目标事实源之一 |

### 2.2 当前真实缺口

1. 本地 Tool、MCP、Skill 和 Subagent 各有目录，但没有统一、可查询、带策略元数据的 Capability Registry。
2. 仓库没有生产 Web Search / Web Fetch 路径，也没有网页证据、稳定网页引用、SSRF 防护和来源保存闭环。
3. 当前 Subagent 主要是只读 Explore；缺少 Research profile、并行预算、证据合并和父子任务可恢复语义。
4. `SageThreadState.goal` 只是状态通道；没有 thread goal 命令、typed blocker、目标评估、无进展保护和安全自动继续。
5. Knowledge Learning Goal、会话 Goal 和掌握度还没有明确关联；当前没有可审计的 Mastery Evidence Ledger。
6. Durable Memory 当前主要以最近事实投影进入上下文；尚无显式 Retrieval Gate、相关性召回和语义/情节/程序三类边界。
7. Knowledge 已能从原始 citation 形成 evidence learning，但 Web Evidence 尚未成为合法来源，回答、练习、掌握度与 Wiki 增量没有形成统一闭环。

## 3. 架构红线

### 3.1 只保留一个 Chat Harness

- 主对话、Knowledge、Coding Practice Engine 共用 session、timeline、run、approval 和恢复协议。
- Surface 只提供上下文、能力白名单和展示适配，不拥有独立 Agent Loop。
- 前端外包线可以调整主画布和 Chat Dock，但不能成为新的运行事实源。

### 3.2 三种目标不得混成一个字段

| 类型 | 生命周期 | 用途 | 事实源 |
| --- | --- | --- | --- |
| `TurnObjective` | 单次 run | 本轮要完成什么 | 当前用户消息和冻结 run context |
| `ThreadGoal` | 多轮 session | 明确的完成条件与自动继续 | Harness checkpoint + session journal |
| `LearningGoal` | 跨 session / workspace | 长期能力与知识建设方向 | Knowledge `purpose.md` revision |

`MasteryEvidence` 不是 Goal 状态。它是支持或反驳某项能力进展的证据，例如测验结果、解释评分、代码测试、项目产物和引用质量。

### 3.3 外部内容默认不可信

- Web、MCP、上传文件和知识源正文均按数据处理，不能提升为系统指令。
- Web Search 结果可以服务当前回答，但不能绕过来源摄取和 proposal 直接写入可信 Wiki。
- Provider key、MCP credential 和网页认证信息只在服务端解析，不进入 checkpoint、timeline、prompt 或浏览器。
- 所有远程 URL 必须经过 scheme、DNS/IP、redirect、大小、MIME 和超时策略。
- MCP/Web 提供的名称、描述和 Schema 也属于外部数据，进入 Capability Registry 前必须归一、限长和冲突检查。

### 3.4 不保存模型私有推理

只持久化公开回答、工具调用、阶段、typed decision、引用、评测摘要和 Provider 明确提供的 reasoning summary。不得从模型内部 chain-of-thought 生成“思考过程”。

## 4. H2.4：Capability Registry

### 4.1 用户可见结果

用户在设置或运行审计中能看到当前可用能力来自哪里、是否健康、风险级别、是否需要审批；模型通过一个统一目录发现本地 Tool、MCP、Skill、Web 和 Subagent，而不是依赖散落的名称约定。

### 4.2 契约

新增中立值对象，建议位置：

```text
packages/sage_harness/sage_harness/capabilities/
  contracts.py
  registry.py
  selection.py
```

核心结构：

```python
CapabilityDescriptor(
    capability_id,        # stable: local:read_file / mcp:github:search_code
    name,
    origin,               # local | mcp | skill | subagent | web
    kind,                 # tool | workflow | delegate
    revision,
    description,
    surfaces,
    risk,
    permission,
    deferred,
    remote_content,
    availability,
    timeout_seconds,
    tags,
)
```

Registry 只管理描述符、健康状态和选择，不执行具体能力。执行仍由现有 Tool adapter、MCP Manager、Skill middleware 和 Subagent executor 负责。

### 4.3 纵向切片

#### H2.4A：只读统一目录

- 将 local tool、MCP descriptor、Skill metadata 和 Subagent profile 投影为 CapabilityDescriptor。
- 稳定 ID 不使用本机绝对路径或密钥。
- 目录 revision 由各来源 revision/hash 确定，刷新后可比较差异。
- 新增服务端 API `GET /api/v1/harness/capabilities`，按 owner/workspace/surface 过滤。
- 保留现有 `/coding/mcp/servers` 兼容接口，不立即删除。

#### H2.4B：统一发现与提升

- `tool_search` 查询 Capability Registry，而不是只查询本地 ToolDefinition。
- 搜索结果只返回 bounded metadata；选中后才提升完整 Tool Schema。
- Skill `allowed_tools` 同时约束模型可见 Schema 和执行层，不能因 MCP 重连或 tool promotion 绕过。
- 名称冲突以 stable capability ID 处理；展示名冲突不得静默覆盖。

#### H2.4C：健康度与审计

- 输出 `capability_catalog_updated`、`capability_selected` 和安全的失败摘要。
- 不把每次心跳写入主 timeline；运行级目录只记录 revision 和实际使用能力。
- 增加 availability、首次/最近成功、P50/P95 延迟、失败类别的聚合指标，不保存敏感参数。

### 4.4 测试门禁

- local/MCP/Skill/Subagent 同名不覆盖；
- owner/workspace/thread/surface 隔离；
- stale MCP schema、disabled skill、unhealthy capability fail-closed；
- `allowed_tools` 在 discovery、promotion、execution 三层一致；
- catalog event 不包含 secret、绝对 home path 或完整 schema；
- 现有 Coding 工具、MCP 和 Skill 测试保持通过。

### 4.5 H2.4A 交付记录（2026-07-18）

H2.4A 已按只读边界实现，未进入 H2.4B：

1. `packages/sage_harness` 新增应用中立的 `CapabilityDescriptor` 与确定性 `CapabilityRegistry`，只管理公开元数据，不拥有发现或执行；
2. `core/harness/capability_adapter.py` 将当前 local tool、MCP descriptor、Skill 和只读 Explore profile 投影到统一目录；
3. 稳定 ID、catalog revision 和 source revision 只由公开元数据与哈希组成，不包含绝对路径、完整 Schema、凭据或 owner/workspace 私有值；
4. `GET /api/v1/harness/capabilities` 强制绑定已授权 `session_id`，由服务端派生 workspace 与 MCP scope，并按 surface/origin/availability/text 过滤；
5. MCP 目录只读取当前 owner/workspace/thread scope 的已缓存 catalog；打开目录不会发起连接、discovery 或工具调用；
6. 现有 `/api/v1/coding/mcp/servers`、Tool adapter、Skill middleware、Subagent executor 和模型可见工具集合保持不变。

本地门禁：

- H2.4A 定向：`13 passed`；
- Tool/MCP/Skill/Subagent/Coding API 回归：`116 passed`；
- 后端全量：`1484 passed`；
- Ruff 全仓：通过；
- mypy：`220 source files` 无错误；
- 前端生产构建：通过，仅保留既有 Harness 大 chunk 提示；
- `git diff --check`：通过。

### 4.6 H2.4B 交付记录（2026-07-18）

H2.4B 已按“目录只发现、稳定 ID 才提升、执行仍走原边界”的原则实现：

1. `CapabilitySelectionIndex` 提供按 surface、availability 和 Skill `allowed_tools` 过滤的确定性发现；搜索只返回限长公开元数据，不返回完整 Tool Schema；
2. `tool_search` 改为两阶段协议：先用关键词发现，再以 `select:<capability_id>` 提升单个稳定能力的完整 Schema；展示名冲突必须改用 stable ID，不再静默选择；
3. promotion checkpoint 同时保存 catalog hash、兼容 name 和 stable capability ID；中间件只信任当前 catalog hash 下的 capability ID，旧 names-only checkpoint 自动 fail-closed；
4. MCP wrapper 只附带非敏感 `mcp_tool_id`，Sage adapter 在应用层映射为 stable capability ID；MCP catalog revision、schema hash、健康状态或 Skill allowlist 变化都会使旧 promotion 失效；
5. Skill `allowed_tools` 同时进入 discovery/promotion catalog hash，并继续由 `SkillActivationMiddleware` 在模型可见工具与执行层阻断伪造调用；
6. local Tool、MCP、Skill 和 Explore Subagent 共享同一可查询目录，实际执行仍由 Tool adapter、MCP Manager、Skill middleware 和 Subagent executor 负责。

本批次不扩张 H2.4C：尚未新增 `capability_catalog_updated`、`capability_selected` timeline 事件，也未实现成功率、P50/P95 延迟和失败类别聚合。

## 5. H2.5：Web Evidence

### 5.1 设计选择

首版采用 provider-neutral `WebSearchPort` / `WebFetchPort`，实现一个服务端 Provider adapter，并允许 MCP Web Search 通过 Capability Registry 作为替代来源。Harness 核心不直接依赖 Tavily、Brave 或其他供应商 SDK。

原生 Web Evidence 路径负责稳定证据契约；通用 MCP 路径负责可插拔性。两者最终都必须归一为相同的 `WebEvidenceBundle`，不能让模型看到两种无法追溯的结果格式。

### 5.2 数据流

```text
search_web(query, freshness, domains, top_k)
  -> provider result metadata
  -> fetch_web(url)（按需）
  -> URL / network / content policy
  -> immutable WebEvidenceArtifact
  -> web citation + bounded excerpt
  -> 当前回答引用
  -> 用户选择“保存为来源”
  -> KnowledgeSource snapshot / proposal
  -> Wiki / index / graph 增量更新
```

### 5.3 证据契约

`WebEvidenceArtifact` 至少保存：

- `evidence_id`、canonical URL、original URL；
- title、provider、retrieved_at、content hash；
- bounded excerpt 和引用 span；
- MIME、HTTP status、redirect chain 摘要；
- remote-content / sanitization 标记；
- retention policy 和可选 artifact ref。

当前回答中的网页 citation 绑定 content hash。网页变化后旧 citation 仍指向原快照；不能悄悄显示最新正文。

### 5.4 安全边界

- 只允许 `https`，开发环境可显式允许 `http` 测试 fixture；
- 拒绝 loopback、link-local、private、metadata service、非预期端口和 DNS rebinding；
- 每次 redirect 重新校验目标，并在真正建立连接时校验解析 IP，不能只检查用户提交的 hostname；
- 限制响应大小、解压后大小、并发、总下载量和超时；
- 首版只解析明确允许的 HTML/text/PDF；脚本、表单和可执行附件不运行；
- 网页正文进入模型前使用 remote-content boundary，工具指令不得扩大权限；
- API key 只由服务端环境或 secret store 解析。
- 首版不转发浏览器 cookie、Authorization header 或本机登录态，不抓取需要用户认证的页面。
- 保存来源时记录抓取策略、许可提示和 retention；用户删除来源后，其私有 Evidence Artifact 必须按引用和保留策略回收。

### 5.5 纵向切片

#### H2.5A：Search + 当前回答引用

- 实现 Search Port、Provider adapter、结果归一和 Web citation。
- 支持 `tool_search -> search_web -> final answer` 的完整流式循环。
- 不写 Knowledge，不自动抓取所有搜索结果。

实现状态（2026-07-18）：已形成可独立验收的 H2.5A 切片。Harness 只依赖 provider-neutral `WebSearchPort`，首个服务端 adapter 使用 SearXNG JSON Search API；能力默认关闭，只有服务端同时启用并配置 endpoint 时才进入 Capability Registry。结果归一为预算内 title、HTTPS URL、excerpt、retrieved time、content hash 和稳定 `wcite_` 引用，并带 `remote_content` 边界进入当前回答。该切片不抓取 URL 正文、不持久化网页 Artifact、不写 Knowledge，也不转发浏览器登录态。

同时关闭两项运行时可追溯性缺口：Subagent 类型按规范化 ID 匹配，父 timeline 通过 `operation_ref` 关联 child run；前端事件回放改为 append-only event view，重复进入同一阶段不会覆盖旧记录。断连时发送失败会保留输入草稿，由用户在连接恢复后显式重试。

H2.5A 联调后增加前置收口 `H2.5B0`：不提高 `100000` 本轮硬上限，而是将同一 `run_id` 的累计 token、模型调用和工具调用实时投影到状态画布；`search_web` 另设默认 `2000 tokens` 的单次证据预算，避免长摘要被后续模型循环重复放大。`run_shell` timeout 改为进程组清理并输出可审计错误分类，同时拒绝根目录扫描和无完整网络 timeout 的 shell 兜底。完整契约见 `2026-07-18-sage-h2-5b0-runtime-budget-shell-reliability.md`。

#### H2.5B：Fetch + Artifact

H2.5B 拆为独立小版本：H2.5B0 已交付本轮累计预算、Web Search 单次证据预算与 Shell 进程组超时清理；H2.5B1 交付只读公开 HTTPS HTML 的 `fetch_web`，逐跳执行 SSRF 校验并将完整正文归档为 run-scoped 私有 Artifact，模型与 timeline 只接收预算内 excerpt 和 opaque 引用。H2.5B2 已将 PDF 解析接入现有 Knowledge Job：显式授权后 MinerU 优先，远程任务以持久化 ticket 异步 submit/poll，worker 等待期间释放 lease，失败或超时后回退本地文本层解析。用户确认后的 Knowledge Source 提案仍保留到 H2.5C。

- 实现安全 Fetch Port、HTML 主内容提取和 PDF 文本适配。
- 长正文放 artifact，消息和 context 只保留预算内 excerpt。
- URL policy 和 prompt injection 负例必须先于真实网络 smoke 通过。

#### H2.5C：保存为 Knowledge Source

- 新增 proposal-only `save_web_source`，绑定 evidence hash、目标 workspace 和用户决策。
- 批量研究可以生成一个来源清单提案，不能弹出 N 次确认。
- 批准后走现有 manifest、snapshot、Wiki proposal、index/graph 主链。

实现状态（2026-07-19）：H2.5C 后端与前端审阅闭环已合入 `dev/sage-v7`。Knowledge Source Proposal 使用 durable store、timeline refresh signal、`no-store` list/detail 和 `expected_revision` CAS 审批；事件只作为刷新信号，不能直接作为可信渲染数据。Wiki、Knowledge Unit、Memory、Mastery 和 Plan 仍属于需要用户批准的长期沉淀，不因工具调用或模型建议自动写入。

### 5.6 验收场景

```text
“查找最近的 LangGraph checkpoint 官方文档，比较两种恢复方式，给出引用；
只把我确认的官方页面保存到知识库。”
```

验收证据：搜索工具被按需提升、每条结论有网页 citation、恶意页面不能注入工具指令、未确认前 Knowledge revision 不变、确认后增量来源可追溯。

## 6. H2.6：Research Subagents

### 6.1 Profile，而不是任意 Agent

扩展现有 `Explore`，首版只新增服务端注册的静态 profile：

| Profile | 能力 | 写权限 | 产物 |
| --- | --- | --- | --- |
| `explore` | 本地/Knowledge 只读 | 无 | 代码或知识发现摘要 |
| `research` | Knowledge + Web Search/Fetch | 无 | citation-backed evidence bundle |
| `practice` | 受限 Practice Engine | 依现有审批 | 测试、练习或项目证据 |
| `synthesize` | 只读已收集 evidence refs | 无 | 冲突、结论和缺口摘要 |

模型可以请求 profile 和任务，但不能动态定义权限、Provider、系统提示或最大预算。所谓“动态 Subagent”只表示按任务创建实例，不表示模型创建新的安全主体。

### 6.2 运行边界

- 继承明确的 owner、workspace、surface、parent run 和 goal ref；
- 每个 child 拥有独立 checkpoint、token、tool call、wall-clock 和 artifact budget；
- 默认不允许嵌套；以后若开启，深度、并发和总量分别限制；
- 父取消传播给所有 child；child 终态幂等写入父 timeline；
- child transcript 不全量注入父上下文，只返回 result brief、evidence refs 和 result ref；
- 多个 Research child 的 citation 按 evidence hash 去重，由 Synthesize 处理冲突。

### 6.3 纵向切片

#### H2.6A：Research 单子代理

- 注册 `research` profile；
- 完成 Knowledge + Web 只读研究；
- 父 run 能流式看到启动、工具摘要、证据数和终态。

实现状态（2026-07-19）：本切片在现有 `task` 委派工具和唯一 Harness Runtime 上注册服务端静态 `research` profile。该 profile 只允许本地只读发现、Knowledge Search、Web Search，以及在服务端实际可用时的安全 Web Fetch；禁止 Shell、文件写入、Memory/Persistence、递归委派和模型自定义预算。每个 child 使用独立的 token、步骤和 wall-clock 上限，父 timeline 只接收阶段、状态、工具名、工具数、证据数和 opaque operation ref，不接收参数、网页正文、prompt 或 child transcript。证据引用仅从服务端证据工具结果提取，不能由本地文件内容伪造。

本切片不包含受限并行、Synthesize、Practice profile，也不宣称已实现 Node Research Task。Node Research Task/Research Branch 与 `parent_thread`、`primary_goal_id`、`run_id` 的绑定，提交时冻结的 graph/page/source revision receipt，以及实际 RAG chunk/trimming/token-budget receipt，仍是后续共享契约。

#### H2.6B：受限并行和综合

- 支持最多 N 个 research child 并行，默认 N=3；
- 共享父 run 总预算，不能通过拆 child 绕过限制；
- 增加重复查询、重复来源和无新证据 breaker；
- Synthesize 只读取 evidence refs，不重新访问网络。

实现进度（2026-07-19）：H2.6B1 已完成受限并行与共享预算底座。LangGraph 原生 ToolNode 可同时执行最多三个服务端注册 child；父子共用 token、model call 和 tool call 总预算，运行中按预留额度计费、终态按实耗结算，超时路径 fail-closed。delegation ledger 使用派生 child 身份保证 checkpoint 重放幂等，全局 evidence refs 使用确定性有界 reducer，完成顺序不影响去重结果。父 timeline 只投影父子合计预算和安全计数。

H2.6B 尚未整体关闭：重复查询/来源 breaker、Evidence Bundle 只读端口和 `synthesize` profile 留在 H2.6B2。没有 Evidence Bundle 读取契约前，不允许让 Synthesize 根据裸 citation id 或 child 文本伪造综合。

#### H2.6C：Practice Profile

- 将 Coding Practice、测验或小实验作为 child run；
- 现有 Tool Policy、Approval 和 Sandbox 原样生效；
- 产出结构化 Mastery Evidence 候选，不直接修改掌握度。

### 6.4 测试门禁

- 父子取消、超时、预算耗尽和进程恢复；
- 跨 workspace/owner、权限扩大和递归委派 fail-closed；
- 并行 completion 顺序不影响 citation 去重结果；
- child 完成后父模型必须能继续得到终答；
- child 失败不会产生伪造 evidence 或 silent success。

## 7. H2.7：Goal and Mastery

### 7.1 Thread Goal

新增显式命令或 API：

```text
/goal <completion condition>
/goal
/goal clear
```

Goal 默认关闭。普通快速对话不会先调用额外评估模型。只有用户显式设置 Thread Goal，或 Growth Surface 创建受控 Goal run 时，才在每轮结束后评估。

Goal evaluator 只读取可见对话、工具终态、citation、artifact/evidence refs 和任务账本，不读取私有推理。结果必须返回：

```text
status: satisfied | blocked | continue
blocker: missing_evidence | needs_user_input | run_failed |
         external_wait | goal_not_met_yet | no_progress
evidence_refs: [...]
next_action: bounded text
```

自动继续只在以下条件同时满足时发生：最新 assistant turn 已 durable checkpoint、用户未发送新消息、目标仍为同一 revision、blocker 为 `goal_not_met_yet`、有新的 next action、未触发上限。默认最多 4 次隐藏继续；连续 2 次无新证据立即停止。

评估输入同样按不可信数据处理，网页、MCP 或工具结果不能通过内容伪造 `satisfied`。续跑创建使用 session/run lease 和 compare-and-swap；刷新、两个 API worker 或恢复任务竞争时只能产生一个 active run。

### 7.2 Learning Goal 与 Mastery Evidence

`ThreadGoal` 可以引用 `LearningGoal(goal_id, goal_revision)`，但不能修改它。长期进度由 Mastery Ledger 投影：

```text
MasteryEvidence(
  evidence_id,
  learning_goal_id,
  capability_id,
  kind,              # quiz | explanation | code_test | artifact | project | citation
  result,            # pass | fail | partial | observed
  score,
  rubric_revision,
  source_ref,
  session_id,
  run_id,
  created_at,
)
```

掌握度是 rubric 对 evidence 的可重算投影，不是模型自报的百分比。LLM Judge 只能为开放题提供一个带 rubric revision 的信号；代码测试、确定性测验和产物验证优先。

### 7.3 纵向切片

#### H2.7A：Goal 状态与人工继续

- Thread Goal 的创建、查看、清除、revision 和 timeline 事件；
- typed blocker；
- 暂不自动继续，先由用户点击“继续目标”验证状态机。

#### H2.7B：安全自动继续

- durable post-turn evaluator；
- no-progress breaker、次数上限和用户消息抢占；
- 断线/重启不重复继续，不产生两个 active run。

#### H2.7C：Mastery Ledger

- 新增 `core/learning/` 中立领域，不把掌握度塞入 Memory fact；
- 接收 quiz、Practice child、代码测试、artifact 和 citation 证据；
- Knowledge Goal 页面读取聚合结果和原始 evidence refs；
- 用户可以纠正 rubric 或标记证据无效，投影可重算。

### 7.4 验收场景

```text
学习目标：掌握 LangGraph interrupt/resume。
任务：先检索我的知识库；缺失时查官方资料；向我提两个问题；
再让我完成一个最小练习。只有测验和练习证据都通过才算达到目标。
```

系统必须能说明还缺哪一类证据，不能仅因为回答看起来完整就把掌握度设为 100%。

## 8. H2.8：Retrieval and Memory

### 8.1 三类记忆边界

| 类型 | 内容 | 当前承载 | 下一步 |
| --- | --- | --- | --- |
| Semantic | 用户确认的事实、偏好、约束 | Memory facts | 相关性检索和冲突/时效处理 |
| Episodic | 会话、任务、失败、学习活动 | timeline / run / evidence | 生成受限 episode summary 和 evidence refs |
| Procedural | 如何完成重复工作 | Skills | 版本、激活和效果评测 |

Wiki/Knowledge 不等于 Memory。知识库保存可引用、可版本化的外部和项目知识；Memory 保存用户相关、跨任务有用且经过确认的事实与活动摘要。

### 8.2 Retrieval Gate

首版决策：

```text
skip | semantic_memory | episodic_memory | knowledge | web | mixed
```

Gate 先使用确定性信号：Surface Context、显式引用、Goal、时间词、用户人物/偏好指代、知识节点选择、是否要求最新信息。只有不确定时才调用低成本分类模型。Gate 不是安全边界，owner/workspace/visibility 过滤仍由各 Port 强制执行。

每种来源独立 token budget，Context Assembler 负责去重、时效、冲突标记和 authority 排序：

```text
frozen surface context
  > current user request
  > current tool observations
  > approved revision-bound knowledge
  > approved memory
  > remote web/MCP evidence
  > summaries
```

### 8.3 纵向切片

#### H2.8A：Gate + 可观测性

- 决策、原因码、耗时、候选来源和实际命中数进入 trace metric；
- UI 只显示“检索了哪些来源”，不显示模型私有推理；
- 普通算术/闲聊必须可以 skip，避免默认拖慢快速体验。

#### H2.8B：Semantic / Episodic Retrieval

- Memory Store 增加 bounded query seam；
- Episode 从 durable timeline 生成，原始 transcript 不直接复制成事实；
- 召回结果带 memory/episode ID、revision、时间和 provenance；
- 冲突事实同时返回并标记，不让最近一条静默覆盖历史。

#### H2.8C：Consolidation Proposal

- 按 episode 数量、显式用户请求或阶段结束触发 consolidation；
- 只生成 Memory/Skill/Wiki proposal，不能静默写入；
- 重复事实、过期偏好和来源失效在 apply 前重新校验。

## 9. H2.9：Knowledge Evolution

### 9.1 闭环原则

当前 `propose_evidence_learning()` 已能从原始 Knowledge citation 形成可验证 learning page。二次拓展不改写该存储，而是补齐入口和闭环：

1. Web Evidence 经用户批准转为 immutable Knowledge Source；
2. Research / Practice / Mastery 生成结构化 evidence refs；
3. Harness 根据 Goal gap 生成一个或一组 Wiki Proposal；
4. policy 校验 citation revision、非递归来源、secret、目标路径和 diff；
5. 批准或安全自动规则应用后，只重建受影响的 Wiki、索引和图节点；
6. 新 revision 反向更新 Goal gap 和后续 Retrieval。

### 9.2 允许自动化的边界

| 变化 | 默认行为 |
| --- | --- |
| 已批准页面的索引重建 | 自动 |
| 原始 citation 的 extractive learning | 依现有确定性 policy |
| 外部网页保存为来源 | 用户确认 |
| 多来源综合 Wiki | proposal + 用户审核 |
| 用户偏好/身份/长期目标 | Memory proposal + 用户审核 |
| 掌握度证据记录 | 自动记录，聚合规则可重算 |
| 掌握度达标 | rubric 评估，可被用户纠正 |
| 公开发布 | 独立审核，永不由 Harness 自动完成 |

### 9.3 纵向切片

#### H2.9A：回答保存为学习提案

- 从当前 run 的 stable citations、artifact 和 Mastery Evidence 生成提案；
- 用户可编辑标题、目标页面和摘要；
- 保存后引用仍能回到原 run 和 source revision。

#### H2.9B：Gap-driven Research

- Knowledge Graph goal alignment 输出 capability gaps；
- 用户选择 gap 后创建 Thread Goal；
- Harness 检索内部知识，必要时启动 Research child；
- 研究完成只生成 source/wiki proposal，不直接改变 goal。

#### H2.9C：增量索引与回归评测

- 应用 revision 后只更新受影响 chunk/page/edge；
- 维护固定问题集，比较更新前后 retrieval hit、citation freshness、answer groundedness；
- 如果知识更新使评测回退，标记 revision 风险而不是继续自动扩张。

## 10. 跨阶段事件和 API 原则

新增事件优先使用通用 envelope，payload 有版本号：

```text
capability_catalog_updated
capability_selected
web_evidence_created
web_source_proposed
subagent_started / subagent_terminal（沿用）
goal_updated
goal_evaluated
mastery_evidence_recorded
retrieval_gate_decided
knowledge_evolution_proposed
```

约束：

- `event_id` 可幂等重建；
- run 可见事件先持久化再广播；
- payload 只放 bounded summary 和 stable ref；
- 大文本、网页、child transcript 和评测明细放 artifact/store；
- 新客户端忽略未知事件，旧 session replay 不失败；
- 前端外包线只消费稳定 view model，不能依赖内部 Python 类名。

## 11. 前端并行开发边界

前端外包会话当前负责主对话和 Knowledge Graph 体验。Harness 二次拓展在以下文件范围内工作：

```text
packages/sage_harness/**
core/harness/**
core/learning/**（新增）
core/coding/tools/**（仅 adapter/registry）
api/harness*.py / api/coding.py（契约接入）
tests/core/harness/** / tests/evals/**
```

外包线独占：

```text
frontend/src/views/CodingView.vue
frontend/src/views/KnowledgeView.vue
frontend/src/components/harness/**
frontend/src/components/knowledge/**
frontend/src/harness/**
```

协作规则：

1. Harness 线新增事件/API 时先写 contract test 和中文 contract-gap/contract-change 说明；
2. 不在 Harness PR 顺带改布局和视觉；
3. 前端不为视觉效果制造假 stage、假 tool 或假 reasoning；
4. 两边稳定后用一个独立小 PR 接入新的 view model；
5. 共享文件 `frontend/src/types/api.ts`、`api/coding.py` 由集成者统一处理，避免双边同时修改。

## 12. 版本、提交与验证

### 12.1 内部里程碑与公开版本

仓库历史文档继续保留 V7.x 名称，但从本计划开始不再把它当作未来产品发布版本。内部用 Harness slice 标识开发边界；服务器首次完整发布统一标记为 `v0.1.0`，在发布门禁完成前不提前打 tag。

| 内部里程碑 | 交付 |
| --- | --- |
| H2.4A-C | Capability Registry |
| H2.5A | Search + Web citation |
| H2.5B-C | Fetch + Knowledge source proposal |
| H2.6A-C | Research Subagents |
| H2.7A-B | Thread Goal + safe continuation |
| H2.7C | Mastery Ledger |
| H2.8A-C | Retrieval Gate + Memory |
| H2.9A-C | Knowledge Evolution closure |

每个 slice 对应一个或少量职责清晰的 commit/PR，不要求一次晚上全部完成。`v0.1.0` 只在这些 slice 中被选入首发范围的部分全部通过服务器门禁后创建。

### 12.2 每个小版本门禁

```text
定向 contract / domain tests
  -> Harness + Coding + Knowledge 关联测试
  -> 后端全量 pytest
  -> ruff + mypy
  -> 前端 contract tests + full test/build（触及共享协议时）
  -> git diff --check
  -> logic-lens：权限、持久化、网络、并发和 API 审查
  -> live / replay / refresh / restart 浏览器或 API smoke
  -> 更新 Obsidian sage-learning
  -> 中文 PR 合入 dev/sage-v7
```

Web、MCP 和模型真实 smoke 使用测试账户/低权限 fixture。CI 不依赖真实网络或真实密钥。

### 12.3 核心指标

- 普通无需检索对话 P50 首 token 不因 Gate 明显回退；
- Tool/MCP/Web/Subagent 选择和执行成功率分别统计；
- Web 回答 citation coverage 和 stale citation rejection；
- Goal satisfied 的 evidence coverage、无进展停止率和重复续跑为零；
- Mastery 投影可重算、确定性 evidence 优先；
- Knowledge 增量更新后的 retrieval hit/freshness 不回退；
- 任何跨 owner/workspace、SSRF、secret、approval bypass 测试必须 100% 通过。

## 13. 实施批次记录

第一批次已交付 `H2.4A`：

1. 为当前 local tools、MCP、Skills 和 Subagent profiles 建立只读 CapabilityDescriptor；
2. 不改变工具执行、模型选择和前端布局；
3. 提供内部 registry query 与服务端只读 API；
4. 加入稳定 ID、revision、scope、secret/path sanitization 测试；
5. 用现有工具场景证明行为完全兼容；
6. 独立提交、PR、全量门禁和 sage-learning 收口后，再进入 H2.4B。

第一批次没有加入 Web Search、Memory 或自动 Goal 续跑，先建立了后续扩展共享的能力底座。随后 H2.4B-C 已完成统一发现、安全提升、健康度与审计；H2.5A 已交付 Search 与当前回答引用，H2.5B0-B2 已依次交付运行预算与 Shell 可靠性、安全 Fetch/Artifact、异步 PDF 解析；H2.5C 已交付用户确认的 Knowledge Source Proposal；H2.6A 已交付单个受限 Research Subagent；H2.6B1 已交付最多三个 child 的真实并行、父子共享预算、恢复幂等账本和 evidence refs 确定性去重。下一阶段 H2.6B2 只能在 Evidence Bundle 只读端口建立后开放重复证据 breaker 与 Synthesize。

## 14. 完成定义

二次拓展只有在以下闭环可重复演示时才算完成：

1. 用户选择一个长期学习目标；
2. Sage 判断内部知识不足，并明确说明需要外部证据；
3. Research Subagent 通过受控 Web Search 获取带稳定引用的资料；
4. Sage 用资料提问或安排 Practice，产生 Mastery Evidence；
5. 未达标时给出明确 gap 并继续，而不是无限循环；
6. 达标后生成 Wiki/Memory proposal，未经许可不写入；
7. 批准后 Knowledge revision、索引和图谱增量更新；
8. 刷新、断线和进程重启后，Goal、引用、子运行、掌握度和提案均可恢复；
9. 用户能够从任何进度结论下钻到原始 evidence、run、citation 和 revision；
10. 全流程不暴露密钥、不保存私有推理、不跨 workspace、不绕过审批。

在此之前，所有阶段都应描述为“已交付某个可验证切片”，不能宣称 Sage 已具备完整自主学习能力。
