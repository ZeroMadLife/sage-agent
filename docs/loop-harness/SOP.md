# Loop 每小时 SOP

1. 获取单实例 lease 和新的 fencing token。
2. 检查启用状态、磁盘、Git、Codex、受控 manifest、根目录分支与清洁度。
3. `git fetch --prune origin`，确认 `origin/dev/sage-v7` 的精确 SHA。
4. 创建 detached 临时 worktree，不复制依赖或运行数据。
5. 用 `codex exec --sandbox read-only --ephemeral --output-schema` 启动全新 Worker。
6. `DRY_RUN` 只接受结构化 `NO_OP/REPORT/BLOCKED`；写模式先接受
   `FRONTEND_CANDIDATE`，再由 Controller 检查 dirty path、真实 diff 和预算。
7. Fixer 只能在候选专属 worktree 编辑；`SHADOW_WRITE` 验证后只记录私有证据。
8. `PR_CANARY` 先检查 `gh` 私有仓库权限、远程/本地 PR 槽位和每日额度；再由 Controller
   禁用 hooks/签名后 commit、非 force push，并创建中文 Draft PR。
9. Controller 通过 synthetic relay session 触发 `sage-loop-review` Claude，只允许读取本轮
   patch 与 validation；审查结论绑定 exact head，PASS 后仍保持 Draft。
10. 保存终态和证据摘要，清理临时 worktree，再释放 lease。
11. `NO_OP` 静默；新 finding、shadow 验证、Draft PR 审查和首次阻断才输出短通知。

单次运行上限 40 分钟，lease 为 90 分钟。任何外部副作用前后都重新验证 fencing
token。根目录脏、分支错误、manifest 漂移、结果不可解析或清理失败都不得继续。
