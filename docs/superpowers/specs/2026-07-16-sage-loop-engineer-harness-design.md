# Sage Loop Engineer Harness 设计

> 状态：设计已确认，等待实施计划与实现
>
> 日期：2026-07-16
>
> 目标分支：`dev/sage-v7`
>
> 运行位置：当前 Mac，本期不部署到服务器

## 1. 结论

Sage 新增一个本机常驻的 Loop Engineer Harness。它通过现有飞书 `cc-connect`
链路按小时唤醒 Codex，在独立 worktree 中扫描最新 `origin/dev/sage-v7`，只自动
修复有确定证据的小 bug，并通过本地门禁、独立只读复审和 GitHub CI 后创建 PR。

合并按风险分三级：

1. 极小、低风险且证据充分的修复可以由 GitHub 自动 squash merge；
2. 仍属于小 bug、但影响稍大的修复保留为 Draft PR，由用户查看 diff 后决定；
3. 超出范围、触及保护区域或无法稳定复现的问题只进入发现报告，不修改代码。

Harness 不直接修改 `main`，不直接向 `dev/sage-v7` push，不部署，不处理生产数据，
也不能自行修改自己的 Prompt、Policy、Controller 或 GitHub 门禁。后续提高自治等级
必须通过人工审查的策略 PR 和策略版本升级完成。

## 2. 背景

当前 Sage 已具备以下可复用基础：

- 飞书机器人、`cc-connect 1.4.1` 与本机 Codex 链路；
- `cc-connect` daemon、cron、独立 session 和主动回传能力；
- 仓库级 `AGENTS.md`、测试命令、构建命令和 Git 收口约定；
- Coding Runtime 中的权限、审批、run trace、lease 和终态思路；
- Knowledge Job 中经过测试的租约、重试、恢复和幂等模式；
- Git worktree 开发经验，以及 `dev/sage-v7` 集成分支约定。

当前尚不具备安全自动合并所需的完整基础：

- 本机尚未安装 `gh` CLI；
- 仓库尚无可作为 required checks 的 `.github` CI workflow；
- `dev/sage-v7` 与 `main` 的远程保护规则尚未按本设计验收；
- 仓库根目录仍需完成一次清洁化，使其只承担集成工作区职责；
- 当前 `AGENTS.md` 仍约定小版本默认直接在根目录开发，与本设计的新 worktree 规则
  冲突，启用前必须通过人工提交统一；
- 现有飞书 `sage` 项目使用日常交互权限，不能直接复用为无人值守执行环境。

因此，本设计不是“现在已有自动化”的声明。只有第 17 节的启用门禁全部通过后，
用户才能通过飞书命令正式开启 24 小时 Loop。

## 3. 目标与非目标

### 3.1 目标

- 每小时、全天扫描一次，长期发现并修复低风险小 bug；
- 与用户白天开发并行，不读取、不复制、不覆盖人工未提交改动；
- 所有代码改动都通过 PR 进入 `dev/sage-v7`；
- 极小修复可以自动合并，稍大修复必须人工看 diff；
- 大范围问题形成短、可追踪、可去重的报告；
- 用户可以在手机飞书查看 PR、异常和每日摘要，并暂停或恢复 Loop；
- 每次运行都有明确终态、证据、清理和恢复路径。

### 3.2 非目标

- 不部署 Sage，也不管理当前临时服务器镜像；
- 不自动合入 `main`；
- 不自动执行产品需求、架构重写、依赖升级或技术栈迁移；
- 不替代人工 roadmap、版本设计和发布验收；
- 不建设多仓库、多租户、分布式队列或独立 Web 管理后台；
- 不让模型根据代码或文档内容自行扩大权限；
- 不追求每小时必须产出 PR、报告或文档。

## 4. 不可破坏的工程约束

### 4.1 根目录是干净集成工作区

`/Users/zeromadlife/Desktop/tour-agent` 固定检出 `dev/sage-v7`，只承担以下职责：

- 查看集成分支当前状态；
- 在 PR 合并后执行 `git fetch` 和 `git merge --ff-only origin/dev/sage-v7`；
- 运行集成验证和本地联调；
- 作为所有临时 worktree 的共同仓库入口。

人工开发与 Loop 开发都使用独立 worktree。启用前必须逐项处理根目录现有未跟踪或
未提交内容：确认应提交、应加入 ignore、应移动到仓库外，或仍需在临时分支完成。
Harness 不得自动删除、stash、reset、checkout 或 rebase 这些内容。

这项新决策覆盖当前 `AGENTS.md` 中“常规小版本直接在根目录开发”的旧约定。Phase 0
必须由人工提交更新 `AGENTS.md`：根目录改为集成工作区，人工和 Loop 功能开发都走
临时 worktree/分支与 PR。Loop 本身没有权限修改该规则。

任一运行发现根目录 `git status --porcelain` 非空、分支不是 `dev/sage-v7`，或者
本地分支无法 fast-forward 到远程时，必须 fail closed：暂停写入和自动合并，保留
只读诊断能力，并立即发送飞书告警。

### 4.2 远程分支是合并真相源

- 扫描基线始终是最新 `origin/dev/sage-v7`，不是根目录未推送状态；
- Loop 分支使用 `codex/loop-<date>-<finding-id>`；
- 所有变更必须通过 GitHub PR；
- 自动合并只能由 GitHub 在 required checks 通过后执行；
- Controller 禁止使用管理员绕过、force push 或直接更新目标分支；
- `main` 只接受独立发布流程，不接受 Loop PR。

### 4.3 一次只处理一件事

- 同一时刻最多一个活动 run；
- 同一时刻最多一个待处理的 Loop 代码 PR；
- 每个 run 最多选择一个候选问题；
- 每个 PR 只修复一个可独立验证的问题；
- 文档发现按天聚合，不为每次扫描创建新文件。

## 5. 运行架构

```text
macOS launchd
  -> cc-connect daemon
  -> hourly cron: loopctl run
  -> Loop Controller
       |- acquire lease and load SQLite state
       |- fetch origin/dev/sage-v7
       |- verify clean integration checkout and PR capacity
       |- create isolated worktree and codex/loop-* branch
       |- launch a fresh sandboxed Codex run
       |- classify NO_OP / FIX / REPORT / BLOCKED
       |- enforce diff policy and run verification
       |- launch a fresh read-only Reviewer
       |- push branch and create PR when eligible
       |- request GitHub auto-merge only for Tier A
       |- persist terminal state and clean worktree
       `- return concise Feishu notification

daily cron: loopctl digest
  -> aggregate SQLite state
  -> send one-screen Feishu daily report
```

### 5.1 `cc-connect`

`cc-connect` 负责：

- 通过 launchd 常驻；
- 按 `17 * * * *` 触发小时任务，避开整点集中任务；
- 按 `55 23 * * *`、`Asia/Shanghai` 发送每日报告；
- 将受控操作命令和任务结果送回绑定的飞书会话；
- 限制允许操作 `sage-loop` 的飞书用户与群。

新建独立 `sage-loop` 项目，不复用日常聊天的 `sage` session。小时运行必须使用新
session，不能继续上一轮 transcript。自由文本消息不能直接改变 cron、权限或合并
策略。

小时任务使用 `cc-connect cron --exec` 调用固定的 `loopctl run` 入口，不能使用
`cron --prompt` 让模型自行组织控制流程。飞书控制命令也必须经过窄命令适配器映射到
固定 `loopctl` 子命令；若当前 `cc-connect` 版本无法安全完成该映射，则远程启停能力
不得上线，只保留本机 CLI，直到适配器验收通过。

### 5.2 Loop Controller

Controller 是确定性控制面，负责所有有副作用的生命周期决策：

- 锁、租约、超时、幂等和恢复；
- Git fetch、worktree、branch、push 和 PR；
- 候选去重、轮换游标和冷却；
- 文件、行数、路径和变更类型检查；
- 测试、构建、复审和 GitHub 状态；
- 飞书事件通知和每日聚合；
- 清理临时分支与 worktree。

Codex 无权通过自然语言覆盖 Controller 判定。即使模型输出“可以自动合并”，只有
Controller 根据结构化证据重新计算为 Tier A 才能请求 auto-merge。

Controller 安装时生成受控 manifest，固定 Policy、Prompt、Controller、验证脚本和
CI workflow 的允许 commit/hash。每次 run 在启动 Worker 前验证 manifest；不一致时
进入 `PAUSED_POLICY_DRIFT`。Loop PR 不得更新 manifest。

### 5.3 Codex Worker

Worker 每次在全新 session 和隔离 worktree 中运行，使用带 sandbox 的 unattended
模式，不使用当前 `yolo` 日常会话。它只负责：

- 阅读指定扫描范围及必要上下文；
- 发现一个高置信候选；
- 构造最小复现；
- 在允许范围内修改代码和测试；
- 运行聚焦验证；
- 返回结构化结果和证据。

Worker 不负责 push、创建 PR、启用 auto-merge、同步根目录或修改调度状态。

### 5.4 Read-only Reviewer

只有 `FIX` 候选通过本地确定性门禁后才启动 Reviewer。Reviewer 使用全新上下文，
只读取：

- 已确认的 Policy 与职责边界；
- base SHA、head SHA 和完整 diff；
- 失败复现与回归测试；
- 聚焦测试、完整测试和构建结果；
- Worker 的结构化理由。

Reviewer 不能编辑文件或补救失败，只能返回 `PASS` 或 `REJECT` 及短理由。
`REJECT` 一律阻断 push 和自动合并。

## 6. 本地状态与目录

运行数据不进入 Sage 仓库：

```text
~/.local/state/sage-loop/
|- state.sqlite3
|- logs/
|- reports/
`- locks/

~/.local/share/sage-loop/
`- worktrees/
```

SQLite 至少记录以下逻辑实体：

| 实体 | 关键字段 | 用途 |
|---|---|---|
| `runs` | run_id、base_sha、policy_version、state、started_at、finished_at | 运行终态与审计 |
| `leases` | resource、owner、expires_at、fencing_token | run 与根目录同步的单实例保护 |
| `scan_cursors` | module、base_sha、last_scanned_at | 模块轮换 |
| `candidates` | fingerprint、evidence、risk、cooldown_until | 去重与冷却 |
| `findings` | finding_id、status、summary、evidence、report_ref | 大问题跟踪 |
| `pull_requests` | number、head_sha、tier、state、merged_sha | PR 生命周期 |
| `daily_digests` | date、counts、sent_at | 每日追踪幂等 |

SQLite 是本机 Harness 的 canonical state。日志用于诊断，不反向决定状态。日志默认
保留 30 天，候选指纹和日报保留至少 90 天；清理任务不得删除仍关联开放 finding
或 PR 的记录。

## 7. 每小时 SOP

### 7.1 Preflight

1. 生成 `run_id`，获取带 fencing token 的单实例 lease；
2. 验证 daemon、磁盘空间、Git、Codex 与网络基础状态；
3. 验证根目录分支和清洁度；
4. `git fetch --prune origin`，解析最新 `origin/dev/sage-v7` SHA；
5. 在独立 `root-sync` lease 下比较根目录 HEAD：落后时只允许 `ff-only`，ahead 或
   diverged 时 fail closed；
6. 验证受控 manifest、GitHub 规则和 required checks 基线；
7. 查询开放的 `codex/loop-*` PR 与本地未终结 run；
8. 有待审代码 PR 时跳过新修复，只更新状态；
9. 创建临时 worktree 和只属于本 run 的分支。

### 7.2 选择扫描范围

优先级如下：

1. 上次扫描后进入 `dev/sage-v7` 的新增或修改代码；
2. CI、测试、ruff、mypy、TypeScript 或构建暴露的确定性问题；
3. 按模块游标轮换的局部代码与相邻测试；
4. 已有 finding 的可复现性复查，但不自动扩大原 finding 的范围。

`TODO`、`FIXME`、代码风格偏好、纯主观重命名或“看起来可以重构”不能单独成为
候选证据。没有可靠候选时返回 `NO_OP`，不能为了产出而修改代码。

### 7.3 Worker 结果

Worker 只能返回以下状态：

- `NO_OP`：没有满足证据标准的问题；
- `FIX`：完成一个允许范围内的最小修复；
- `REPORT`：发现真实问题，但超出自动修改范围；
- `BLOCKED`：环境、权限、依赖或工具异常。

任何缺字段、无法解析、结论冲突或超时都按 `BLOCKED` 处理，不从自由文本推断成功。

### 7.4 Terminalize

Controller 必须在同一事务边界内记录 terminal state 与 lease 释放意图。清理遵循：

1. 先保存 diff、测试、Reviewer 和 GitHub 证据；
2. 再确认 PR 或 finding 的远程/本地状态；
3. 只清理干净且无未推送有效变更的 worktree；
4. 最后释放 lease；
5. 清理失败进入 recovery 队列，不伪装为成功。

单次 run 最长 40 分钟。下一小时触发时若旧 run 仍持有有效 lease，则记录一次
`SKIPPED_BUSY`。lease 90 分钟过期后可以由新 Controller 恢复，但必须使用新的
fencing token，旧进程不能继续提交终态。

SQLite fencing 不能天然阻止已经失去 lease 的旧进程调用 GitHub。Controller 在每个
外部副作用之前和之后都必须重新校验 resource、owner 与 fencing token，包括创建
worktree、commit、push、创建/更新 PR、请求 auto-merge、同步根目录和删除分支。
每个远程动作同时携带 `run_id`、精确 head SHA 或幂等键；校验失败立即终止旧进程。

## 8. 候选与三级处置策略

### 8.1 所有可修改候选的共同条件

只有同时满足以下条件，才能进入 `FIX`：

- 有失败测试、静态检查、构建错误或稳定最小复现；
- 只解决一个行为问题；
- 修改前能说明预期行为，修改后有独立验证；
- 最多修改 3 个文件，总增删不超过 150 行；
- 必须增加回归测试，或由已有确定性测试直接覆盖；
- 不通过删除测试、放宽断言、吞掉异常或增加 ignore 来获得绿色；
- 不触及第 9 节保护区域；
- 不需要新增依赖、迁移数据或改变公开产品决策。

### 8.2 Tier A：自动合并

Tier A 是 `FIX` 的严格子集：

- 最多 2 个实现文件和 1 个测试文件；
- 总增删不超过 80 行；
- 复现与修复均为确定性；
- 不修改公共 API、共享 schema/store 或跨模块契约；
- 不包含二进制、生成文件、符号链接、文件模式或 lockfile 变化；
- Worker 聚焦测试、本地完整门禁、生产构建全部通过；
- 独立 Reviewer 返回 `PASS`；
- PR head 与审核时的精确 SHA 相同；
- GitHub required checks 对最新 base 通过。

满足后创建普通 PR，添加 `loop:auto-merge` 标签，并请求 GitHub squash auto-merge。
Controller 不在本地执行 merge。

### 8.3 Tier B：人工审查

候选仍满足共同条件，但不满足任一 Tier A 限制时进入 Tier B：

- 创建 Draft PR；
- 添加 `loop:manual-review` 标签；
- 飞书立即发送一条短消息，包含问题、风险原因和 PR 链接；
- 用户查看 GitHub diff 后手动 ready、merge、关闭或要求修改；
- Harness 不重复催促，只在状态变化和每日报告中展示。

Tier B 仍不得触及保护区域。人工审查不是让 Loop 绕过 Tier C 的通道。
如果用户向 Tier B 分支追加提交，Harness 立即撤销自己的 Reviewer 结论并停止管理
该分支；后续合并完全进入人工流程，直到 PR 关闭后才做安全清理。

### 8.4 Tier C：只报告

以下情况只记录 finding：

- 超过 3 文件或 150 行；
- 认证、权限、密钥、隐私、租户隔离；
- 数据库迁移、共享 schema/API/store；
- 部署、CI、基础设施、依赖升级；
- 跨模块重构、产品行为变化、大型性能优化；
- 无法稳定复现或收益无法证明；
- 需要用户产品选择或扩大当前 scope。

finding 必须包含稳定 ID、证据、影响、为什么不能自动修、建议的下一步和去重指纹。
不得输出长篇泛化架构建议。

## 9. 保护区域与禁止操作

### 9.1 永久禁止自动修改

- `AGENTS.md`、`CLAUDE.md` 和其他指令文件；
- `docs/loop-harness/**`；
- Loop Controller、策略检查器和其测试基线；
- `.github/**`、branch/ruleset/auto-merge 配置；
- `.env*`、凭据、密钥、证书和本机认证配置；
- `db/migrations/**` 与生产数据脚本；
- 认证、授权、租户、OAuth 和 token 存储实现；
- Docker、服务器 bootstrap、部署与回滚脚本；
- package lock、依赖清单和自动生成产物；
- 二进制、图片、归档文件和符号链接。

保护清单由版本化 Policy 管理。路径不在清单中不代表自动安全；共享契约和语义风险
仍必须由 Controller 分类为 Tier C。

Controller、策略检查器和测试基线的精确路径由实施计划固定，并写入受控 manifest。
未被 manifest 覆盖的安装不允许执行 `/loop enable`。

### 9.2 永久禁止命令

- `git push --force`、`git reset --hard`、`git clean`；
- 绕过 hooks、CI、Reviewer、branch protection 或 required checks；
- `gh pr merge --admin` 或任何管理员 bypass；
- 直接 push `dev/sage-v7` 或 `main`；
- `sudo`、系统服务修改、部署、数据库迁移和生产数据操作；
- 下载并执行远程脚本；
- 修改 Keychain、`~/.ssh`、`~/.codex`、`~/.cc-connect` 凭据；
- 输出、复制或提交 secret、token、cookie 和隐私数据。

Agent 默认不需要网络。fetch、push、GitHub API 和飞书通知由 Controller 的窄适配器
完成。

## 10. 验证与复审门禁

### 10.1 聚焦验证

Worker 必须先运行能复现问题的最小测试或检查，记录修改前失败与修改后通过。预期值
必须来自公开行为，不得从修复实现本身复制。

### 10.2 本地完整门禁

只有候选修复才运行完整门禁；`NO_OP` 不消耗完整测试资源。门禁至少包含：

- 与改动匹配的聚焦测试；
- `scripts/check.sh`；
- 前端受影响时执行 `npm run test -- --run`；
- `npm run build`；
- `git diff --check`；
- secret、禁止路径、文件模式、二进制和 diff budget 检查。

任一检查缺失、超时或失败都不能创建可合并 PR。环境故障可以保留本地证据并进入
`BLOCKED`，不能把“测试未运行”描述为“测试通过”。

### 10.3 GitHub required checks

启用前必须建立 GitHub CI，并把稳定、唯一命名的检查设为 `dev/sage-v7` required
checks。至少覆盖后端 lint/type/test 与前端 test/build。远程规则要求 PR、最新 base、
禁止 force push、禁止删除目标分支、禁止管理员绕过。

本地 Reviewer 不能替代 GitHub CI，GitHub CI 也不能替代本地最小复现和独立复审。

## 11. PR、自动合并与本地同步

### 11.1 PR 内容

每个 Loop PR 必须包含：

- finding ID、policy version、base SHA 和 head SHA；
- 问题证据与最小复现；
- 修复摘要和明确非目标；
- 修改文件、diff budget 和风险等级；
- 本地验证与 Reviewer 结论；
- 自动合并资格或需要人工审查的原因；
- 回滚方式。

### 11.2 精确 head 保护

Controller 请求 auto-merge 时必须绑定审核过的 head SHA。任何后续提交都会使原审核
失效，必须重新运行本地门禁和 Reviewer。禁止在审核后只刷新标签或复用旧结果。

### 11.3 合并后

GitHub 合并成功后：

1. 记录 merge SHA；
2. 删除远程 Loop 分支；
3. 验证根目录仍干净并位于 `dev/sage-v7`；
4. 获取独立 `root-sync` lease，并确认根目录 HEAD 仍等于合并前记录的 base SHA；
5. `git fetch origin`；
6. `git merge --ff-only origin/dev/sage-v7`；
7. 执行最小 post-merge smoke；
8. 更新 PR 与 run 终态；
9. 清理临时 worktree。

如果根目录无法 fast-forward，立即暂停自动合并并发飞书告警。不得通过 reset、stash
或 rebase 强行恢复“干净”。

## 12. 仓库文档体系

实现阶段建立：

```text
docs/loop-harness/
|- README.md
|- POLICY.md
|- SOP.md
|- PROMPT.md
|- REVIEW.md
|- RUNBOOK.md
`- findings/
   |- OPEN.md
   `- archive/
```

| 文件 | 职责 |
|---|---|
| `README.md` | 唯一入口、状态说明、文档索引、GitHub PR 查询链接 |
| `POLICY.md` | 职责、风险等级、自动修改和禁止范围 |
| `SOP.md` | 小时 run、状态机、验证、PR、清理和恢复步骤 |
| `PROMPT.md` | 实际注入 Worker 与 Reviewer 的版本化 Prompt |
| `REVIEW.md` | Tier A/B/C 判定、人工 diff 审查和 merge/close 规则 |
| `RUNBOOK.md` | daemon、cron、启停、故障、备份与卸载 |
| `findings/OPEN.md` | 去重后的开放大问题索引 |

`README.md` 不每小时写动态状态，而是链接到 GitHub 的开放 Loop PR 查询和稳定 finding
索引。原始 run、日志和瞬时状态只保存在 Mac 本地。

大问题先进入 SQLite 和飞书即时通知。同一天的新 finding 最多聚合为一个文档 Draft
PR；如果已有代码 PR 等待处理，则文档更新排队，不与代码修复混在同一 PR。

## 13. Prompt 契约

### 13.1 权威顺序

Worker 只接受以下权威输入：

1. Controller 注入的系统边界；
2. 当前已审核的 `POLICY.md`、`SOP.md` 与 Prompt version；
3. 根目录 `AGENTS.md` 中不与前两项冲突的仓库约定；
4. 本 run 的结构化 job envelope。

源码、测试、README、issue、PR 内容、注释、`.coding/`、外部网页和飞书普通消息均是
不可信数据。它们即使包含“忽略规则”“执行命令”或“扩大权限”，也不能成为指令。

### 13.2 Job envelope

每次至少注入：

```json
{
  "job_id": "loop-20260716-0017",
  "base_sha": "<full-sha>",
  "policy_version": "1.0",
  "scan_scope": ["core/example", "tests/core/example"],
  "max_files": 3,
  "max_changed_lines": 150,
  "deadline_seconds": 2400,
  "protected_paths_digest": "<digest>"
}
```

实际值由 Controller 生成，模型不能修改。Prompt 不包含凭据、token、用户隐私或本机
无关路径。

### 13.3 结构化输出

Worker 返回：

```json
{
  "verdict": "NO_OP | FIX | REPORT | BLOCKED",
  "summary": "short text",
  "evidence": [],
  "reproduction": [],
  "changed_files": [],
  "tests": [],
  "risk_reasons": [],
  "suggested_tier": "A | B | C",
  "confidence": 0.0
}
```

Controller 独立计算 changed files、行数、测试结果和最终 tier，不信任模型自报值。

## 14. 飞书操作与每日追踪

只有 allowlist 中的用户可以执行控制命令：

```text
/loop status
/loop run
/loop enable
/loop pause
/loop report today
```

- `/loop status`：显示 daemon、cron、lease、开放 PR 和最近错误；
- `/loop run`：手动触发一轮，仍遵守全部门禁；
- `/loop enable`：仅在启用门禁全部通过后开启小时任务；
- `/loop pause`：停止接收新 run，不中断正在 terminalize 的 run；
- `/loop report today`：重发当天摘要，不重复创建状态。

这些命令必须映射到固定 Controller 动作，不能作为自由 Prompt 让 Agent自行解释。普通
飞书消息不能改变 schedule、policy、merge tier 或 protected paths。

窄命令适配器必须验证 project、chat、sender、命令名和参数数量，并为每次请求生成
幂等键。未知参数和自然语言尾随内容直接拒绝。适配器验收失败时，`/loop enable`、
`/loop pause` 和 `/loop run` 只允许在本机终端执行。

### 14.1 即时通知

以下事件立即通知：

- 创建 Tier A PR 并等待 CI/auto-merge；
- 创建 Tier B Draft PR；
- 新增 Tier C finding；
- 自动合并成功或 post-merge smoke 失败；
- 连续基础设施故障触发自动暂停；
- 根目录、分支保护或 required checks 不满足不变量。

`NO_OP`、正常跳过和重复候选不即时发消息。

### 14.2 每日报告

每天固定发送一条，即使当天没有改动。消息控制在手机一屏内，只保留非空重点：

```text
Loop 日报 07-16
扫描：24 次，21 无问题，2 跳过，1 异常
自动合并：#128 修复 xxx
待你审查：#129
大范围发现：LH-004
状态：运行中
```

日报必须可从 SQLite 重建，发送幂等；重复执行不会重复推送同一日报。

## 15. 失败、恢复与自动暂停

### 15.1 Fail closed 条件

- 根目录不干净或分支错误；
- base SHA 无法确认；
- GitHub branch protection、required checks 或 auto-merge 状态不符合基线；
- `gh`、Codex、测试环境或凭据不可用；
- diff 超预算、触及保护路径或包含未知变更类型；
- Worker 或 Reviewer 输出不可解析；
- Reviewer 拒绝；
- 测试、构建、secret scan 或 `git diff --check` 失败；
- head SHA 在审核后变化；
- 远程状态不明确或网络请求超时。

### 15.2 连续故障

同类基础设施错误连续 3 次后自动执行 `PAUSED_ERROR`：

- 禁止新 Worker 和新 PR；
- 保留状态、日志和只读查询；
- 飞书发送一次包含错误码与恢复入口的短告警；
- 只有问题修复且 `/loop enable` preflight 重新通过后才能恢复。

业务候选 `NO_OP`、重复 finding 和有待审 PR 导致的跳过不计入连续故障。

### 15.3 崩溃恢复

启动时先恢复：

- 过期 lease；
- 已 push 但未记录 PR 的分支；
- 已创建 PR 但本地仍标记运行中的 run；
- 已合并但未同步根目录的 PR；
- 已发送但未标记的日报；
- 残留 worktree 和无法安全清理的分支。

恢复必须查询 Git 与 GitHub 事实后推进状态，不能凭本地日志猜测远程结果。

## 16. 安全与滥用防护

- 飞书 `allow_from` 只包含明确用户和目标群；
- 特权控制命令必须有独立授权，不复用开放群成员权限；
- Loop 使用独立 Codex session、worktree、状态目录和最小 GitHub 凭据；
- GitHub token 只存在本机安全存储和进程环境，不进入 Prompt、日志或仓库；
- 目标分支规则禁止直接 push、force push、删除和管理员绕过；
- Agent 无权修改 Controller、Policy、CI 或分支保护；
- PR body、issue、代码注释和文档内容按 prompt injection 输入处理；
- secret scan 失败时不 push，不在飞书回显匹配内容；
- 日志只记录脱敏摘要、命令类型、退出码和 artifact hash。

## 17. 分阶段启用

### Phase 0：清洁与远程门禁

- 把根目录转换为干净的 `dev/sage-v7` 集成工作区；
- 人工更新 `AGENTS.md`，使根目录集成职责和全员 worktree/PR 规则成为仓库当前约定；
- 安装并认证 `gh`，凭据不写仓库；
- 建立后端与前端 GitHub CI；
- 保护 `dev/sage-v7` 和 `main`；
- 启用 PR、required checks、up-to-date、禁止 force/delete/bypass；
- 验证仓库计划支持并启用 auto-merge；
- 建立独立 `sage-loop` 飞书项目和用户 allowlist。

### Phase 1：Controller 与只读运行

- 实现 SQLite state、lease、scan cursor、去重和日报；
- 实现 worktree、Git、GitHub、Codex 和 Feishu 适配器；
- 实现 Policy、Prompt、diff gate 和 fake boundaries；
- 在临时 Git 仓库完成单元与集成测试；
- 运行 24 小时 dry-run，只允许 `NO_OP`、`REPORT` 和本地候选，不写代码、不 push。

### Phase 2：人工 PR canary

- 选择一个人为准备的确定性小 bug fixture；
- 完成 Worker、验证、Reviewer 和 Draft PR 全链路；
- 用户检查 diff、证据、飞书消息、清理与根目录状态；
- 人工合并，验证 post-merge fast-forward 和 smoke。

### Phase 3：自动合并 canary

- 使用符合 Tier A 的独立 canary；
- 验证精确 head、required checks 和 squash auto-merge；
- 验证合并后根目录同步、分支删除、日报和恢复状态；
- canary 任何异常都回退到 Tier B，不继续扩大自动化。

### Phase 4：24 小时运行

- 通过飞书 `/loop enable` 开启 `17 * * * *`；
- 首周保持 Tier A 的严格 80 行预算；
- 每日查看一屏摘要，每周审查误报、漏报、成本和回滚；
- 只有独立 Policy PR 获得人工批准后才调整频率或自治范围。

## 18. 验收矩阵

| 场景 | 预期结果 |
|---|---|
| 无可靠问题 | `NO_OP`，无 PR、无即时飞书消息 |
| 同一问题重复发现 | 复用 fingerprint，不重复 PR/报告 |
| Tier A 小 bug | 完整门禁通过后 auto-merge |
| Tier B 小 bug | Draft PR，等待用户看 diff |
| Tier C 大问题 | finding + 飞书短通知，不修改代码 |
| 根目录出现未提交文件 | 暂停写入和自动合并，不动用户文件 |
| base 在审核后前进 | 重新验证，不复用旧 Reviewer 结果 |
| Reviewer 拒绝 | 不 push 或关闭未发布候选 |
| CI 失败 | PR 不合并，记录失败终态 |
| Controller 中途崩溃 | 新进程按 Git/GitHub 事实恢复 |
| 同时触发两次 | 只有一个 fencing token 可以推进 |
| 飞书重复发送命令 | 幂等，不重复创建 cron/run/日报 |
| 自动合并后 | 根目录仅 `ff-only` 同步并保持干净 |
| 连续三次基础设施故障 | 自动暂停并只告警一次 |

## 19. 测试要求

实现至少覆盖：

- risk classifier 与 Tier A/B/C 边界表；
- protected paths、diff budget、binary/mode/symlink 检查；
- candidate fingerprint、module cursor 和 cooldown；
- lease、fencing、timeout、重复触发和崩溃恢复；
- 临时 Git 仓库中的 worktree、branch、cleanup 和 ff-only；
- fake Codex、fake GitHub、fake Feishu 的确定性集成测试；
- head SHA 变化、required checks 失败和 auto-merge 禁止；
- 旧进程丢失 lease 后无法 push、建 PR、请求 auto-merge 或同步根目录；
- PR/finding/日报幂等；
- secret redaction 与 prompt injection fixtures；
- 飞书命令的 sender/chat/参数校验与自由文本拒绝；
- 根目录脏状态不触发任何破坏性命令。

真实 Codex、GitHub 和飞书只用于显式 canary，不作为普通单元测试依赖。

## 20. 后续提高自治等级

未来可以增加更激进的扫描或修复，但必须同时满足：

1. 基于历史数据证明当前 Tier A 误合并率、回滚率和漏报可接受；
2. 新类别有确定性复现、测试 seam 和回滚路径；
3. 更新 `POLICY.md`、Prompt、测试矩阵和 policy version；
4. 通过普通人工 PR 审查；
5. 先 dry-run，再人工 canary，再自动 canary；
6. Loop 本身不能创建或自动合并该策略 PR。

首版不预留“模型自行学习后扩大权限”的入口。自治进化是人工治理的策略升级，不是
Agent 自我授权。

## 21. 交付边界

本设计确认了 Loop Engineer 的产品与工程边界，但以下能力仍未交付：

- Controller、SQLite schema、Codex/Feishu/GitHub adapters；
- `docs/loop-harness/` 正式 SOP 与 Prompt；
- GitHub Actions、branch protection 和 auto-merge；
- `gh` 安装与认证；
- `sage-loop` 项目、daemon 与 cron；
- 只读、人工 PR 和自动合并 canary。

下一步应先编写分阶段实施计划，再按 Phase 0 到 Phase 4 逐层交付。任何阶段失败都
停留在更低自治等级，不把设计目标描述为已上线能力。
