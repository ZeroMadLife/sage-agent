# Sage 桌面端长期学习产品实施计划

> 状态：已获 CTO 全权实施授权，按阶段本地开发与审查；公开 push、PR 合并、签名凭据和发布仍按外部变更门禁单独执行。
>
> 设计来源：`docs/superpowers/specs/2026-08-24-sage-desktop-learning-product-design.md`
>
> 设计提交：`b2891b1127e5b7b3038c0758eb611faf4e1bec17`

## 1. 交付策略

按纵向能力切片，不按“先后端、再前端、最后测试”横向推进。第一阶段先证明两个最高风险路径：

1. Python/FastAPI/Harness 能否作为 macOS arm64 sidecar 在无 Python/Node 的机器上启动；
2. 已有可恢复学习任务 A1/A2 能否安全接回当前 `dev/sage-v7`，并与新设计的三种 plan identity 兼容。

所有功能在独立 worktree 和职责分支完成，先定向验证、再代码审查、再进入集成分支。根 worktree 只做同步和集成验证。

## 2. 当前可复用资产

| 资产 | 当前状态 | 处理方式 |
| --- | --- | --- |
| 产品设计 | `docs/desktop-learning-product-design@b2891b1` | 作为所有切片的事实来源 |
| 认证产品化 | `feat/productization-foundation`，23 个文件未提交 | 先独立审查与 checkpoint，不重写 |
| 可恢复学习 A1/A2 | `feat/recoverable-learning-bootstrap-v1@20182e4` | 已提交候选；审查后迁移到新基线 |
| 旧学习计划 | `docs/recoverable-learning-task-v1@5054381` | 复用已验证合同，按新 plan identity 修订 |
| Harness/RAG/Memory/Mastery | 当前 `dev/sage-v7@87478e8` | 作为底层原语，不宣称已完成产品接线 |

已复验：认证候选 `49` 个后端/脚本测试、`7` 个前端认证测试和生产构建通过；学习 A1/A2 候选 `27` 个测试及 Ruff 通过。

## 3. 依赖图

```text
P0 认证候选审查与 checkpoint ───────────────┐
                                              ├─ D2 首次启动/Keychain/Provider
D0 macOS sidecar packaging spike ── D1 安全桌面宿主 ┤
                                              └─ D3 Cloud OAuth/会话

L0 学习 A1/A2 候选审查与基线迁移
  └─ L1 只读 Learning Scope
      └─ L2 Assistant 任务确认
          └─ L3 Research/Synthesize/Learning Artifact
              └─ L4 Practice/Mastery/Resume
                  ├─ M0 Memory 控制面
                  └─ E0 学习与记忆 Eval

D1 + D2 + L2/L3/L4 ── I0 桌面端首次学习 E2E ── R0 发布候选收口
```

P0、D0、L0 可并行。M0/E0 后置，不阻塞用户先使用桌面学习闭环。

## 4. 切片 P0：认证产品化候选收口

**交付行为**

- 保留 Web HttpOnly Cookie；为桌面/TUI 提供短期 access token、opaque refresh rotation、设备撤销和统一 owner 校验；
- 保留 `quickstart.sh` 作为开发/源码启动入口，不把它包装成桌面安装器；
- 将当前未提交成果整理为职责清晰的本地 checkpoint commit。

**公共 seam**

- `/api/v1/cloud/auth/device/login`；
- `/api/v1/cloud/auth/token/refresh`；
- `/api/v1/cloud/auth/token/revoke`；
- HTTP/WS Cookie + Bearer 统一依赖；
- `scripts/quickstart.sh --check/--start`。

**验收证据**

- access token 过期、session 撤销、refresh replay 和设备 family 撤销均 fail closed；
- JWT 不授予 workspace 权限，跨 owner 请求仍由资源查询拒绝；
- refresh token 不进入 URL、日志或 localStorage；
- 定向测试、Ruff、Mypy、前端认证测试与生产构建通过。

**非目标**

- 不在 P0 实现 Keychain、桌面 OAuth、公开注册或计费。

## 5. 切片 D0：macOS sidecar packaging spike

**交付行为**

- 选择 PyInstaller one-dir，打包一个与 Sage API 同 Python 3.12 运行时的 sidecar；
- 在临时数据目录启动最小 FastAPI app，验证 liveness/readiness、SQLite checkpoint、TLS Provider client 和优雅退出；
- 生成版本、build SHA、依赖清单和可重复构建 receipt。

**公共 seam**

- `desktop/sidecar/` 构建入口；
- `sage-api-aarch64-apple-darwin` external binary；
- `/health/live`、`/health/ready` 的最小 schema；
- sidecar `--bind 127.0.0.1 --port 0 --data-dir ...` 启动合同。

**验收证据**

- 无仓库 `.venv`、无系统 Python/Node 的隔离环境能够启动产物；
- 核心 import、SQLite 读写、checkpoint 重开、HTTPS 请求初始化通过；
- 端口为 OS 分配，进程退出后无遗留 child；
- 产物不包含 `.env`、Provider key、用户数据或绝对开发路径。

**依赖与非目标**

- 无功能依赖，可与 P0/L0 并行；
- spike 不承诺 DMG、Keychain、OAuth、updater 或完整前端。

## 6. 切片 D1：安全桌面宿主

**交付行为**

- Tauri 2 启动 Vue build 和 D0 sidecar；
- sidecar 通过匿名 pipe 上报身份，Rust 校验后向 Vue 提供 endpoint；
- HTTP/SSE/WS 使用启动期 bearer、Host/Origin 校验；
- 展示 ready/degraded/blocked 能力页，支持退出、崩溃退避和熔断。

**公共 seam**

- Desktop Host Adapter；
- `{pid, port, instance_id, api_version, build_sha, nonce}` handshake；
- `/health/live`、`/health/ready`、`/capabilities`；
- single-instance lock、orphan cleanup、crash budget。

**验收证据**

- 端口抢占、本机伪造 handshake、错误 nonce、旧 API version 和跨 Origin 请求被拒绝；
- 窗口隐藏、连接断开、sidecar 崩溃、显式退出和机器重启符合恢复矩阵；
- Tauri 不开放通用 shell、任意远程导航、devtools 或跨 workspace 文件访问；
- macOS arm64 本地开发包 smoke 通过。

**依赖与非目标**

- 依赖 D0；
- Developer ID/notarization 作为 D1 发行子门禁，缺少签名凭据时不阻塞开发包验证，但不能宣称可公开安装。

## 7. 切片 D2：首次启动、Keychain 与 Local Provider

进入 D2 前先关闭 D1 技术债：在不改变既有 handshake、PID identity、crash budget 和真机 smoke 合同的前提下，从 `supervisor.rs` 提取 diagnostics 与 state repository；该拆分不得与 Keychain/Provider 功能混在同一职责提交中。

**交付行为**

- 首次启动选择 Local/Cloud，完成数据目录、迁移、workspace 和 capability 检查；
- Rust 持有 macOS Keychain 所有权，SQLite 只存 `key_ref/key_hint`；
- Local Provider 支持新增、探测、选择默认模型、轮换和删除；
- 无 Docker 时仍可对话/RAG，副作用工具明确 blocked。

**公共 seam**

- Desktop Secret Broker；
- Provider metadata/key-ref DTO；
- `ready/degraded/blocked + reason_code + action`；
- onboarding state machine。

**验收证据**

- Keychain 锁定、拒绝访问、轮换、注销和删除都有可恢复错误；
- secret 不进入环境变量、SQLite、Timeline、日志或诊断包；
- 无 Docker/PostgreSQL/Web Search 的降级矩阵与设计一致；
- 首次启动、刷新和重启后 onboarding 状态可重建。

**依赖与非目标**

- 依赖 P0、D1；
- 不实现 Cloud OAuth 和 updater。

## 8. 切片 D3：Cloud OAuth 与桌面会话

**交付行为**

- 系统浏览器 + PKCE + 一次性 state + Rust 临时 loopback callback；
- Rust 交换并保存桌面 refresh/session credential，Vue 不接触 token；
- sidecar 作为受限 broker 调用 Cloud API，支持撤销和重新登录。

**验收证据**

- state 过期、callback replay、错误 redirect、授权码重放和已撤销设备全部拒绝；
- Cloud Provider key 只留服务器；
- WebView 不依赖系统浏览器 Cookie 共享；
- 登录、刷新、退出、重启恢复和跨 owner 拒绝 E2E 通过。

**依赖与非目标**

- 依赖 P0、D1；可以与 D2 后半段并行；
- 首版不同时实现 deep-link OAuth。

## 9. 切片 L0：可恢复学习 A1/A2 迁移

**交付行为**

- 审查并迁移现有 draft、CAS 修改、activation intent、幂等 Session/Goal/Plan bootstrap 和 reconciliation；
- 使用 expand-migrate-contract：新增 `learning_plan_hash`，暂时兼容旧 `plan_hash`，后续调用者迁移完成再收缩；
- 保留 activation receipt 的 owner、task revision、capability revision 和 source policy。

**公共 seam**

- `POST/GET/PATCH /api/v1/learning/tasks`；
- `POST /api/v1/learning/tasks/{task_id}/activate`；
- activation receipt/reconciliation；
- Learning Task repository。

**验收证据**

- 并发激活只有一个 winner；四个故障注入点重启后完成或补偿；
- 孤立 Session 被归档；
- `learning_plan_hash`、`turn_context_plan_hash`、`dag_hash` 不再混用；
- 原 27 个定向测试、集成回归、Ruff、Mypy 通过。

**依赖与非目标**

- 基于现有 `feat/recoverable-learning-bootstrap-v1@20182e4`，不重新实现；
- 本片不执行检索、生成学习 Artifact 或写 Mastery。

## 10. 切片 L1：首轮只读 Learning Scope

**交付行为**

- active task 冻结 `AllowedCapabilitySet`，模型可见工具和执行入口双重过滤；
- 默认允许 Knowledge/Evidence/Memory read，按来源策略允许只读 Web/Research；
- Shell、Patch、Git write、删除、write-MCP、Practice 和自动长期写入默认不可见且不可调用。

**验收证据**

- `不要联网`、旧 capability revision、伪造 tool call 和 Skill 未激活均 fail closed；
- Context/Capability 漂移返回明确 409，不触发真实工具；
- Timeline 不公开 query、source path、Skill prompt 或网页正文。

**依赖与非目标**

- 依赖 L0；复用现有 Permission/Policy/Approval/Sandbox，不新建权限系统。

## 11. 切片 L2：Assistant 任务确认与进入会话

**交付行为**

- 在 Assistant 展示 draft、澄清项、来源策略和风险边界；
- 用户确认后只调用一次 activate，成功后进入共享会话；
- 失败保留 draft 和重试入口，刷新后恢复真实状态。

**验收证据**

- 前端覆盖 `draft/needs_confirmation/activating/active/activation_failed`；
- 首轮消息不早于 activation receipt；
- 旧 Assistant/Coding 入口保持兼容；
- ego-lite/Playwright 覆盖创建、确认、失败重试和刷新恢复。

**依赖与非目标**

- 依赖 L0/L1；不重写 CodingView。

## 12. 切片 L3：Research、Synthesize 与 Learning Artifact

**交付行为**

- LearningPlan/KnowledgeUnit 使用稳定 identity 和 source policy；
- Research 只收集 revision-bound hits/citations；Harness 统一组装 EvidenceBundle；
- Synthesize 生成带引用 Markdown/受控卡片 Learning Artifact；
- Knowledge 不足时返回 source gap，按策略有界触发 Web Research。

**公共 seam**

- LearningPlan/KnowledgeUnit schema；
- Learning Artifact Store：id/kind、Goal/Plan/Unit、content hash、media type、evidence refs、source revisions、status、idempotency key、retention；
- Resume Summary 只保存不透明 Artifact ref。

**验收证据**

- 无证据/过期 revision citation 为零；
- 重试和 Resume 不重复 Artifact；
- Web 禁用、Provider 失败、证据冲突和预算耗尽有确定性状态；
- 首个 Sage Task DAG/Checkpoint/Resume 学习材料可在 UI 查看来源。

**依赖与非目标**

- 依赖 L1/L2；不执行任意 HTML/JS，不自动写 Knowledge/Memory。

## 13. 切片 L4：Practice、Mastery 与 Resume

**交付行为**

- 用户完成 code_test 后形成 PracticeReceipt；服务端验证、criterion 绑定、durable outbox 后进入 Mastery Ledger；
- 闭卷解释先形成带 model/rubric revision 的 Judge Artifact，不单独判定 mastered；
- 中断后分别校验三种 plan hash、owner、scope、Skill/MCP revision，再继续未完成单元。

**验收证据**

- 跳过 Practice 不写 Mastery；重复 receipt 幂等；失效 evidence 触发重算；
- 成功 child、Artifact 和 Evidence 在 Resume 后不重复；
- sidecar kill、刷新、进程重启、Approval pending 和 source revision 漂移均有覆盖；
- Timeline/Checkpoint 不保存大正文或 Judge 私密内容。

**依赖与非目标**

- 依赖 L3；开放题自动晋级 Mastery 明确后置。

## 14. 切片 M0：Memory 控制面

**交付行为**

- 在 Settings 展开现有 pending proposal 审批为 proposal/fact 搜索、详情、revision/event chain、更正 proposal、撤回和 recall receipt；
- 明确“撤回后 active recall 不可见”与“物理隐私删除”是两套合同；
- EpisodeReference 与获批 DurableEpisodeSummary 分开展示。

**验收证据**

- owner/workspace 隔离、CAS 冲突、冲突组原子返回、撤回后不再召回；
- recall receipt 展示 reason/provenance/token/latency，不暴露正文或 secret；
- 物理删除在定义审计保留与索引级联协议前不伪装成交付。

**依赖与非目标**

- 依赖 L4 的真实学习 episode；
- 导出、批量删除和 Procedural Skill 控制面可拆独立后续切片。

## 15. 切片 E0：学习与记忆 Eval

**交付行为**

- 先建立本地 Sage 长期学习消融：无记忆、现有 lexical、混合召回、分层加载；
- 再接 LoCoMo/LongMemEval 等公开 benchmark，固定模型、embedding、top-k、judge、数据版本和 token 成本；
- 30-50 条独立 review Gold 与产品功能分开交付。

**验收证据**

- write precision、false memory、Recall@K/MRR、Answer Correctness、temporal update、contradiction、abstention、Goal continuity；
- input/memory token、retrieval p50/p95、owner 越权率、deletion leakage 和 provenance coverage；
- 报告区分公开 benchmark、本地消融、确定性合同测试和真实 Provider 结果。

**依赖与非目标**

- 可在 L3 后开始 corpus/runner，最终结果依赖 L4/M0；
- 不把单一数字写成生产 SLA 或简历唯一论据。

## 16. 集成与发布收口

### I0：桌面首次学习 E2E

- D1/D2 与 L2-L4 合入同一候选 SHA；
- macOS 桌面端完成创建目标、确认计划、读取引用、code_test、关闭/崩溃、恢复、查看 Mastery 和 Memory Proposal；
- 使用 ego-lite/Playwright 验证 UI，使用真实 sidecar 进程和临时用户目录验证生命周期。

### R0：发布候选

- 后端门禁、Ruff、Mypy、前端测试/build、桌面 Rust tests、packaging smoke、`git diff --check`；
- Developer ID 签名、notarization、stapled DMG、Gatekeeper/quarantine 首启；
- 结构化诊断包脱敏审计；
- 更新 README、版本 Release、Obsidian `sage-learning` 的 source commit、证据、风险和下一阶段边界；
- 发布到 `main` 只使用已经在 staging 验证的同一不可变 SHA。

## 17. 升级给 CTO 的事项

只在以下情况暂停并请求决策：

- 需要 Apple Developer ID、notarization 或付费签名资源；
- Cloud OAuth provider 配置、域名或生产凭据缺失；
- 需要不可逆数据库迁移、用户数据物理删除策略或公开发布；
- sidecar 打包证明当前 Python 原生依赖无法可靠分发，需要切换 Electron/服务端路线；
- 产品方向、收费、隐私承诺或公开 SLA 发生变化。
