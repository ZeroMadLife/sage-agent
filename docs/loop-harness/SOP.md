# Loop 每小时 SOP

1. 获取单实例 lease 和新的 fencing token。
2. 检查启用状态、磁盘、Git、Codex、受控 manifest、根目录分支与清洁度。
3. `git fetch --prune origin`，确认 `origin/dev/sage-v7` 的精确 SHA。
4. 创建 detached 临时 worktree，不复制依赖或运行数据。
5. 用 `codex exec --sandbox read-only --ephemeral --output-schema` 启动全新 Worker。
6. 只接受结构化 `NO_OP/REPORT/BLOCKED`；Phase 1 的 `FIX` 建议降级为本地候选报告。
7. 保存终态和证据摘要，清理临时 worktree，再释放 lease。
8. `NO_OP` 不通知；新 finding、首次阻断和连续三次故障暂停才输出短通知。

单次运行上限 40 分钟，lease 为 90 分钟。任何外部副作用前后都重新验证 fencing
token。根目录脏、分支错误、manifest 漂移、结果不可解析或清理失败都不得继续。
