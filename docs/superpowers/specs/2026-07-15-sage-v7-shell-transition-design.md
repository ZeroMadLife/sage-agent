# Sage V7-P1.1 共享 Shell、字号与对话形变设计

> 日期：2026-07-15
> 分支：`codex/feat-v7-shell-transition`
> 基线：`dev/sage-v7@9b98a26`
> 状态：用户已确认，进入实施

## 1. 问题

V7-P1 已交付个人助手首页，但首页与 Coding Chat 仍是两个独立页面壳：首页使用 `AssistantNavigation`，Chat 自带 `CodingSidebar`。从首页发送首条消息时，界面只能在 session 创建后直接切换路由，视觉上没有“从个人助手开始一段对话”的连续感。

同时，首页、导航和过程信息大量使用 `9px` 到 `12px` 字号。它们在 1440px 桌面上信息密度较高，但中文可读性不足。

## 2. 目标

1. 首页与 Chat 共享稳定的 Sage 主导航和页面背景；
2. 首页 Composer 在 session 创建成功后形变为 Chat 底部 Composer；
3. 用户首条消息只发送一次，失败时保留草稿；
4. 提高首页、导航、聊天正文、Composer 和会话列表字号；
5. 支持深链、刷新恢复、移动端菜单、减少动态效果和不支持 View Transition 的浏览器；
6. 不修改后端 timeline、run reconnect 或首条消息发送协议。

## 3. 非目标

- 本阶段不实现 KnowledgeWorkspace、embedding 或 Hybrid RAG；
- 不重写 Session Store 和 WebSocket 状态机；
- 不引入动画库；
- 不把设置页、终端或 Inspector 放回聊天主界面；
- 不以动画掩盖 session 创建延迟或失败。

## 4. 方案选择

### 4.1 备选方案

1. Vue 路由淡入淡出：实现简单，但 Composer 没有空间连续性；
2. 单页面状态切换：首页和 Chat 共存于一个大组件，动画自由，但会破坏深链、刷新恢复和组件边界；
3. 应用级稳定 Shell + Shared Element View Transition：保留真实路由，以原生同文档 View Transition 连接两个 Composer。

采用方案 3。浏览器通过特性检测使用 `document.startViewTransition`，不支持时直接导航；`prefers-reduced-motion: reduce` 下禁用形变。

## 5. 页面结构

`App.vue` 持有应用级布局选择：

```text
App
├── Naive UI providers
├── AssistantNavigation（assistant/coding/knowledge/evolution/public）
│   └── RouterView
└── RouterView（settings）
```

个人助手相关页面不再各自创建导航。Chat 保留会话列表能力，但改为从标题栏打开的 Drawer，不再与 Sage 主导航形成双重常驻侧栏。桌面主界面因此保持“能力菜单 + Chat”，移动端继续使用带焦点圈定的全屏主菜单和会话 Drawer。

## 6. Composer 形变

### 6.1 状态序列

```text
ready
  -> creating_session
  -> session_ready
  -> route_transition
  -> observing_run

creating_session -> failed -> ready（草稿保留）
```

1. 用户提交后锁定重复发送；
2. `startSessionWithPrompt` 创建 session，并把首条消息登记为待发送消息；
3. session ID 返回后记录 recent session；
4. 使用 `startViewTransition` 执行 `router.push` 并等待新路由 DOM 完成一次更新；
5. 首页和 Chat Composer 使用同一 `view-transition-name: sage-composer`；
6. 新 Chat 挂载后沿用现有 WebSocket on-open 单次发送机制；
7. optimistic user message 进入时间线，timeline 内容轻微上移淡入。

动画时长为 `280ms` 到 `320ms`，使用非线性 ease-out。移动端缩短位移并以淡入为主，避免软键盘出现时产生大范围跳动。

### 6.2 失败与降级

- session 创建失败：不触发路由和形变，恢复按钮并保留输入；
- 路由失败：保留当前有效 session 观察状态，显示中文错误；
- 不支持 API：直接执行路由导航；
- 减少动态效果：不使用 shared-element animation；
- 重复点击和重复 WebSocket open：首条消息仍只发送一次。

## 7. 字号系统

新增语义字号 Token，不使用 viewport width 缩放字体：

| Token | 尺寸 | 用途 |
| --- | --- | --- |
| `--sage-font-xs` | 12px | 时间、计数、技术元信息 |
| `--sage-font-sm` | 13px | 次要说明、控件辅助文字 |
| `--sage-font-md` | 14px | 导航、普通控件 |
| `--sage-font-body` | 15px | 中文正文、Composer |
| `--sage-font-lg` | 18px | 分区标题 |
| `--sage-font-title` | 28px | 页面标题 |

主要页面不再显示 `9px` 或 `10px` 文本。代码输出和极窄状态标记可以使用 `12px`，但必须保持足够行高和对比度。

## 8. 测试边界

自动化验证：

- 首页发送只创建一个 session、只调用一次导航；
- View Transition 支持、缺失和减少动态效果三种路径；
- session 创建失败保留草稿；
- 主导航跨 assistant/coding 路由保持同一 Shell；
- Chat 会话 Drawer 的 Escape、焦点圈定和焦点返回；
- 深链、刷新、timeline replay 和 run reconnect 不回归。

浏览器验证：

- `1440x900`：主导航稳定、Chat 无双重常驻侧栏、Composer 连续形变；
- `1024x768`：消息与 Composer 无遮挡；
- `390x844`：菜单/会话 Drawer、中文长文本和软键盘布局可用；
- 浅色、深色和减少动态效果均通过。

## 9. 服务器部署节点

V7 采用两次部署，而不是等全部完成后一次上线：

1. **V7-P2 开始时建立私有预发布环境**：Docker Compose、域名/HTTPS、GitHub OAuth callback、PostgreSQL/pgvector 持久卷、备份恢复、日志和健康检查；仅本人或邀请用户访问；
2. **V7-P5 公开上线**：P2 知识审核、P3 检索评测、权限隔离、公开资料包和泄漏测试通过后，才开放 HR Agent。

Kubernetes 不作为首发依赖。单机 Docker Compose 达到容量或故障域瓶颈后再引入。

## 10. 后续边界

- V7-P2：KnowledgeWorkspace、SourceRevision、持久摄取、Wiki Draft 与 diff review；
- V7-P3：PostgreSQL FTS + pgvector + RRF、稳定引用、上下文预算和 Benchmark；
- V7-P4：Query Note、Wiki Proposal、Memory Proposal 的受控进化闭环；
- V7-P5：HR 公开 Agent 与一键部署。
