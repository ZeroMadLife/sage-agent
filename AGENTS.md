# Sage 开发协作约定

## 图像生成平台

- 默认使用 GPT Image CLI，通过 `gpt-image-2` 和 `https://api.honglin.asia/v1` 生成图片。运行时从 macOS Keychain 服务 `codex-image-api-honglin` 读取凭据，只向单次进程注入 `OPENAI_API_KEY`。
- 默认平台不可用时，可以使用 `https://image.mentalout.top` 网页工作台。凭据从 macOS Keychain 服务 `codex-image-api-mentalout` 读取，只填写到该网站的 API Key 输入框，完成后清空输入并关闭临时标签页。
- mentalout 当前使用网页任务协议，不能把它当作兼容 `/v1/images/generations` 的 OpenAI Images API。
- 不得把原始凭据写入仓库、文档、日志、终端输出或聊天回复。

## Git 与 Worktree 收口

每次功能提交、修复提交或阶段结束后，必须完成一次收口回顾：

1. 列出需求完成度、未完成边界和验证证据；不把设计或计划当作已交付功能。
2. 在对应 worktree 执行与改动匹配的测试、生产构建和 `git diff --check`。
3. 审查目标分支与集成分支的共同祖先、共享 API/store 影响和未提交文件，给出“可合并 / 继续开发 / 需修复”的明确结论。
4. 合并到集成分支后，在集成 worktree 重跑受影响验证。只有短期分支已被集成分支包含且 worktree 干净，才删除该 worktree 和本地短期分支。
5. 每次完成合并或关闭阶段，都更新 Obsidian `sage-learning`：source commit、测试证据、关闭风险、遗留问题和下一阶段边界。

分支策略：`main` 仅保留可发布版本；`dev/sage-v6` 是当前集成开发分支；`codex/*` 是短期隔离分支。未经完整门禁，不直接将功能 worktree 合入 `main`。

## 文档语言

面向团队的设计书、计划、复盘和 Obsidian 学习材料使用中文。代码标识符、API 字段和命令保留英文，以匹配运行时契约。
