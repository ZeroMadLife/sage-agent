# Sage Loop Engineer Phase 2 实施计划

> 日期：2026-07-16
>
> 状态：执行中；`SHADOW_WRITE` 已部署，Claude 真实链路已烟测，PR canary 待 GitHub 认证实跑
>
> 设计依据：`docs/superpowers/specs/2026-07-16-sage-loop-engineer-phase2-design.md`

## 目标

在不改变当前 Phase 1 默认行为的前提下，实现 Codex 扫描、受控前端修复、确定性验证、
中文 Draft PR、cc-connect Claude 独立审查、去重报告和分阶段自动合并。每个任务完成后
保持 `DRY_RUN` 可回退，直到 shadow 和 canary 门禁通过。

## 当前进度（2026-07-16）

- 已完成小版本 1-3：mode/SQLite 迁移、dirty path 避让、Scanner/Fixer、diff policy；
- 小版本 4 已完成固定 Vitest/build 验证与 quota 证据，前后截图仍待补；
- 小版本 5-6 的 GitHub Draft PR 与 cc-connect Claude Reviewer 已实现并通过 fake 纵向测试；
- Claude Reviewer 已在前台与 launchd daemon 各完成一次真实 `PASS` 烟测，临时 evidence 均已清理；
- 当前部署保持 `SHADOW_WRITE`，小时任务不启动 Reviewer；`gh` 尚未认证，因此不会 push 或创建 PR；
- 小版本 4 截图、小版本 5 Issue、小版本 6 自动返工、小版本 7-8 rollout 仍待完成；
- 实现分支为 `codex/loop-phase2`，基于 Phase 1 PR head 叠加，未修改根目录用户工作。

## 实施原则

- Phase 1 PR 先独立审查并合入 `dev/sage-v7`；Phase 2 使用新的 `codex/*` 分支和 PR；
- 每个小版本只增加一层权限，默认关闭下一层；
- 模型只读或改 worktree，Controller 独占 GitHub 外部动作；
- 测试先覆盖失败关闭、路径和副作用，再接入真实 Codex、Claude 与 GitHub；
- 不修改根目录用户工作，不复用主飞书开发会话。

## 小版本 1：状态、模式与策略契约

### 修改范围

- 更新 `core/loop_harness/config.py`、`models.py`、`state.py`；
- 新增 Scanner、Fixer、Reviewer 结构化 schema；
- 更新 `docs/loop-harness/POLICY.md`、`PROMPT.md`、`SOP.md`、`REVIEW.md`；
- 扩充 `tests/core/loop_harness/test_state.py` 和契约测试。

### 实现内容

- 增加 `SHADOW_WRITE`、`PR_CANARY`、`AUTO_MERGE_TIER_A` mode；
- 增加 candidate、artifact、PR、review、Issue 和 rollout 状态；
- 保存 dirty path、目标 base/head SHA、重试次数、每日 PR 额度、指纹和证据保留时间；
- schema 迁移必须向后兼容现有 SQLite；
- 未显式启用时仍只允许 Phase 1 `DRY_RUN`。

### 验证

- 旧数据库可启动并迁移；
- mode 非法、策略漂移或 schema 不兼容时失败关闭；
- 返工次数、开放 PR 数和 rollout 门槛由状态层确定性约束。

## 小版本 2：Git 隔离、dirty path 避让与 diff policy

### 修改范围

- 更新 `core/loop_harness/git.py`、`runner.py`；
- 新增确定性 diff policy 模块；
- 扩充 `tests/core/loop_harness/test_git.py`、`test_runner.py`。

### 实现内容

- 以 `origin/dev/sage-v7` 创建 scan/fix/review worktree；
- 用 NUL 分隔的 porcelain 输出收集根目录 dirty path；
- exact path、测试配对、直接 import 邻接和共享层变更触发避让；
- 检查 Worker 前后 `HEAD` 不变、tracked diff 在 allowlist 内；
- 实现 2 个生产文件、1 个测试文件、80 行预算；
- 拒绝 symlink、二进制、图片、依赖、配置和保护路径；
- 同时只允许一个开放 Loop PR lease，并按 Asia/Shanghai 日期限制每天一个新 PR。

### 验证

- 根目录脏但不重叠时运行继续；
- 重叠、邻接、超预算、保护路径、Worker 自行 commit 时无远程副作用；
- 目标 SHA 漂移时旧候选失效并重新验证；
- 清理只删除 Loop 管理的 worktree。

## 小版本 3：Scanner 与 Fixer 双 Worker

### 修改范围

- 拆分 `core/loop_harness/worker.py`；
- 新增版本化 scanner/fixer prompt 与 schema；
- 扩充 `tests/core/loop_harness/test_worker.py`。

### 实现内容

- Scanner 保持 read-only、ephemeral、无用户配置；
- Fixer 使用 workspace-write、无网络、单候选 job envelope；
- 固定扫描范围轮换、置信度阈值和中文输出；
- 将仓库源码、Issue 和运行数据声明为不可信输入；
- Worker 超时、非结构化输出、越权写入或宣称外部动作时失败关闭。

### 验证

- fake Codex 覆盖四种 Scanner verdict；
- Fixer 只能修改 envelope 允许路径；
- Prompt 注入、网络请求、依赖安装、commit 和保护路径修改被阻断；
- `DRY_RUN` 下 Fixer 永不启动，`SHADOW_WRITE` 下永不 push。

## 小版本 4：前端验证与视觉证据

### 修改范围

- 新增 Controller validation adapter 和 artifact manager；
- 新增仓库外 Playwright 运行环境与固定视口配置；
- 增加 validation/artifact 测试和 Runbook。

### 实现内容

- 按顺序运行定向 Vitest、全量 Vitest、build 和 diff check；
- 视觉候选在固定桌面/手机视口采集前后截图；
- 检查非空页面、资源加载、溢出和截图文件完整性；
- artifact 只写状态目录，失败 7 天、合并 14 天、总上限 1 GiB；
- 页面或 mock 环境无法稳定复现时降级为报告。

### 验证

- 命令失败、超时、页面空白、截图缺失和磁盘超限时不创建 PR；
- 证据清理不越过状态目录且满足保留期；
- 前端现有测试与 build 全绿。

## 小版本 5：确定性 GitHub PR/Issue 适配器

### 前置

- 完成 `gh` 最小权限认证，凭据由 macOS Keychain 管理；
- 验证私有仓库的 push、PR、Issue 和 Actions 状态访问；
- 不把 token 传入模型进程。

### 修改范围

- 新增 GitHub adapter、中文模板和 fake transport；
- 更新 `runner.py`、`cli.py`、Runbook；
- 增加 PR/Issue 幂等和副作用测试。

### 实现内容

- Controller 生成 commit、push 和中文 Draft PR；
- 强制目标分支、分支前缀、中文标题/正文和单问题 PR；
- 按指纹创建或更新带标签的报告 Issue；
- 记录每个外部副作用的 idempotency key；
- 网络或认证失败时保留本地候选，连续三次自动暂停。

### 验证

- 重试不会创建重复 PR、Issue、评论或 merge 请求；
- 非中文 PR、错误目标分支、目标漂移和开放 PR 超限被拒绝；
- token 不出现在进程参数、日志、SQLite 和测试快照中。

## 小版本 6：cc-connect Claude Reviewer

### 环境变更

- 新增 cc-connect 项目 `sage-loop-review`；
- work_dir 指向 Controller 管理的私有 evidence 根目录；
- Claude Code 使用 `plan` 模式、固定系统 Prompt 和独立 session key；
- 不修改现有 `sage-review` 知识库项目。

### 修改范围

- 新增 reviewer adapter、schema 和超时处理；
- 更新 `runner.py` 和飞书通知；
- 增加 fake cc-connect/Claude 测试。

### 实现内容

- PR 创建后通过 cc-connect 触发一次性审查；
- Reviewer 当前只读取 `shadow.patch` 与 `validation.json`，截图在视觉证据小版本补齐后接入；
- Reviewer 返回 `PASS/REQUEST_CHANGES/BLOCK`，Controller 校验 evidence 目录摘要未变化；
- 最多一次 Codex 返工和一次复审；
- 第二次失败保留 Draft，停止自动循环。

### 验证

- 不同 PR 使用不同 Claude session；
- 主 `sage`、`sage-review` 会话历史不包含 Loop 审查上下文；
- 无法解析、超时、写文件和缺少证据均为 `BLOCK`；
- 审查意见、PR 评论和飞书摘要为中文。

## 小版本 7：日报、索引与保留策略

### 修改范围

- 更新 digest、notifier、cleanup 和 status 输出；
- 新增 `docs/loop-harness/reports/README.md` 与参考政策索引；
- 增加日报幂等、Issue 去重和磁盘配额测试。

### 实现内容

- `NO_OP` 静默；
- Draft PR 完成首轮 Claude 审查后发送一条合并摘要；
- 每天 23:55 汇总 PR、review、Issue 和阻塞；
- 静态 README 链接 GitHub `loop-report` Issue 过滤视图；
- 每周只检测官方参考来源变化，生成报告而不自动修改 Loop 文档；
- 状态、日志、截图和临时 worktree 按硬配额清理。

### 验证

- 同一日期多次 digest 不重复发送；
- 同一指纹不重复创建 Issue；
- 小时运行不创建 Markdown；
- 日报内容短、中文、可从手机直接打开 PR/Issue。

## 小版本 8：Shadow、PR Canary 与自动合并

### 实现内容

1. 把 Phase 2 单轮 deadline 设为 55 分钟，cc-connect 外层 timeout 设为 60 分钟，lease
   保持 90 分钟，并为 Scanner/Fixer/验证/Reviewer/清理设置分段预算；
2. 在 `SHADOW_WRITE` 运行至少 3 次真实 diff、验证和 Claude 审查，不 push；
3. 审查 shadow 证据和磁盘增长，确认无根目录写入；
4. 切换 `PR_CANARY`，运行至少 7 天且至少 5 个 PR；
5. 统计接受、拒绝、返工、冲突、回滚和误报；
6. 只有门槛满足后启用 `AUTO_MERGE_TIER_A`；
7. auto-merge 使用 squash，不绕过 required checks，合并后删除远程 Loop 分支；
8. 任一安全门禁失败时自动回退或暂停。

### Canary 通过条件

- 0 次保护路径、根目录或主会话污染；
- 0 次重复远程副作用；
- 0 次绕过 CI 或目标 SHA 漂移合并；
- Claude 审查和人工抽查无 P1/P0 遗漏；
- PR 接受率、误报率和平均处理量达到人工确认阈值；
- 本机状态目录持续低于配额。

## 总体验证门禁

每个小版本至少执行：

```bash
pytest tests/core/loop_harness tests/scripts/test_loopctl.py -q
ruff check core/loop_harness tests/core/loop_harness tests/scripts/test_loopctl.py
mypy core/loop_harness
git diff --check
```

涉及真实前端候选时追加：

```bash
cd frontend
npm run test -- --run
npm run build
```

涉及 cc-connect、GitHub 或调度变更时，使用 fake transport 通过后再做一次受控真实 smoke，
并核对 daemon、cron、session 隔离、PR/Issue 幂等、飞书投递和状态目录体积。

## 提交与 PR 切片

- Phase 1 保持当前 PR 独立，不向其中加入 Phase 2 实现；
- Phase 2 每 1 至 2 个相邻小版本形成一个职责清晰的 commit；
- 实现 PR 首先保持 Draft，完成 shadow 后转 Ready；
- PR 正文持续更新实际验证、关闭风险和未完成 rollout，不把设计能力写成已交付；
- 每次小版本收口更新 Obsidian `sage-learning` 的 source commit、测试证据和下一阶段边界。

## 回滚

最高优先级回滚是 `loopctl pause`。权限回退依次为：

```text
AUTO_MERGE_TIER_A -> PR_CANARY -> SHADOW_WRITE -> DRY_RUN -> PAUSED
```

回滚不得删除 SQLite、日志、PR、Issue 或审查证据。需要清理 worktree 和 artifact 时只能
使用 Controller 的受控 cleanup；凭据撤销通过 GitHub/Keychain 完成，不写入仓库脚本。
