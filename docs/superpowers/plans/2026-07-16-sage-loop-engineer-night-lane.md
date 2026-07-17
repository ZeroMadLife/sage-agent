# Sage Loop Engineer Phase 3：夜间深度开发 Lane 实施计划

> 日期：2026-07-16
>
> 状态：设计已确认，待 Phase 2 验收后执行
>
> 设计依据：`docs/superpowers/specs/2026-07-16-sage-loop-engineer-night-lane-design.md`

## 目标

在 Phase 2 Fast Lane 稳定后，增加动态目标分支、夜间活跃开发避让、统一候选评分、双夜
门禁和受控深度 PR。全过程保持默认关闭；先 shadow，再 Draft PR canary，最后只对纯视觉
N-A 开启独立自动合并 canary。

## 前置与实施原则

- Phase 1、Phase 2 必须分别完成审查、验证和 canary，Phase 3 不塞回现有控制器 PR；
- 每个小版本保持数据库向后兼容，并能回退到 Phase 2 模式；
- GitHub 变量、Git/PR/merge、飞书发送和 session 触发只能由 Controller 执行；
- Codex/Claude 只处理固定 job envelope，不直接决定权限或用户可见文案；
- fake transport 和确定性时钟先通过，再做真实 cc-connect、GitHub 和调度 smoke；
- 根目录用户修改、主 Codex/Claude 会话和既有 `sage-review` 项目不得受影响。

## 小版本 1：Phase 3 状态与模式

### 修改范围

- 扩展 `core/loop_harness/config.py`、`models.py`、`state.py`、`cli.py`；
- 增加数据库迁移和状态契约测试；
- 更新 Loop Policy、SOP、Review 和 Runbook。

### 实现

- 增加 `SHADOW_NIGHT`、`NIGHT_PR_CANARY`、`NIGHT_VISUAL_AUTO_MERGE`；
- 保存 target branch/SHA、night theme、优先级快照、权限等级、Lane/slot 和 deadline；
- 保存分析批准、绑定 SHA、直接依赖摘要、Claude 结论和失效原因；
- `DRY_RUN`、Phase 2 mode 和 Night mode 相互显式转换，非法跃迁失败关闭；
- 旧 SQLite 可原地迁移，暂停 Night Lane 不影响 Fast Lane。

### 验证

- 旧数据库迁移、重复迁移和回滚读取正常；
- 未启用 Phase 3 时调度、开放 PR 数和权限与 Phase 2 完全一致；
- 连续 3 夜基础设施失败只暂停 Night Lane。

## 小版本 2：TargetResolver

### 修改范围

- 新增 TargetResolver 和 GitHub Repository Variable adapter；
- 调整 `git.py`，不再在 Phase 3 mode 中固定 `dev/sage-v7`；
- 增加 fake GitHub 和远端分支测试。

### 实现

- 每轮读取 `SAGE_LOOP_TARGET_BRANCH` 和 `SAGE_LOOP_NIGHT_THEME`；
- 只接受 `^dev/sage-v[0-9]+(?:\.[0-9]+)*$`，解析精确远端 SHA；
- job、push、审查、merge 前重复校验 branch/SHA；
- 变量或 SHA 变化时，使旧评分、批准、验证和审查原子失效；
- 模型进程不接触 GitHub 凭据，也不能覆盖解析结果。

### 验证

- 缺失、非法、不存在和越权分支均无模型或远程副作用；
- 变量变化不会把旧候选错误应用到新版本；
- GitHub 重试不会产生重复通知。

## 小版本 3：ActivityGuard、夜间调度与硬截止

### 修改范围

- 新增 ActivityGuard、可注入时钟和 night scheduler；
- 扩展 worktree/lease、cc-connect 活跃状态和进程元数据 adapter；
- 增加时区、时间窗口和并发测试。

### 实现

- `01:00` 开始，活跃时每 30 分钟重试，`03:00` 仍活跃则跳过；
- `06:00` 停止新副作用并清理未提交 Loop worktree；
- 只读取 session 状态、时间、路径和 lease，不读取聊天正文；
- 根目录 dirty 改为 exact path、测试配对和直接依赖避让；
- Night lease 存在时 Fast Lane 返回 `BUSY_NIGHT_LANE`；
- 人工开发运行中触碰候选路径时终止 Worker，不 commit/push。

### 验证

- DST/Asia/Shanghai 边界、01:00/03:00/06:00 和跨日额度可确定性复现；
- 非重叠 dirty path 可继续，重叠路径无写入；
- deadline 到达后不存在新 PR、评论、merge 或未提交 worktree。

## 小版本 4：PriorityEngine 与权限分类

### 修改范围

- 新增候选评分、硬门禁和 N-A 至 N-E diff policy；
- 扩展 candidate schema、队列查询和解释输出；
- 增加评分表驱动测试和路径/AST 检查。

### 实现

- 按设计保存每个正向分和风险扣分，低于 60 只入报告/队列；
- `SAGE_LOOP_NIGHT_THEME` 只影响路线权重，不绕过门禁；
- 安全、数据、鉴权、迁移、生产配置和部署直接归 N-E；
- N-A 检查 Vue `<script>`、事件、状态、API、router、props/emits 和数据映射；
- Controller 根据真实 diff 重新分类，忽略 Worker 自报等级；
- 每晚只选一个可在窗口内独立验证的主题。

### 验证

- 高分不能覆盖硬门禁；
- 行为改动不会被误判为 N-A；
- 同分排序和评分解释稳定，不依赖模型措辞。

## 小版本 5：双夜状态机与深度 Worker

### 修改范围

- 扩展 runner/orchestrator、Codex 分析/Fixer schema 和 worktree 管理；
- 新增第一夜分析记录和第二夜实现 envelope；
- 增加状态机、漂移和超时测试。

### 实现

- N-C/N-D 第一夜只产出依赖、复现、测试、预算、契约影响和回滚；
- Claude `APPROVE` 后保存绑定 target SHA、路径和依赖摘要；
- 第二夜开始前全部重算，任一漂移返回第一夜；
- N-D 每次只允许一个纵向切片和设计规定的 400 行预算；
- 实现后创建新的 Claude diff 审查，不能复用分析批准；
- N-A/N-B 可同夜执行，但 N-B 永远保持 Draft。

### 验证

- N-C/N-D 无法同夜分析后直接实现；
- 旧批准、旧测试和旧 Claude 结论不能跨 SHA 使用；
- 06:00 截止和 lease 丢失时无远程副作用。

## 小版本 6：Codex/Claude 返回与状态文案

### 修改范围

- 更新 `docs/loop-harness/PROMPT.md` 和模型输出 schema；
- 新增确定性 message renderer、事件码和去重键；
- 扩展 cc-connect reviewer/notifier fake 测试。

### 实现

- Codex 只返回发现、修改、证据、验证建议和风险；
- Claude 只返回 verdict、findings、验证评价和 merge 建议；
- Controller 渲染“已触发、已交审、已暂停、已恢复、已失效、已回滚”；
- 只有 lease 成功且 session 被网关接受才记为“已触发”；
- “恢复”只用于 Lane/服务健康恢复；Worker 使用“重试/继续执行”，SHA 漂移使用“失效”；
- 每条手机消息默认不超过 12 行，正常定时启动与内部交接默认静默；
- 返回文案不包含 Prompt、思考过程、凭据、工具日志和未经门禁确认的完成声明。

### 验证

- renderer 使用固定输入做逐字契约测试；
- Codex/Claude 超时、拒绝、不可解析和重复回调不会出现错误状态词或重复消息；
- 每条恢复消息明确是否复用候选、测试、审查和 worktree，默认不复用；
- 中文、行数、链接、路径和 SHA 在手机卡片中完整可读。

## 小版本 7：PR 槽位、验证与通知闭环

### 修改范围

- 扩展 GitHub adapter、validation/artifact manager、notifier 和 digest；
- 增加 `fast_slot`/`deep_slot`、截图和幂等副作用测试。

### 实现

- Fast 最多 1 个、Deep 最多 1 个，总计最多 2 个开放 Loop PR；
- 两个槽位禁止路径、测试、直接依赖或共享契约重叠；
- deep PR 超过 3 天只提醒，不再创建第二个；
- N-A/N-B 执行前端测试、build 和桌面/手机截图；
- N-C/N-D 执行定向测试、受影响模块全量门禁和契约检查；
- 飞书最终卡片固定展示范围、原因、行为、数据影响、验证和结果；
- N-B/N-C/N-D 永远 Draft；N-E 只创建去重报告。

### 验证

- 重试不重复创建 PR、Issue、评论、auto-merge 或飞书消息；
- slot 占用和路径重叠时正确排队；
- 测试、build、截图或 Claude 失败时不会自动合并。

## 小版本 8：Shadow、PR Canary 与视觉自动合并

### 实施顺序

1. 在 `SHADOW_NIGHT` 连续运行至少 3 夜，不 push；
2. 人工检查选择质量、ActivityGuard、截止行为、文案和磁盘增长；
3. 启用 `NIGHT_PR_CANARY`，所有等级仍由用户人工合并；
4. 累积至少 7 天、5 个 N-A PR，统计误判、回滚和人工否决；
5. 只有 N-A 零行为误判、零安全门禁事故且 required checks 稳定时，启用
   `NIGHT_VISUAL_AUTO_MERGE`；
6. N-B/N-C/N-D 长期保持 Draft，N-E 长期只报告。

### Canary 门禁

- 0 次根目录、主会话、保护路径或共享契约污染；
- 0 次错误目标分支、过期 SHA、重复副作用或截止后副作用；
- 0 次 N-A 行为改动误判和自动合并后回滚；
- Claude 审查、人工抽查、测试、build 与截图证据完整；
- 飞书消息短、中文且状态词无歧义；
- 状态目录持续低于 1 GiB，日志保持约 35 MB 上限。

## 每个小版本的验证门禁

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

涉及 GitHub、cc-connect、launchd 或飞书时，先通过 fake transport，再做一次受控真实 smoke，
核对凭据不进入模型、日志、SQLite、进程参数或测试快照。

## 提交、PR 与回滚

- Phase 3 使用独立 `codex/*` 分支和 Draft PR，不混入 Phase 1/2 实现 PR；
- 每 1 至 2 个相邻小版本形成一个可独立验证的 commit；
- 每次收口更新 PR 实际完成度和 Obsidian `sage-learning`，不把计划写成已交付；
- 权限回退顺序为：

```text
NIGHT_VISUAL_AUTO_MERGE -> NIGHT_PR_CANARY -> SHADOW_NIGHT -> Phase 2 mode -> DRY_RUN -> PAUSED
```

暂停不删除 SQLite、日志、PR、Issue 或审查证据。worktree 和 artifact 只能由 Controller 的
受控 cleanup 清理；凭据撤销通过 GitHub/Keychain 完成。
