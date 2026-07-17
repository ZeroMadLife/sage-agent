# Sage V6.9 前端整改交接计划

日期：2026-07-13。接手对象：GLM 前端开发 Agent。团队文档使用中文。

## 当前事实

当前 `dev/sage-v6@912a3f7` 是开发基线，因此现在启动它时页面基本没有视觉变化是正常的。

- `codex/sage-v6-9-integration@193e38c`：已完成 V6.9 的可靠性、聊天壳和设置中心，但尚未合入 `dev/sage-v6`。
- `codex/v6-9-visual-system@8ea23dc`：已完成 Token、主题、消息流、Composer 与 Diff 视觉实现，尚未合入集成分支。
- 所以不能把当前 `dev/sage-v6` 页面作为 V6.9 前端整改验收结果。

建议新建 GLM 短期分支：`git switch dev/sage-v6`，随后 `git switch -c codex/frontend-v6-9-remediation`。

先审查并选择性移植下述参考提交。禁止复制本地 Hermes Studio 的 BSL 1.1 源码、CSS、资源或品牌，只参考布局比例、密度和信息架构。

## 已完成参考

### 可靠性与 Chat Shell

来源：`codex/sage-v6-9-integration`。关键提交：`7137b9d`、`ec77aa1`、`d519842`、`30f2a24`、`0a2d9c4`、`5151463`。

已实现：

- SessionEventJournal、timeline REST 分页、WebSocket `after=sequence` 补发。
- 服务端 RunCoordinator 持有运行；浏览器断开、切换会话或进入设置不取消运行。
- `/coding` 恢复顺序：最近 session -> 服务端最新未归档 session -> 明确空列表才创建一次。
- `resume` 验证会话，避免陈旧前端缓存污染当前 session。
- 侧栏只发导航意图；页面路由统一执行 Store 切换，避免重复 replay/reconnect。
- 256px 桌面栏；1024px 抽屉；390px 全屏会话 sheet；焦点、Escape、inert 背景隔离。
- Files/Diff 都是临时 Drawer，聊天页没有常驻 Inspector。

绝不能破坏的契约：

`timeline` 使用 `event_id` 去重、`sequence` 补齐；运行归后端所有，WebSocket 只是观察通道；前端按 `session_id` 隔离状态，A 的迟到事件不得写入 B；会话切换必须先经服务端 `resume` 验证。

### 视觉实现参考

来源：`codex/v6-9-visual-system@8ea23dc`。

该提交已经验证：31 个前端测试文件、236 项测试、生产构建、`git diff --check`；Playwright 验证过 1440x900、1024x768、390x844 和深色设置页。

可审查并移植：

- `sage.ui.theme=light|dark|system` 的全局 Token 应用。
- Sage 自有黑白灰高密度工作台；绿色、黄色、红色仅表达语义状态。
- 用户右对齐轻量气泡，助手无卡片文档流，过程块可折叠。
- 紧凑 Composer、中文 tooltip。
- 只读 Diff：unified/并排切换、行号、增删底色、长行横向滚动。

## GLM 前端整改任务

### P0 可见视觉闭环

1. 将 Token 化样式正式接入聊天页、设置页和 Drawer。
2. `light/dark/system` 必须真正应用到全局，刷新后保持。
3. 桌面首屏固定为 256px 会话栏 + 最大 880px 居中聊天区，无常驻第三栏。
4. 用细边框、6/8px 圆角、弱阴影和紧凑控件降低卡片感。
5. Composer 支持自动增高、权限模式、上下文摘要、图标发送/停止和中文 tooltip。
6. 禁止渐变背景、装饰球、营销卡片、单一紫色调。

### P1 Diff、Markdown 与响应式

1. 保持 Diff 只读，不引入 Monaco。
2. 统一/并排 Diff、行号、增删背景、长行横向滚动。
3. Markdown 加代码高亮；本轮不加 Mermaid、KaTeX、虚拟列表。
4. 1440x900：256px 侧栏，聊天居中；1024x768：可关闭侧栏 Drawer；390x844：全屏会话 sheet，设置为“分类列表 -> 详情”两级。
5. 所有 Drawer/Dialog 必须支持 Escape、焦点圈定和关闭后的焦点返回。

### P2 架构清理

1. 将 `SettingsSection`、ThemeMode、设置导航集中到公共前端常量/类型。
2. 清理无调用方的旧 Inspector，但先更新测试和出口。
3. 只修改样式和展示层，不重写 router、Coding Store、timeline 协议。
4. 文案中文；代码标识和 API 字段保持英文。

## 明确禁止

- 不引入 Naive UI 全量迁移。
- 不引入 Monaco、xterm、node-pty 或浏览器终端。
- 不做网页 Skill 安装、MCP 密钥输入、外部 Skill 自动执行。
- 不做多用户、认证、云工作区、GitHub OAuth。
- 不复制 Hermes Studio 源码、CSS、资源、品牌或文案。
- 不改后端 timeline API、RunCoordinator、SessionEventJournal 和事件一致性语义。

## 验收门禁

每个阶段必须执行 `cd frontend`、`npm test -- --run`、`npm run build`、`git diff --check`。

Playwright 核心路径：

1. 恢复最近会话，打开 Files/Diff，Escape 关闭。
2. A 运行中切 B，返回 A 并刷新，过程、审批和终态一致且终态不重复。
3. A 进入设置再返回，先 REST replay 后 WS cursor 重连同一 run。
4. 三种视口、浅色与深色均截图；长中文标题、路径和命令不溢出。
5. 控制台不得有未处理 Promise。

## Git 交接规则

功能分支完成后：需求复审 -> 质量复审 -> 集成 worktree 重跑测试/构建 -> 祖先检查 -> 清理短期分支与 worktree。最终合入目标是 `dev/sage-v6`；V6.9 完整门禁通过前不得进入 `main`。

## 后端协作边界

Codex 后续重点：Harness、journal、run reconnect、Memory/Dream、Skill/Agent 执行约束和真实 benchmark。

GLM 重点：Token、聊天体验、设置、响应式、无障碍、前端测试与截图。

新增 API、timeline event 字段、Store 公共状态、权限语义必须先共同确认。

## 版本边界

- V6.9：聊天主界面、设置中心、timeline/reconnect 私测、浅深主题、只读 Diff。
- V6.9.1：虚拟列表、Mermaid、公式、高级 Markdown、高级 Diff。
- V7：认证、租户隔离、云工作区、GitHub、Docker/Actions、Monaco、沙箱、终端。
- V8：Local Companion、未提交代码同步、Code RAG、AST 知识图谱、增量索引。
