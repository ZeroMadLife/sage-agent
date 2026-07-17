# Sage V6.9 聊天主视图收线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 收线 Sage 的聊天主视图，使主题、长会话滚动、上下文预算和执行过程形成一致、可恢复的工作台体验。

**架构：** 保持 Composer 固定在中心栏底部，`message-area` 继续是唯一的会话滚动容器。新的滚动状态只在 Vue view 内管理，不写入服务端；上下文状态只投影现有 `CodingContextSnapshot`，不改变后端压缩策略或暴露原始模型推理内容。

**技术栈：** Vue 3、TypeScript、Pinia、Vitest、现有 CSS Design Token、FastAPI 已有 context snapshot API。

---

### Task 1: 主题 Token 完整覆盖

**文件：**
- 修改：`frontend/src/views/CodingView.vue`
- 修改：`frontend/src/components/coding/chat/CodingToolActivity.vue`
- 修改：`frontend/src/components/coding/chat/CodingThinkingIndicator.vue`
- 测试：`frontend/src/composables/useWorkbenchPreferences.test.ts`

- [ ] 将聊天页剩余的浅色硬编码背景、文字、边框和工具状态颜色替换为 `--sage-*` token。
- [ ] 将 Spinner 的边框和工具状态颜色改为 success/warning/danger token，保证 `data-theme="dark"` 下没有白底组件。
- [ ] 补充主题根节点和工具过程组件使用 token 的渲染测试。

### Task 2: 长会话滚动跟随与新消息提示

**文件：**
- 修改：`frontend/src/views/CodingView.vue`
- 测试：`frontend/src/views/CodingView.test.ts`

- [ ] 先测试：位于末尾时新增 turn 自动滚动；用户向上浏览时新增 turn 不跳屏并出现“回到底部”按钮；点击按钮恢复末尾和未读数。
- [ ] 使用 `scrollHeight - scrollTop - clientHeight <= 80` 判断跟随状态，使用 `unseenMessageCount` 记录不跟随期间新增 turn。
- [ ] 添加可访问的 `aria-live` 未读提示；保留 timeline `loadOlder` 的现有锚点恢复行为。

### Task 3: 上下文预算状态投影

**文件：**
- 修改：`frontend/src/components/coding/composer/CodingComposer.vue`
- 测试：`frontend/src/components/coding/composer/CodingComposer.test.ts`

- [ ] 先测试 `normal`、`compact`、`high/emergency`、`context_operation_active` 和 compaction error 的可见中文状态。
- [ ] 将单独圆环替换为带 token 数值、进度条和状态标签的预算状态块；保留手动压缩按钮和原有 ARIA progressbar。
- [ ] `high/emergency` 用 warning/danger 状态，`context_operation_active` 显示压缩中，未配置时显示模型未配置。

### Task 4: 执行反馈与工具详情收口

**文件：**
- 修改：`frontend/src/stores/coding.ts`
- 修改：`frontend/src/views/CodingView.vue`
- 修改：`frontend/src/components/coding/chat/CodingThinkingIndicator.vue`
- 修改：`frontend/src/components/coding/chat/CodingToolActivity.vue`
- 测试：`frontend/src/stores/coding.test.ts`
- 测试：`frontend/src/components/coding/chat/CodingToolActivity.test.ts`

- [ ] 发送请求立即设置“准备执行”，运行中不因为工具出现而隐藏阶段反馈。
- [ ] 工具参数提供折叠 JSON；工具结果保存完整内容并按 800 字符预览展开。
- [ ] 明确只展示执行阶段、工具、审批和上下文状态，不新增 reasoning token 或 CoT 字段。

### Task 5: 验证与收口

**文件：**
- 修改：`/Users/zeromadlife/Desktop/Obsidian-Knowledge-Base/03_项目/tourswarm/技术沉淀/sage-learning/36-V6.9聊天主视图收线复盘.md`

- [ ] 运行前端全量测试、生产 build、`git diff --check`。
- [ ] 浏览器验证桌面 `1440×900`、平板 `1024×768`、手机 `390×844` 的浅色/深色、长会话和底部跳转状态。
- [ ] 审查分支祖先和 worktree 清洁度，给出可合并/继续开发结论；被 `dev/sage-v6` 包含后删除短期 worktree 与分支。
