---
name: commit
description: 准备 commit 消息和改动摘要
allowed-tools: read_file, search, run_shell
---
请准备本次提交：

1. 用 run_shell 跑 `git status` 和 `git diff --staged`
2. 总结改动内容
3. 建议 commit message（中文，遵循 conventional commits）
4. 不要实际执行 git commit，只给建议
