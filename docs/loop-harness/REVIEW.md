# Loop Review

当前 Phase 1 和 Phase 2 `SHADOW_WRITE` 都不创建 PR。进入后续 canary 时按以下顺序审查：

1. 先确认失败复现与预期行为独立于修复实现。
2. 检查完整 diff、文件类型、模式、符号链接、secret 和保护路径。
3. 检查修改文件数与增删行数，Controller 独立计算 Tier。
4. 检查聚焦测试、后端门禁、前端测试/构建和 `git diff --check`。
5. 检查 Reviewer 结论绑定精确 head SHA。

Tier A 只能由 GitHub 在 required checks 通过后 squash auto-merge；Tier B 保持 Draft，
用户查看 diff 后决定；Tier C 只进入 finding。用户向 Loop 分支追加 commit 后，原复审
立即失效并转人工管理。

## PR 中文模板

- 标题：保留 `fix(loop):` 等前缀，冒号后用中文概括问题和修复。
- 正文：固定使用“问题证据、修改内容、验证结果、风险与回滚”四个中文小节。
- 审查：Reviewer 的结论、拒绝原因和遗留风险使用中文，代码标识符和命令保留英文。
- 发布前：Controller 检查标题与正文已中文化；不通过时不得创建或更新 PR。
