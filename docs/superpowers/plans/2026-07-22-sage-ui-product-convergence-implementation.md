# Sage 前端产品收束实施计划

> 日期：2026-07-22
>
> 集成分支：`dev/sage-v7`
>
> 对应规格：`docs/superpowers/specs/2026-07-22-sage-ui-product-convergence-design.md`
>
> 边界：只修改 `frontend/**` 与前端文档；共享 Harness、Knowledge、API、DB 和 `packages/sage_harness/**` 只读

## 1. 目标与交付策略

本计划把已确认的 A `Soft Precision`、A1 `Dialogue + Facts`、K2 `Immersive Graph Canvas`、S1 分组设置和 S3 命令面板拆成五个可独立验证的纵向切片。

每个切片必须形成一个用户可观察的完整行为，并在独立 `codex/*` 分支通过 PR 合入 `dev/sage-v7`。不把视觉 token、主对话重构、图谱布局和设置迁移揉成单个超大 PR。

## 2. 固定约束

1. 继续复用唯一的 `useHarnessSession`、`useCodingStore`、WebSocket client 和 timeline 投影。
2. Knowledge 不创建 Chat Dock 或第二聊天 runtime；节点动作只生成主对话待提交 context。
3. 继续使用 Sigma、Graphology 和 ForceAtlas2 Worker。
4. 前端没有后端契约时隐藏入口或显示“尚未开放”，不长期使用假数据。
5. 所有状态和动画来自 timeline、job 或用户交互事实。
6. 旧路由先保留兼容，不在没有引用证明和测试时删除。
7. 每个 PR 都执行前端测试、生产构建、`git diff --check` 和三视口浏览器验收。

## 3. Slice 1：视觉底座、导航与命令面板

### 用户可观察行为

- 新用户默认进入浅色 `Soft Precision` 主题，仍可在设置中选择深色或跟随系统；
- 主导航只把“主对话”和“Knowledge”作为私人核心入口，公开门面使用外部入口语义，设置固定在底部；
- `/assistant` 与 `/coding/**` 在导航中统一高亮为“主对话”；
- `⌘K / Ctrl+K` 在私人应用与设置中打开命令面板；
- 命令面板可搜索并跳转主对话、Knowledge、公开门面和设置分组；
- “新建对话”“导入知识来源”只导航到对应意图，不直接创建 session/job 或绕过确认；
- 390px 使用底部三项导航，公开门面和次级设置通过命令面板进入。

### 新增

- `frontend/src/components/product-shell/CommandPalette.vue`
- `frontend/src/components/product-shell/CommandPalette.test.ts`
- `frontend/src/components/product-shell/useCommandPalette.ts`
- `frontend/src/components/product-shell/useCommandPalette.test.ts`
- `frontend/src/components/product-shell/index.ts`

### 修改

- `frontend/src/style.css`
- `frontend/src/App.vue`
- `frontend/src/App.test.ts`
- `frontend/src/components/assistant/AssistantNavigation.vue`
- `frontend/src/components/assistant/AssistantNavigation.test.ts`
- `frontend/src/composables/useWorkbenchPreferences.ts`
- `frontend/src/composables/useWorkbenchPreferences.test.ts`

### 实施步骤

1. 先写失败测试，锁定默认浅色、主导航入口、`/coding/**` 归属于主对话、移动底栏、快捷键打开、搜索、Enter 跳转、Escape 关闭与焦点恢复。
2. 将 `docs/product/style.md` 中已确认 token 映射到现有 CSS token；保留旧 token alias，避免一次修改所有组件。
3. 将首次主题默认值从 dark 改为 light；已有本地偏好保持不变。
4. 收束 `AssistantNavigation`，删除 Today/Growth 的主导航展示，不删除对应路由。
5. 新建模块级 `useCommandPalette`，只管理 open/close 与触发焦点，不引入 Pinia 或新的全局 store。
6. `CommandPalette` 维护固定只读命令表，使用 Vue Router 导航；side-effect 命令只能进入带 intent 的页面。
7. 在 `App.vue` 私人应用根部挂载命令面板；`/public` 独立门面不挂载。
8. 桌面保留安静左栏，移动改为固定底部导航并为页面预留安全区。

### 定向验证

```bash
cd frontend
npm run test -- --run src/composables/useWorkbenchPreferences.test.ts
npm run test -- --run src/components/assistant/AssistantNavigation.test.ts
npm run test -- --run src/components/product-shell/CommandPalette.test.ts
npm run test -- --run src/App.test.ts
npm run build
```

### 浏览器验收

- `1440x900`、`1728x1000`：导航层级、主题、命令面板和设置跳转；
- `390x844`：底部导航不遮挡 composer/graph，命令面板全宽且焦点可恢复；
- light/dark/reduced-motion；
- 页面级无横向滚动、控制台错误和重复 keydown listener。

## 4. Slice 2：A1 主对话与 Facts Rail

### 用户可观察行为

- `/assistant` 成为主对话空态，现有 session 仍通过 `/coding/session/:id` 恢复；
- 对话区保留唯一消息流和 composer；
- 右侧 Facts Rail 按 Approval/Failure/Recovery、Goal、Run、Evidence、Context 排序，并可收起；
- 完整工具过程默认折叠为事实摘要，展开后仍复用现有 timeline 组件；
- Knowledge context 在发送前显示“待提交”，后端 receipt 后才显示“已冻结”；
- 移动端 Facts 进入 bottom sheet，不压缩消息正文。

### 新增

- `frontend/src/components/conversation/GoalHeader.vue`
- `frontend/src/components/conversation/FactsRail.vue`
- `frontend/src/components/conversation/ContextReceiptChip.vue`
- 对应 Vitest 文件

### 修改

- `frontend/src/views/AssistantHomeView.vue`
- `frontend/src/views/CodingView.vue`
- `frontend/src/components/harness/ChatHarnessLayout.vue`
- `frontend/src/components/harness/chat/**`（仅展示密度和摘要）
- `frontend/src/harness/timelineProjection.ts`（仅缺少前端投影时）
- 对应现有测试

### 实施步骤

1. 使用现有 timeline fixture 为七种 run 状态和 Facts 优先级写失败测试。
2. 将 Goal、Run、Proposal、Evidence 映射成只读 view model；组件不直接请求 API。
3. `ChatHarnessLayout` 增加 A1 模式，不破坏现有 Dock/Inspector 的兼容能力。
4. Assistant 空态收缩为 Purpose/Goal 摘要、三个下一步和 composer。
5. Coding session 页面改用“主对话”语义，但不重命名共享 runtime/store。
6. 实现 1440/1728 Facts Rail 和 390 bottom sheet。

### Contract Gate

若 Goal evaluation、Mastery Evidence 聚合或 frozen receipt 未提供稳定 DTO，只展示现有真实字段并记录 contract gap，不在前端推算完成度。

## 5. Slice 3：K2 Knowledge 图谱与研究交接

### 用户可观察行为

- Knowledge 默认没有 Chat Dock，画布获得主要空间；
- 全局态存在细边和稳定社区，hover 增强一跳邻域，selected 使用不会遮盖节点的双层环；
- Inspector 只显示节点、来源、revision 和动作；
- “在主对话深入研究”把选中节点作为待提交 context 带回主对话；
- 200/1k/5k fixture 按预算逐级降级，异常时显示可搜索节点列表。

### 新增

- `frontend/src/components/knowledge/KnowledgeWorkspaceHeader.vue`
- `frontend/src/components/knowledge/KnowledgeSourceRail.vue`
- `frontend/src/components/knowledge/KnowledgeGraphToolbar.vue`
- `frontend/src/components/knowledge/KnowledgeNodeInspector.vue`
- `frontend/src/components/knowledge/KnowledgeGraphFallbackList.vue`
- 对应测试与固定 fixture

### 修改

- `frontend/src/views/KnowledgeView.vue`
- `frontend/src/components/knowledge/KnowledgeGraphCanvas.vue`
- `frontend/src/components/knowledge/knowledgeGraphPresentation.ts`
- `frontend/src/harness/surfaceContext.ts`
- `frontend/src/harness/knowledgeNodeResearch.ts`
- 对应现有测试

### 实施步骤

1. 先固定 200/1k/5k graph fixture、选中深色节点截图点和非空像素基线。
2. 将 Toolbar、Inspector、来源与导入 UI 移出 Canvas；Canvas 只保留 renderer 生命周期。
3. 统一全局/hover/selected/path/gap 的 nodeReducer、edgeReducer、zIndex 和 label 策略。
4. ForceAtlas2 初始收敛后停止；拖拽只做有上限的局部重热。
5. 删除 Knowledge 页常驻 Chat Dock 展示，保留 Harness context serializer。
6. Deep Research 通过现有 draft bridge 或明确的 route state 进入主对话；刷新后不伪造已冻结状态。
7. 完成可访问列表入口和移动 bottom sheet。

### 性能门禁

- 200 节点首次可交互目标 `<=1.2s`，交互 55fps；
- 1k 节点目标 `<=2.5s`，交互 45fps；
- 5k 节点 `<=3s` 提供社区或列表入口，不要求全边渲染；
- hover/selected 不重新执行全图布局。

## 6. Slice 4：来源导入、设置 S1 与 Memory 收束

### 用户可观察行为

- Knowledge 空态只提供 Obsidian、GitHub、Markdown 三类导入；
- 导入流程展示真实扫描/解析/Wiki/建图阶段或诚实的处理中状态；
- 设置按个人与体验、模型与能力、数据与记忆、运行与开发分组；
- 待批准 Memory Proposal 在主对话优先提示，已保存 Memory 在设置管理；
- 未开放的 Purpose/Soul、Memory 删除/导出或 job 字段显示只读边界。

### 新增

- `frontend/src/components/knowledge/KnowledgeImportFlow.vue`
- `frontend/src/components/settings/SettingsGroupNav.vue`
- `frontend/src/components/settings/MemorySettings.vue`
- 对应测试

### 修改

- `frontend/src/views/KnowledgeView.vue`
- `frontend/src/views/SettingsView.vue`
- `frontend/src/router/index.ts`
- `frontend/src/components/conversation/FactsRail.vue`
- 对应 store 调用和测试，不新增后端接口

### Contract Gate

Knowledge ingest job、Purpose/Soul 和已保存 Memory 管理缺口必须先与 Harness/Knowledge 会话同步。前端不修改 `api/**` 或共享 schema。

## 7. Slice 5：旧入口迁移、公开门面与全量回归

### 用户可观察行为

- Growth 从导航移除，旧地址根据已确认迁移语义安全重定向；
- Coding/Publishing 保留深链但不再是主导航入口；
- 公开门面保持独立 build、公开 corpus 和 CSP，不继承私人命令面板或数据；
- 三尺寸、主题、键盘、reduced motion、图谱降级和错误状态全部通过浏览器验收。

### 修改

- `frontend/src/router/index.ts`
- `frontend/src/views/EvolutionView.vue` 或重定向策略
- `frontend/src/views/PublicProfileView.vue`（只做风格对齐，不改公开权限）
- `frontend/src/router/public.ts`
- README 截图和页面说明（实现完成后）

### 删除门禁

不直接删除旧 View/组件。只有满足以下全部条件才单独提交删除：

1. `rg` 确认生产和测试无引用；
2. 替代路由和数据访问有测试；
3. 旧深链有重定向/兼容策略；
4. 全量测试、构建和浏览器回归通过。

## 8. 每个 Slice 的完整门禁

```bash
cd frontend
npm run test -- --run
npm run build
npm run build:public
cd ..
git diff --check
```

根据改动补充：

- Playwright/真实浏览器 `1440x900`、`1728x1000`、`390x844` 截图；
- console error、页面溢出、焦点顺序和 Escape/focus restore；
- Graph Canvas 非空像素、selected 节点中心色、hover 邻域边；
- 受影响 API/store contract 测试；
- diff 对 `core/**`、`api/**`、`db/**`、`packages/sage_harness/**` 的越界扫描。

## 9. Git 与收口

1. 规格 PR 合入 `dev/sage-v7` 后，为每个 Slice 从最新集成分支创建独立 worktree 和 `codex/*` 分支。
2. 每个 Slice 以失败测试开始，小提交保持单一职责。
3. PR 标题、正文、验证摘要使用中文；CI 全绿后再合并。
4. 合入后在根目录集成分支执行受影响测试和构建，不在根目录直接开发。
5. 更新 Obsidian `sage-learning`，记录 source commit、截图、测试证据、关闭风险和下一 Slice 边界。
6. 所有 Slice 完成前不合入 `main`，不宣称整套 UI 已交付。
