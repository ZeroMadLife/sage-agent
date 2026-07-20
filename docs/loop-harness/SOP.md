# Loop 每小时 SOP 2.3

1. 获取单实例 lease 和新的 fencing token。
2. 检查启用状态、磁盘、Git、Codex、受控 manifest、根目录分支与清洁度。
3. `git fetch --prune origin`，确认 `origin/dev/sage-v7` 的精确 SHA。
4. 创建 detached 临时 worktree，不复制依赖或运行数据。
5. 用 `codex exec --sandbox read-only --ephemeral --output-schema` 启动全新 Worker。
6. `DRY_RUN` 只接受结构化 `NO_OP/REPORT/BLOCKED`；写模式先接受
   `FRONTEND_CANDIDATE`，再由 Controller 检查 dirty path、真实 diff 和预算。
7. Fixer 只能在候选专属 worktree 编辑；`SHADOW_WRITE` 验证后只记录私有证据。
8. Worker 输出无法解析或不符合严格 schema 时，只用全新的临时输出文件和更明确的 JSON 提示
   重试一次；第二次仍失败必须记录 `BLOCKED_WORKER_OUTPUT`，不得宽松解析或继续执行。
9. `PR_CANARY` 先检查 `gh` 私有仓库权限、远程/本地 PR 槽位和每日额度；再由 Controller
   禁用 hooks/签名后 commit、非 force push，并创建中文 Draft PR。
10. Controller 通过 synthetic relay session 触发 `sage-loop-review` Claude，只允许读取本轮
   patch 与 validation；审查结论绑定 exact head。
11. `PR_CANARY` 一律保持 Draft。`AUTO_MERGE_TIER_A` 仅对 Tier A 将 PR 转 Ready，等待全部
    GitHub checks 成功，再次核对 exact head/base、人工 review 和阻止标签后 squash 合并并删除
    Loop 远程分支；Tier B 保持 Draft，Tier C 只报告。
12. 保存终态和证据摘要，清理临时 worktree，再释放 lease。
13. `NO_OP` 静默；新 finding、shadow 验证、Draft PR 审查、Tier A 自动合并和首次阻断才输出
    短通知。

单次运行上限 40 分钟，lease 为 90 分钟。任何外部副作用前后都重新验证 fencing
token。根目录脏、分支错误、manifest 漂移、结果不可解析或清理失败都不得继续。
