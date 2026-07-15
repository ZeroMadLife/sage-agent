# Sage V7-P1 个人助手首页与知识引导实施计划

> 日期：2026-07-15
> 集成分支：`dev/sage-v7`
> 短期分支：`codex/feat-v7-assistant-home`
> 依赖规格：`docs/superpowers/specs/2026-07-15-sage-v7-personal-assistant-knowledge-evolution-design.md`

## 1. 目标与交付边界

本阶段把 Sage 的默认入口从 Coding 工作台调整为个人助手首页。用户进入后能看到真实的近期对话、当前项目/工作区、待处理 Memory Proposal 与知识能力建设状态，并可以从首页直接发起或继续现有 Chat。

本阶段只建立产品入口和可验证的真实数据契约，不实现 Wiki 摄取、向量检索、知识图谱、飞书连接器或 HR 问答。未实现能力必须显示为中文引导或“尚未连接”，不能使用生产假数据，也不能伪造保存成功。

### 验收结果

1. `/#/assistant` 成为默认入口，旧 `/#/coding/session/:sessionId` 深链保持可用；
2. 首页摘要由 `GET /api/v1/assistant/home` 返回，所有计数和列表来自现有存储；
3. 首页 Composer 能创建一次现有 Coding Session，并带用户输入进入聊天；
4. 最近对话能继续进入原 session，运行恢复仍走既有 replay/reconnect；
5. 知识库、成长记录和公开主页有诚实的阶段空状态，不调用不存在的后端；
6. 浅色/深色在桌面、平板、手机三视口无溢出、遮挡和不可达操作。

## 2. 架构与数据约束

### 2.1 首页是读模型，不是新的 canonical store

`AssistantHomeSummaryService` 只投影现有数据：

- `CodingSessionStore`：最近未归档对话、消息数、工作区和更新时间；
- workspace-scoped `MemoryStore`：按当前可见 session 汇总 pending proposal；
- `CloudRepository`：已登录云模式下的当前用户和 owner-scoped project；工作区状态先从近期 Coding Session 投影；
- 本地模式：返回明确的 `local` identity，不伪造云账号；
- Knowledge：P1 固定返回 `not_configured` 和零计数，表示能力尚未接入，而不是“连接成功”。

首页 API 不扫描完整工作区文件、不读取 Memory 正文、不反序列化 timeline 内容，也不调用模型。聚合结果必须有界，近期列表默认最多 6 条。

### 2.2 身份与隔离

- 如果启用了云 Control Plane，必须从 HttpOnly session 解析用户，不能接受前端传入 `user_id`；
- 云用户只看到 `owner_user_id` 与自己匹配的 Coding Session、Project 和 Workspace；
- 本地未登录模式只显示无 owner 的本地 session；
- 一个聚合分区失败时以 section-level `status/error` 表达，不把其他分区变成 500；
- 密钥、Provider 凭据、Memory 内容、文件内容和跨用户统计不得进入响应。

### 2.3 前端状态

首页使用独立的轻量 `assistantHome` store，不复用或重置 `coding` store。进入首页不隐式创建 session，也不连接聊天 WebSocket；只有用户发送消息或点击继续会话时才进入 Coding 路由。

首页导航与 Chat 共用新的 `AssistantNavigation` 外壳。P1 不拆除 `CodingSidebar` 内部会话管理，以免扩大 V6.9 已验证的时间线状态机回归面；Chat 页面先增加“返回今天”的稳定入口，侧栏统一留到后续小步收敛。

## 3. 公共契约

### 3.1 API 响应

```json
{
  "identity": {
    "mode": "local",
    "user_id": null,
    "display_name": "本地工作区"
  },
  "knowledge": {
    "status": "not_configured",
    "source_count": 0,
    "wiki_page_count": 0,
    "last_synced_at": null
  },
  "sessions": {
    "status": "ready",
    "items": [],
    "total": 0,
    "error": null
  },
  "projects": {
    "status": "unavailable",
    "items": [],
    "total": 0,
    "error": null
  },
  "proposals": {
    "status": "ready",
    "memory_pending": 0,
    "wiki_pending": 0,
    "note_pending": 0,
    "error": null
  },
  "suggested_actions": [
    {
      "id": "start-conversation",
      "kind": "chat",
      "label": "开始一次学习对话",
      "description": "让 Sage 帮你理解项目、整理资料或制定练习。",
      "target": "/assistant?action=compose"
    }
  ]
}
```

`status` 使用 `ready | empty | not_configured | unavailable | error`。`suggested_actions` 由服务端按固定优先级和状态生成，最多 4 条，不调用 LLM。

### 3.2 前端路由

- `/` -> `/assistant`；
- `/assistant`：真实首页；
- `/coding` 与 `/coding/session/:sessionId`：现有 Practice Engine；
- `/knowledge`：知识源与摄取状态；
- `/evolution`：笔记、Wiki 与 Memory Proposal；
- `/public`：公开资料包预览；
- `/settings/:section?`：保持现有设置中心；
- 未知路由 -> `/assistant`。

## 4. 纵向实施切片

### Slice 1：真实首页摘要 API

**新增：**

- `core/assistant/__init__.py`
- `core/assistant/home.py`
- `api/assistant.py`
- `tests/core/assistant/test_home.py`
- `tests/api/test_assistant_routes.py`

**修改：**

- `api/schemas.py`
- `api/main.py`

**步骤：**

1. 先为本地空状态、近期 session 排序/上限、owner 过滤、pending proposal 去重和分区降级写失败测试；
2. 定义 `AssistantHomeSummary` 及各 section schema，限制枚举和列表长度；
3. 实现纯聚合 `AssistantHomeSummaryService`，依赖以构造参数注入，避免直接依赖 FastAPI Request；
4. route 只负责解析可选云身份、组装 repository/store 并返回 schema；
5. 在 `create_app()` 注册 router；云仓库不可用时本地模式可正常返回，云认证已启用但 cookie 无效时沿用现有认证策略；
6. 验证响应不包含 secret、memory content、workspace file content 或其他 owner 数据。

**完成证据：** 定向后端测试、`ruff check`、`mypy` 和 API schema 字段断言通过。

### Slice 2：首页读取、导航和确定性状态

**新增：**

- `frontend/src/api/assistant.ts`
- `frontend/src/stores/assistantHome.ts`
- `frontend/src/components/assistant/AssistantNavigation.vue`
- `frontend/src/components/assistant/AssistantHomeSummary.vue`
- `frontend/src/components/assistant/index.ts`
- `frontend/src/views/AssistantHomeView.vue`

**修改：**

- `frontend/src/types/api.ts`
- `frontend/src/router/index.ts`
- `frontend/src/style.css`

**测试：**

- `frontend/src/api/assistant.test.ts`
- `frontend/src/stores/assistantHome.test.ts`
- `frontend/src/components/assistant/AssistantNavigation.test.ts`
- `frontend/src/views/AssistantHomeView.test.ts`
- `frontend/src/router/index.test.ts`

**步骤：**

1. 先覆盖加载、重试、分区错误、空状态、最近对话路由和未知路由重定向；
2. 新 store 只管理 summary/loading/error/refresh，不导入 Coding Store；
3. `AssistantNavigation` 使用图标 + 中文标签，桌面固定左栏，移动端全屏 Sheet，支持 Escape、焦点返回和当前路由标识；
4. 首页中间区按“欢迎/同步状态 -> 主 Composer -> 建议动作 -> 最近项目/对话 -> 待确认沉淀”排布；
5. 使用 Sage Green、Source Blue、Review Yellow、Coral Red 表达状态，保持背景和正文为中性灰；
6. 所有空状态提供真实下一步，不展示不存在的 Graph、RAG 指标或摄取进度。

**完成证据：** 组件和 store 测试通过，首页仅发起一次 summary 请求，刷新不会创建 session。

### Slice 3：首页 Composer 接入现有 Chat

**修改：**

- `frontend/src/views/AssistantHomeView.vue`
- `frontend/src/stores/coding.ts`
- `frontend/src/views/CodingView.vue`
- `frontend/src/components/coding/composer/CodingComposer.vue`（仅在现有事件不足时）

**测试：**

- `frontend/src/views/AssistantHomeView.test.ts`
- `frontend/src/stores/coding.test.ts`
- `frontend/src/views/CodingView.test.ts`

**步骤：**

1. 为“输入内容 -> 创建一次 session -> 路由 -> 发送一次消息”写失败测试；
2. 在 Coding Store 暴露一个有明确幂等边界的 `startSessionWithPrompt(prompt)`，复用已有 session 初始化和发送逻辑；
3. 首页发送期间禁用重复提交，失败保留草稿并显示中文错误；
4. 成功后进入 `/coding/session/:id`，消息继续由既有 timeline 持久化和回放；
5. 点击最近对话只路由/恢复，不自动发送内容；
6. Coding 页面增加“今天”导航入口，不改变 replay、run reconnect、审批或滚动语义。

**完成证据：** session 只创建一次、消息只发送一次；失败不产生幽灵 session，旧深链回归测试通过。

### Slice 4：知识/成长/公开阶段页面

**新增：**

- `frontend/src/views/KnowledgeView.vue`
- `frontend/src/views/EvolutionView.vue`
- `frontend/src/views/PublicProfileView.vue`

**修改：**

- `frontend/src/router/index.ts`

**测试：**

- `frontend/src/views/KnowledgeView.test.ts`
- `frontend/src/views/EvolutionView.test.ts`
- `frontend/src/views/PublicProfileView.test.ts`

**步骤：**

1. Knowledge 页只解释当前可接入的 Markdown、Obsidian、GitHub 来源和 P2 建设状态；
2. Evolution 页复用首页 proposal 计数，并链接到已有 Memory 设置，不伪造 Wiki/Note 审批；
3. Public 页明确“仅发布已筛选资料包”，不显示私有 session、Memory、Git 或工作区；
4. 三页共用导航与页面标题栏，不使用卡片嵌套或营销 Hero；
5. 不添加无后端能力的上传、发布、同步按钮。

**完成证据：** 每个入口都有可访问的诚实状态，路由前进/后退和直接刷新正常。

### Slice 5：视觉、响应式与真实浏览器验收

**修改：**

- `frontend/src/style.css`
- 本阶段新增 Assistant 组件样式

**步骤：**

1. 扩展现有 Design Token，不引入第二套主题系统；
2. 桌面 `1440x900`：约 228px 左栏，中间内容最大宽度约 920px，首屏能看到 Composer 和至少一个后续区块；
3. 平板 `1024x768`：左栏可收起，标题、Composer 和状态行不重叠；
4. 手机 `390x844`：左栏为全屏 Sheet，长中文标题、路径和按钮不溢出；
5. 检查 light/dark/system、加载、空、部分错误、有近期 session、有 proposal 六类状态；
6. 检查键盘顺序、focus-visible、ARIA、Escape、焦点返回和 `prefers-reduced-motion`；
7. 使用当前 V7 worktree 的新端口启动后端/前端，确认不是旧 V6 服务，再执行截图和控制台检查。

**完成证据：** 三视口截图、无控制台错误、无 UI 重叠、真实 API 数据与页面一致。

## 5. 完整门禁

短期分支提交前执行：

```bash
pytest -q tests/core/assistant tests/api/test_assistant_routes.py
pytest -q
ruff check .
mypy core/ mcp_servers/ agents/
cd frontend && npm test -- --run
cd frontend && npm run build
git diff --check
```

若仓库实际 mypy 门禁已包含 `api/`，按 CI 命令扩展执行，不为了通过而缩小现有门禁。

浏览器至少验证：

- 首次空状态；
- 继续最近 session；
- 首页发送后进入 Chat；
- Chat 返回首页再恢复 session；
- summary 请求失败和局部分区失败；
- 浅色与深色三视口。

## 6. Git 与阶段收口

1. 从 `dev/sage-v7` 创建 `codex/feat-v7-assistant-home` 隔离 worktree；
2. 每个 Slice 红绿验证后形成小提交，避免把共享 store、API 和视觉混成一个不可审查提交；
3. 完成 Spec Review 和 Code Quality Review，明确给出“可合并 / 继续开发 / 需修复”；
4. 合入 `dev/sage-v7` 后在集成 worktree 重跑受影响测试、构建和 `git diff --check`；
5. 确认集成分支包含短期提交且 worktree 干净后删除短期 worktree/分支；
6. 更新 Obsidian `sage-learning`，记录 source commit、API/组件职责、测试证据、关闭风险和 V7-P2 边界；
7. V7-P1 不合入 `main`，不部署服务器，不启动 cc-connect 联调。

## 7. 下一阶段边界

V7-P2 才新增 `KnowledgeWorkspace`、`KnowledgeSource`、`SourceRevision`、持久摄取队列、raw/wiki/schema 结构、两阶段摄取、哈希去重和 Wiki diff review。V7-P1 的 `knowledge.status=not_configured` 是显式兼容位，P2 将用真实聚合替换，不改变首页响应的顶层结构。
