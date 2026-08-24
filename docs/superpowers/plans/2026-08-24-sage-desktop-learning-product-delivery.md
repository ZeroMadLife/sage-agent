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

### D2 实施收口（2026-08-25）

> 首轮候选记录保留用于追溯，但其正式 receipt 已被 D2.1 三镜头复审判定为假绿，
> 不再作为 D2 最终验收证据；当前有效候选与门禁见下方 D2.1 收口。

- **候选代码 SHA**：`b55f0f25e6f435a87389c56407afabb11a2a2d20`，分支
  `feat/desktop-provider-onboarding-v1`，仅本地 commit，未 push、未建 PR。
- **基线职责**：先以 `7b1e655`、`71d340c`、`2a423de` 将 P0 三段认证提交整合到
  D1 `e6c26af`；再以 `d31a5c7` 独立提取 diagnostics 与 state repository。上述提交不含
  D2 Keychain/Provider 功能，既有 handshake、PID identity、crash budget 与正式 bundle
  smoke 合同保持不变。
- **D2 职责**：`bc58d5b` 建立 Rust Keychain/Provider 合同，`747b892` 完成首次启动与
  Local Provider 产品闭环；`c05eb09`、`453561e` 分别补齐冻结 sidecar 的版本化模型清单
  与动态工具模块，`b55f0f2` 保证首次启动 onboarding blocked 时仍持续轮询宿主并在
  sidecar crash/restart 后取得轮换会话。
- **已交付行为**：Local/Cloud 首次选择、数据目录/迁移/workspace/capability 检查；Rust
  独占 macOS Keychain，SQLite 只保存 `key_ref/key_hint`；Local Provider 新增、探测、默认
  模型、轮换、注销与删除；状态统一为 `ready/degraded/blocked + reason_code + action`，刷新
  与重启可重建。无 Docker 时 Assistant、Coding session 与 SQLite RAG 仍可用，write、patch、
  shell 副作用工具不进入能力目录；桌面端 Cloud OAuth 路由保持 404。
- **secret 边界**：Provider secret 只经 Rust 到 sidecar stdin bootstrap 的继承 pipe 传递，
  不进入环境变量、SQLite、Timeline、日志、诊断包或最终 `.app`。Keychain 测试使用唯一临时
  service/account 并清理，覆盖锁定、拒绝、轮换、注销、删除与恢复。
- **门禁证据**：Rust 35 tests、fmt、clippy 通过；D2 Python targeted 66 passed，完整后端
  `2047 passed, 12 skipped`，最终 desktop suite `57 passed`，Ruff 与受影响 12 个源文件
  mypy 通过；Vue `72 files / 546 tests` 与 production build 通过；`git diff --check` 通过。
- **正式 bundle**：使用 Python `3.12.13` 从 clean HEAD 运行唯一入口，receipt 位于
  `/private/tmp/sage-desktop-b55f0f2/desktop-bundle-receipt.json`。`source_dirty=false`，target
  为 `aarch64-apple-darwin`，sidecar 12 项 smoke 与 `.app` 的 `app_launch/crash_restart/`
  `handshake_health/explicit_exit/process_cleanup/webview_reconnect` 全部 `passed`；host 与
  sidecar 均为 arm64 Mach-O，ad-hoc 签名经 `codesign --deep --strict` 验证，退出后无残留进程。
- **遗留与下一步**：当前只可称 macOS arm64 本地开发 `.app`；大 chunk warning 仍在，且
  Developer ID、notarization、staple、DMG、updater、Cloud OAuth、Intel macOS、Windows 与
  Linux 均未交付。D2 等待中枢三镜头复审，不先行进入 D3 或发布。

### D2.1 三镜头复审修复 mini-spec（2026-08-25）

首轮 receipt 只证明产物可启动，不能证明本地模型回合、SQLite RAG 或副作用工具 fail-closed；
因此撤销其 D2 最终验收效力。本增量不扩大到 Cloud OAuth、updater、Developer ID、公证或 DMG。

**状态与进程不变量**

- sidecar spawn 前的 onboarding、配置与 Keychain `locked/access_denied/missing` 是可恢复配置阻塞，
  不属于进程 crash，不记录 crash budget；宿主直接发布同一 `reason_code/action`，即使 sidecar
  尚未 ready，Vue 也必须读取 onboarding 并展示解锁、授权或重新录入入口。
- 配置变更只在旧 sidecar 已验证退出、PID ownership/orphan 已持久化收敛后启动新 generation。
  TERM、KILL、身份复核或持久化任一步失败均保持 `blocked`，保留旧 PID/orphan，禁止覆盖所有权
  或产生第二实例；后续显式重试在收敛后才恢复。
- `/capabilities` 是 sidecar 就绪后的运行能力权威，顶层状态由关键能力与可选能力派生：关键
  `blocked` 为 `blocked`，否则任一可选 `degraded/blocked` 为 `degraded`，全部 ready 才为
  `ready`。sidecar 未就绪时宿主/onboarding 只描述启动前修复状态，HostGate 不交叉掩盖冲突。

**Provider 一致性不变量**

- SQLite 持久化 `active_provider_id`；第一个 connected Provider 可自动成为 active，新增或探测
  其他 Provider 不切换。`set_active_provider` 是固定 action union；修改非 active Provider 不重启
  runtime。断连或删除 active 后清空 active，runtime fail closed，必须由用户显式重选。
- Keychain 与 SQLite 的 add/rotate/disconnect/delete 使用 durable staged operation。每次操作先在
  SQLite 记录 operation id、kind、phase、provider/key refs 与脱敏 error，再执行 Keychain 腿和
  metadata 腿；BEGIN、commit、补偿任一失败都保留可诊断、可重试状态。启动时 reconciliation
  幂等完成或回滚未决操作，不静默丢弃补偿错误，也不把 secret 写入 SQLite、环境、日志、
  Timeline 或诊断包。

**连接与 artifact 不变量**

- Desktop WebSocket 使用 connection epoch 取消迟到的 host-status/socket；`close()` 后不得安装
  新 socket。重试耗尽时清除对应 session、发布 degraded，并向上层派发一次 terminal `close`。
  只有连接稳定跨过最短稳定窗口后才重置预算，open/immediate-close 不能无限续命。
- 正式 receipt 的 `local_conversation`、`local_sqlite_rag`、`side_effect_tools_blocked` 仅在冻结
  sidecar 隔离运行中完成真实断言后写入：本地无敏感 stub Provider 至少完成一次模型 turn；
  通过公开产品合同完成 SQLite RAG ingest/search；真实请求 `/capabilities` 与
  `/api/v1/harness/capabilities`；catalog 不含副作用工具且直接执行 fail closed。不得用常量、
  源码扫描或空 session 代替 artifact 行为，不放宽生产 SSRF 与 secret 边界。

**Red/Green 与验收矩阵**

- 每项行为先执行对应 Rust/Vitest/Pytest Red 并记录失败断言，再按上述不变量 Green；重点覆盖
  locked/denied/missing 不耗预算、TERM/KILL/persist 失败与恢复、跨资源 storage/compensation
  失败和重启 reconciliation、add-two/select/model/rotate/delete-or-disconnect-active/restart、
  capability 完整矩阵、WebSocket exhaustion/close-during-await/open-close churn。
- 最终从 clean code HEAD 运行 Rust full/fmt/clippy、Python desktop/auth/full、Vue focused/full/build、
  唯一临时 Keychain service/account round-trip 与清理、arm64 full product bundle、strict codesign、
  secret 扫描、host/launcher/sidecar 零残留和 `git diff --check`。receipt 文件/symlink/目录计数均
  取新候选实跑结果，不再把包含 symlink 的 entries 总数表述为“普通文件”。

### D2.1 实施收口（2026-08-25）

- **代码候选**：`59e96c547c2a16ca29e63d471d9b0502dee36995`。配置/Keychain pre-spawn
  阻塞不再消费 crash budget；HostGate 在 sidecar 未 ready 时仍读取 onboarding 修复动作。
- **进程所有权**：配置重启只有在旧 sidecar 已确认退出且 orphan ownership 持久化收敛后才启动
  新 generation；TERM/KILL/identity/persist 任一失败均保持 blocked，不覆盖旧 PID。
- **Provider 一致性**：schema v2 持久化 `active_provider_id` 与 `provider_operations` journal；
  add/rotate/disconnect/delete 支持重启 reconciliation，未决 operation 存在时后续 Provider 变更
  fail closed，避免交错操作留下孤儿 secret。active Provider 删除或断连后必须显式重选。
- **连接与能力**：Desktop WebSocket 使用 epoch、稳定窗口和有界重试；耗尽后只派发一次 terminal
  close 并清 session。`/capabilities` 按关键/可选子能力派生顶层状态，HostGate 以 sidecar 状态为
  ready 后的唯一运行能力权威。
- **artifact 合同**：冻结 sidecar 使用隔离 loopback stub Provider 完成真实模型 turn、SQLite RAG
  ingest/search、两个 capability endpoint 查询、catalog 排除与直接副作用工具 fail-closed；receipt
  字段只由已执行断言生成。
- **Red 证据**：各修复先由 Rust/Vitest/Pytest 合同命中旧行为；logic-lens 复审又复现了“待删除
  journal 后继续 rotate 会成功写入新 secret”的交错漏洞，新增测试在旧实现上因 `unwrap_err()`
  收到 `Ok(LocalProviderView)` 失败，Green 后由 journal gate 阻断并在重启后收敛。
- **源码门禁**：Rust `44 passed`、fmt/clippy 通过，真实临时 Keychain round-trip 已清理；Python
  desktop/auth 均 `61 passed`，full `2054 passed, 12 skipped`，Ruff 与受影响 Python mypy 通过；
  Vue focused `39 passed`、full `72 files / 549 tests`、production build 通过；source product smoke 与
  `git diff --check` 通过。
- **正式 artifact**：从 clean docs HEAD `8479a2cf77e30b3f2f7c9d43d4076e828261df61`
  运行唯一入口，receipt 为
  `/private/tmp/sage-desktop-8479a2cf77e30b3f2f7c9d43d4076e828261df61/desktop-bundle-receipt.json`，
  SHA-256 `466b274664a985d01714980cd35d0a3cef5946fddc412693233b490b2e325074`；
  `source_dirty=false`，target 为 `aarch64-apple-darwin`，sidecar 12 项 smoke 与 `.app` 6 项
  lifecycle smoke 全部 `passed`。其中模型 turn、SQLite RAG 与副作用工具阻断均来自冻结产物真实请求。
- **计数与安全**：sidecar receipt SHA-256 为
  `e512859637eeb3a3c684cc7a0e732266d5d4844aec54f01e605b7e940ed235f4`；artifact
  manifest 的 269 entries 经逐项分类为 247 个 regular files、22 个 symlinks、0 missing，写入
  `build-receipt.json` 后物理目录为 248 个 regular files、116 个 directories、22 个 symlinks。
  最终 `.app` 为 274 个 regular files、121 个 directories、0 symlink。host、launcher、sidecar
  均为 arm64 Mach-O，app 与三个嵌套可执行文件通过 `codesign --verify --deep --strict`/strict 验证，
  签名为 ad-hoc；secret sentinel 在 `.app` 与 receipt 中均零命中，退出后精确进程名复查为零残留。
- **发布边界**：不沿用首轮 `b55f0f2` receipt。当前仍只是 macOS arm64 ad-hoc/local dev 候选，
  等待第二轮中枢三镜头复审；Cloud OAuth、updater、Developer ID、公证、stapled DMG 均未交付。

### D2.2 Runtime/Standards 复审修复 mini-spec（2026-08-25）

本增量只修 supervisor 与 desktop adapter 的并发、所有权和关闭语义；D2.1 已放行的 durable
journal、active Provider、capability 派生和 artifact 行为合同保持不变。

**Supervisor generation 与 ownership 不变量**

- backoff 或可恢复配置 timer 醒来后，任何 failure accounting 必须在同一状态临界区先验证
  `launch_generation`。`desktop_launch_superseded` 是取消结果：不清 PID/child/orphan，不记录 crash
  budget，不发布旧状态，也不为旧 generation 安排下一次 retry。
- 配置重启同一时刻只有一个 stop owner；第二个快速 action 合并到进行中的 restart，不再捕获或终止
  同一 orphan。stop completion 只有在 generation 与 orphan identity 均匹配时才能提交；迟到 completion
  是 no-op，不得重写 orphan、PID 或新 snapshot。
- `terminate_verified` 只有 `TerminationOutcome::Stopped` 才证明旧进程已停止；`IdentityChanged`、
  `KillSent` 和 I/O failure 都保持 blocked。只有停止结论明确且 ownership 清除已持久化成功，才能启动
  新 generation；启动 orphan cleanup 与配置 restart 使用同一放行语义。

**Provider runtime invalidation 不变量**

- Provider operation outcome 在成功和可恢复失败上都携带 `runtime_invalidated`。active Provider 的
  rotate/disconnect/delete 一旦 metadata 已 fail closed 或 journal 进入未决阶段，即使 Keychain cleanup
  失败也立即停止旧 sidecar；未决 journal 未 reconciliation 前，runtime 配置读取继续 fail closed，
  不得用旧 secret 重启。

**Desktop WebSocket 不变量**

- 恢复期间若 session revision 已变化且当前 session 为 null，adapter 不把 null 当作永久结果；它以
  当前 revision 继续重新读取 host status。`ready(old) -> starting/degraded(null) -> ready(new)` 必须安装
  new session socket，epoch 只负责丢弃迟到结果，不消耗无意义的 terminal budget。
- 用户 `close()`、CodingStream session 切换和组件卸载是 clean close，不发布全局 `degraded`；只有异常
  transport close、retry exhaustion 或宿主真实断线影响 HostGate。retry exhaustion 仍只派发一次 terminal
  close 并清除对应 session。

**Red/Green 与收口**

- 逐项先添加 deterministic Rust/Vitest Red，再做最小 Green；focused 覆盖旧 timer 晚于新 ready、双
  configuration action、乱序 completion、两入口 `IdentityChanged`、active cleanup failure、null-session
  host recovery、session switch/unmount 与 exhaustion。
- 最终运行 Rust full/fmt/clippy、Vue focused/full/build、Python desktop/auth/full、临时 Keychain round-trip
  cleanup、source product smoke、secret scan、精确进程零残留和 `git diff --check`。代码与文档分别使用
  中文职责 commit；因 Rust/TS 产品源码改变，必须从新的 clean docs HEAD 重做正式 arm64 bundle，并
  固定 code/docs/receipt SHA、strict codesign、manifest 与 `.app` 分类计数后等待第三轮三镜头复审。

### D2.2 实施收口（2026-08-25）

- **代码候选**：`71e3467610b12552672b3c0df5d4f48a12e43632`。launch failure accounting
  在同一状态临界区先核对 generation；`desktop_launch_superseded` 与任何旧 generation failure 都作为
  取消，不改变新 PID/child/orphan、snapshot 或 crash budget，也不继续旧 timer retry。
- **配置重启**：快速重复 action 合并到一个 configuration stop owner，不再次捕获同一 orphan；stop
  completion 以 generation + orphan identity CAS 提交，迟到成功/失败均为 no-op。只有明确
  `TerminationOutcome::Stopped` 且 orphan 清除持久化成功才放行；`IdentityChanged`、`KillSent`、I/O
  与持久化失败保持 blocked。启动 orphan cleanup 使用同一严格 outcome 语义。
- **Provider runtime invalidation**：active rotate/disconnect/delete 的 operation outcome 在成功和 Err
  上都携带 `runtime_invalidated`；metadata fail closed 或 pending journal 后的 Keychain cleanup failure
  仍触发 supervisor stop。pending journal 使 `runtime_configuration()` 返回
  `provider_reconciliation_required`，在 sidecar spawn 前禁止旧 secret runtime 重启。
- **连接关闭语义**：session revision 改变且当前 session 为 null 时，WebSocket 按当前 revision 继续
  有界读取 host status，已覆盖 `ready(old) -> starting(null) -> ready(new)` 并安装 new socket。用户
  `close()`、CodingStream session switch 与 stop/unmount 不发布全局 degraded；异常 close 与 retry
  exhaustion 仍发布 degraded、清 session 并只派发一次 terminal close。
- **Red 证据**：旧 supervisor 分别把新 PID 清为 null、接受 `IdentityChanged`、让迟到 completion
  覆盖新状态；旧 adapter 在 transient null 后只调用两次 host status，主动 cleanup 发布两次
  degraded；旧 Provider API 无法返回 runtime invalidation。上述断言均先失败，再以最小实现 Green。
- **源码门禁**：Rust full `56 passed`（含唯一临时 Keychain service/account round-trip 与清理）、fmt、
  clippy `-D warnings` 通过；Provider focused `14 passed`；Vue focused `46 passed`、full
  `72 files / 552 tests`、production build 通过；Python desktop/auth 各 `61 passed`，完整门禁为 Ruff
  lint、466 files format、Mypy 231 source files 与 `2054 passed, 12 skipped`；source product smoke、
  changed-diff secret scan、精确进程检查和 `git diff --check` 通过。secret scan 仅命中三个明确的
  `test-secret-*` 测试夹具，不含真实凭据。
- **正式 artifact**：不沿用 `8479a2c` receipt；从 clean docs HEAD
  `6fe4bf17ddf17cf11c4adf3532cdb868a521f4d2` 运行唯一入口，receipt 位于
  `/private/tmp/sage-desktop-6fe4bf17ddf17cf11c4adf3532cdb868a521f4d2/desktop-bundle-receipt.json`，
  SHA-256 `54f081c1289691b1b8a2b1bd2622452bb9821c5ecfda471deaadf9be459b95b1`；
  `source_dirty=false`、Python `3.12.13`、target `aarch64-apple-darwin`。sidecar 12 项真实 smoke
  与 `.app` 6 项 lifecycle smoke 全部 `passed`，包含模型 turn、SQLite RAG、capability 与副作用工具
  fail-closed 的冻结产物断言。
- **计数与签名**：sidecar receipt SHA-256 为
  `cd5e55e1a1c2ce59e9bbbd140077a7e3ac3ea4077802bc2a01b3d1ce9e89a6f0`；269 manifest entries
  实测为 247 个 regular files、22 个 symlinks、0 missing，写入 receipt 后物理 sidecar 为 248 个
  regular files、116 个 directories、22 个 symlinks；最终 `.app` 为 274 个 regular files、121 个
  directories、0 symlink。host、launcher、sidecar 均为 arm64 Mach-O；app deep strict 与三个嵌套
  executable strict codesign 均通过，签名为 ad-hoc。
- **最终安全复核**：packaged product secret、测试 sentinel、private-key marker 与 `.env` 零命中；唯一
  `.pem` 是 certifi 公共 CA trust bundle，内容不含 private key。`sage-desktop`、`sage-api`、
  `sage-api-aarch64-apple-darwin` 经 `pgrep -x` 复查均无残留，仓库仍为 clean。当前候选等待第三轮
  Runtime/Standards/Product 三镜头复审；Cloud OAuth、updater、Developer ID、公证、stapled DMG
  仍未交付。

### D2.3 Supervisor 最终短审修复 mini-spec（2026-08-25）

本增量只关闭 D2.2 第三轮剩余的两条 supervisor 并发原子性问题；Provider journal、desktop
adapter、capability 与已放行 artifact 合同保持不变。

**Launch success commit 不变量**

- readiness 通过后的 generation 检查、durable orphan ownership、`pid`/`child` 占有与 ready session
  发布必须构成一个 generation + ownership CAS 成功提交；不得在一次可被 restart 插入的预检查后继续
  分段登记 ownership 或 ready。
- 只有当前 generation 且 host 未 stopping 时才能提交成功。CAS 失败是 superseded/cancelled：旧 launch
  不得改变新 generation 的 orphan、PID、child 或 snapshot，并必须终止自己仍持有、尚未发布的 child。
- process identity 观察在状态锁外完成；durable ownership 写入与内存发布在单一同步临界区完成，临界区内
  不调用会重入 host mutex 的诊断或终止路径，失败后的 child cleanup 在锁外执行。

**Configuration restart begin 不变量**

- `configuration_restart_in_progress` 必须先于任何 snapshot 或 generation 写入被检查。重复 begin 是纯
  no-op，不得改变 blocked/`reason_code`/`action`、generation、orphan identity 或当前 stop owner。
- 确定性交错覆盖：第一个 restart 的 stop-failed 已发布 blocked、但尚未 finish 时第二 action 到达；
  blocked 修复动作必须保持，随后原 stop owner 仍能按原 generation 完成收敛。

**Red/Green 与门禁**

- 先增加上述两个确定性 supervisor Red，再做最小 Green；focused 通过后运行 Rust full/fmt/clippy，
  并按 Rust-only 影响运行相邻 Provider、desktop Python、Vue focused 与 source smoke。
- 代码与文档使用独立中文 commit；因 Rust 产品源码变化，从新的 clean docs HEAD 重做正式 macOS
  arm64 bundle，复核 receipt、strict codesign、secret scan、分类计数与 host/launcher/sidecar 零残留，
  最后固定 code/docs/receipt SHA 并等待第四轮最终短审。

### D2.3 实施收口（2026-08-25）

- **代码候选**：`8788404ea6bd1e5a0408f0057a2278dbf4442290`。`launch_once` 在 readiness
  与 process identity 检查后，以同一 host state 临界区执行 generation/stopping/empty ownership CAS、
  durable orphan 写入及 PID/child/ready 发布。restart 不能插入该提交；CAS 失败时 child 仍由旧 launch
  独占并在锁外终止，不登记 orphan、PID 或 ready，也不覆盖新 generation 状态。
- **锁与 I/O 边界**：process identity 观察、失败后的 child kill 与 diagnostics 均在 host mutex 外；
  repository 写入不回调 host state，并在 durable ownership 与内存发布之间同步完成，因此失败不会发布
  ready，成功提交也不会留下可被 restart 分割的 ownership 窗口。
- **重复 restart begin**：先检查 `configuration_restart_in_progress`，再修改 snapshot 或 generation。
  stop-failed blocked 与 finish 之间到达的第二 action 为纯 no-op，保留原 `reason_code/action`、generation、
  orphan identity 和 stop owner；原 owner 仍能按同一 generation 完成收敛。
- **Red/Green 证据**：旧实现的精确重复 begin 交错得到 `left: "starting" / right: "blocked"`；旧
  launch 路径不存在能在预检查后再次核对 generation 并归还未发布 child 的成功提交 seam。新增两条
  deterministic supervisor 合同先失败，最小 Green 后 supervisor focused `18 passed`。
- **源码门禁**：Rust full `58 passed`（保留上轮 56 条并新增 2 条）、fmt、clippy `-D warnings`
  通过；其中 Provider `14 passed`，唯一临时 macOS Keychain service/account round-trip 与 cleanup 通过。
  Rust-only 相邻门禁为 Vue host adapter/HostGate focused `39 passed`、Python desktop `61 passed`、source
  product smoke `1 passed`；`git diff --check` 与 changed-diff private-key/API-key 扫描通过。正式 arm64
  bundle 尚待从下一笔 clean docs HEAD 重建，不能沿用 `5a1d3db` 收据。
- **正式 artifact**：已从 clean docs HEAD `a01277d32cbb8d0a05ea15240ad0e0887e364dc9`
  运行唯一入口；receipt 位于
  `/private/tmp/sage-desktop-a01277d32cbb8d0a05ea15240ad0e0887e364dc9/desktop-bundle-receipt.json`，
  SHA-256 `063b208780afc56a00bae91669961e66e3a6ccc5fa025ca202a55c55646959f5`。
  `source_dirty=false`、Python `3.12.13`、target `aarch64-apple-darwin`；sidecar 12 项真实
  product smoke 与 `.app` 6 项 lifecycle smoke 全部 `passed`，包含冻结产物模型 turn、SQLite RAG、
  capability 与副作用工具 fail-closed 断言。
- **计数与签名**：sidecar receipt SHA-256 为
  `408e474751cd6c447f92ea63d991178a662369927ce2deb5653a3596f6faac6d`；269 manifest entries
  逐项实测为 247 个 regular files、22 个 symlinks、0 missing。写入 `build-receipt.json` 后物理
  sidecar 为 248 个 regular files、116 个 directories、22 个 symlinks；最终 `.app` 为 274 个
  regular files、121 个 directories、0 symlink。host、launcher、sidecar 均为 arm64 Mach-O；app deep
  strict 与三个嵌套 executable strict codesign 均通过，签名为 ad-hoc。
- **最终安全复核**：packaged product secret、测试 sentinel、通用 `sk-*`、private-key marker 与
  `.env` 均零命中；唯一 `.pem` 是 certifi 公共 CA trust bundle。`sage-desktop`、`sage-api`、
  `sage-api-aarch64-apple-darwin` 经 `pgrep -x` 复查均无残留，仓库保持 clean。当前候选等待第四轮
  Runtime/Standards 最终短审；Cloud OAuth、updater、Developer ID、公证、stapled DMG 仍未交付。

### D2.4 Supervisor ownership recovery 修复 mini-spec（2026-08-25）

本增量只关闭 D2.3 第四轮 Runtime/Standards 共同确认的两个 supervisor 反例；D2 已放行的
Provider/Keychain/journal/capability 与 artifact 产品合同保持不变。

**未发布 child cleanup 不变量**

- launch success CAS 拒绝必须把已观察的 `OrphanRecord` 与仍由 caller 独占的 child 一并返回，不能只
  调用会消耗句柄、却没有 wait/identity verification 保证的 `CommandChild::kill()`。
- caller 在 host mutex 外使用既有 `terminate_runtime(record)` 完成 PID/start-time/executable identity
  校验。明确 stopped/not-found 或 identity reused 是该未发布旧 identity 的安全终态；signal error、
  仍存活、仅 `KillSent` 或无法确认均不安全。
- 不安全结果必须 durable 保存为 pending unpublished orphan，发布
  `blocked + desktop_unpublished_sidecar_cleanup_failed + open_diagnostics` 并写脱敏 diagnostic；任何 pending
  orphan 均阻止后续 generation starting/ready。重启先 reconciliation，安全收敛后才允许 launch。
- child termination、process observation、repository I/O 与 diagnostics 不得在会重入的 host mutex 路径
  中交错；若 durable persist 自身失败，保留内存 ownership 并升级为 persistence blocked。

**Starting transition 不变量**

- `launch_once` 的 starting transition 必须在同一 host state 临界区检查 generation、stopping、
  configuration restart 与 pending orphan，再决定是否发布 `starting`；预检查只允许优化，不是授权。
- 确定性 barrier 交错覆盖 `old precheck -> new ready -> old resume` 与
  `old precheck -> restart stop-failed blocked/finish -> old resume`。旧 caller 只能返回 superseded，不能
  改变新 generation 的 snapshot/session/reason/action。

**Red/Green 与门禁**

- P1 覆盖 signal/kill error、同 identity 仍存活、identity reused 与 confirmed stopped；P2 必须经过
  `launch_once` 实际使用的 production starting seam，不从 success commit helper 起步。每项先 Red 再最小
  Green，并用 logic-lens 检查资源、持久化、generation 与锁顺序。
- focused 后运行 supervisor/Rust full/fmt/clippy、Provider/Keychain、desktop Python、Vue host adapter/
  HostGate、source product smoke 与 `git diff --check`。代码/docs 独立中文 commit；Rust 源码变化后从新
  clean docs HEAD 重建正式 arm64 bundle，固定 receipt、12+6 smoke、manifest、strict codesign、secret
  scan、零残留与 code/docs/receipt SHA，再等待第五轮短审。

### D2.4 实施收口（2026-08-25）

- **代码候选**：`e66f960307d5b2aadb3a84b6b7f91f6a70ce2e42`。launch success CAS 拒绝现在归还
  完整 `OrphanRecord + child`，production caller 在 host mutex 外持有 child 并按 PID/start-time/
  executable 执行 verified termination。只有 `Stopped/IdentityChanged` 对该未发布旧 identity 属于安全
  收敛；signal error、仍存活、`KillSent` 或无法确认都会写入独立
  `desktop-host-unpublished-orphans.json`，发布专用 blocked reason/action 与脱敏 diagnostic。
- **恢复与 fail closed**：pending unpublished ownership 与当前 generation 的 main orphan 分开持久化，
  journal 损坏、写入失败和并发追加均不静默覆盖。pending 会阻止 starting、success commit 与
  configuration restart；重启逐个做 identity-verified reconciliation，只有安全终态才移除。journal
  persistence failure 保留内存 ownership 与 `desktop_state_persist_failed`，后续 failure accounting 不再
  覆盖该状态或消费 crash budget。
- **production caller 修复**：logic-lens 发现健康检查后的 stopping/generation 裸 `child.kill()` 会绕开
  verified cleanup，仍可能丢失已读取 Provider secret 的旧进程 identity；本轮移除这两个预返回，让健康
  sidecar 统一经过 process observation、generation/ownership CAS 与 caller cleanup。测试同时证明 child
  在异步 termination 完成前不会 Drop。
- **starting 原子性**：`launch_once` 使用的 transition 在同一 host state 临界区完成 generation、
  stopping、configuration restart、pending ownership 检查与 `starting` 发布。两个 barrier 交错分别让
  新 generation 先发布 ready、以及 restart 先发布 stop-failed blocked；旧 launch 恢复后均只返回
  `desktop_launch_superseded`，不覆盖 session/reason/action。
- **Red/Green 证据**：P2 旧实现的两个精确交错均得到 `left: Ok(()) / right:
  Err("desktop_launch_superseded")`；P1 旧实现的 kill error/timeout/`KillSent` 用例在 durable journal 中
  得到 `pid=None` 而期望 `pid=42`。新增 production caller Red 先因缺少统一 cleanup caller 编译失败，
  Green 后 supervisor focused `25 passed`，repository 文件合同 `5 passed`，覆盖 journal 往返/去重、
  corruption fail closed 与并发唯一追加。
- **源码门禁**：Rust full `68 passed`、fmt、clippy `-D warnings` 通过；Provider `14 passed`，唯一临时
  macOS Keychain service/account round-trip 与 cleanup `1 passed`。Rust-only 相邻门禁为 Vue host
  adapter/HostGate `39 passed`、Python desktop `61 passed`、source product smoke `1 passed`；changed-diff
  secret scan 零命中，host/launcher/sidecar 精确进程检查均为零，`git diff --check` 通过。正式 arm64
  bundle 尚待从下一笔 clean docs HEAD 重建，不能沿用 `c50d658` 收据。

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
