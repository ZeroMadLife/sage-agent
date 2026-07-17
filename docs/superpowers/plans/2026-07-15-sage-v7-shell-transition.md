# Sage V7-P1.1 共享 Shell 与 Composer 形变实施计划

> 目标分支：`codex/feat-v7-shell-transition`
> 集成分支：`dev/sage-v7`
> 对应规格：`docs/superpowers/specs/2026-07-15-sage-v7-shell-transition-design.md`

## 交付切片 1：锁定现有契约

修改：

- `frontend/src/views/AssistantHomeView.test.ts`
- `frontend/src/router/index.test.ts`
- 新增 `frontend/src/composables/useViewTransition.test.ts`

行为：

- session 创建成功后只导航一次；
- session 创建失败时不导航并保留草稿；
- View Transition API 缺失或减少动态效果时仍能完成导航；
- 现有 pending initial prompt 只在 WebSocket open 后发送一次。

验证：相关 Vitest 先失败，再由后续切片修复。

## 交付切片 2：语义字号 Token

修改：

- `frontend/src/style.css`
- `frontend/src/components/assistant/AssistantNavigation.vue`
- `frontend/src/views/AssistantHomeView.vue`
- `frontend/src/components/assistant/AssistantHomeSummary.vue`
- `frontend/src/components/coding/sidebar/CodingSidebar.vue`
- `frontend/src/components/coding/composer/CodingComposer.vue`
- `frontend/src/components/coding/chat/CodingMessageTurn.vue`
- `frontend/src/components/coding/chat/CodingRunTrace.vue`

行为：

- 新增 `xs/sm/md/body/lg/title` 字号 Token；
- 首页、导航、Chat 和 Composer 不再使用 `9px`/`10px` 可见文字；
- 中文正文和输入区提高到 `15px`，元信息最低 `12px`；
- 保持 390px 宽度下不溢出。

验证：组件测试、生产构建、三视口截图。

## 交付切片 3：应用级共享 Shell

修改：

- `frontend/src/App.vue`
- `frontend/src/router/index.ts`
- `frontend/src/components/assistant/AssistantNavigation.vue`
- `frontend/src/views/AssistantHomeView.vue`
- `frontend/src/views/KnowledgeView.vue`
- `frontend/src/views/EvolutionView.vue`
- `frontend/src/views/PublicProfileView.vue`
- `frontend/src/views/CodingView.vue`

行为：

- assistant/coding/knowledge/evolution/public 共用一个应用级 `AssistantNavigation`；
- settings 保持独立布局；
- Chat 不再显示第二个常驻会话侧栏；
- 会话列表从 Chat 标题栏以 Drawer 打开；
- 桌面和移动端均保留 Escape、焦点圈定和焦点返回。

验证：路由测试、导航组件测试、CodingView 测试和深链恢复测试。

## 交付切片 4：Composer Shared Element

新增或修改：

- 新增 `frontend/src/composables/useViewTransition.ts`
- `frontend/src/views/AssistantHomeView.vue`
- `frontend/src/components/coding/composer/CodingComposer.vue`
- `frontend/src/style.css`

行为：

- 封装 feature detection、减少动态效果和路由 DOM 更新等待；
- 两个 Composer 使用唯一的 `sage-composer` shared-element name；
- 页面时间线使用轻量进入动画；
- 失败与无 API 环境直接降级，不影响功能。

验证：composable 单测、首页交互测试、浏览器真实形变。

## 交付切片 5：完整门禁与收口

执行：

```bash
cd frontend && npm test -- --run
cd frontend && npm run build
pytest -q
ruff check .
mypy core api
git diff --check
```

浏览器验证 `1440x900`、`1024x768`、`390x844`，覆盖浅色、深色、减少动态效果、创建失败和运行中 session。

收口：

1. 写入 Obsidian `45-V7-P1.1共享Shell与对话形变源码复盘.md`；
2. 记录 source commit、测试证据、关闭风险和 V7-P2 边界；
3. 给出“可合并 / 继续开发 / 需修复”结论；
4. 合入 `dev/sage-v7` 后重跑受影响测试和构建；
5. 确认祖先关系与 worktree 干净后删除短期分支和 worktree。
