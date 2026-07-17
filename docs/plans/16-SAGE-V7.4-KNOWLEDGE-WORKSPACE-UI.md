# Sage V7.4 Knowledge Workspace UI 设计与开发计划

## 1. 本轮边界

本计划只负责 Knowledge Workspace 的页面、交互、组件和前端数据契约，不设计个人助手 Agent Harness。

另一个开发会话负责：

- 主对话与个人助手能力；
- Agent 工具选择与调度；
- 对话中的检索、学习与记忆策略；
- Coding/Assistant timeline；
- Harness 内部的自治执行方式。

Knowledge 页面只提供“建库、同步、观察与治理”界面，以及一个“在对话中使用”的集成动作。它不复制一套 Chat，也不规定 Agent 内部如何工作。

## 2. 产品决策

当前 Knowledge 页面中心的“向知识库提问”是 RAG 联调工具，不应该成为正式产品主入口。

正式 Knowledge 页面只让用户理解三件事：

1. 可以一次导入本地文件、文件夹，或连接 GitHub/Obsidian；
2. Sage 会自动发现变化、解析内容、更新 Wiki 和索引；
3. 用户查看最终知识结构、同步过程和少量真正需要处理的异常。

直接检索能力继续保留在后端，供主对话会话按需使用。Knowledge 页面不再展示 citation 调试列表或“沉淀为学习笔记”实验按钮。

## 3. 用户最终看到的过程

```text
选择文件 / 文件夹 / GitHub
        ↓
页面显示发现、解析、视觉处理、Wiki 更新、索引进度
        ↓
普通内容自动完成，不逐条审核
        ↓
中心区域从导入态切换为 Wiki Overview
        ↓
用户查看主题、页面、来源、版本和异常
        ↓
需要使用知识时，点击“在对话中使用”进入主对话
```

“自动”不是前端动画。页面每个状态都必须来自持久后端任务和 revision 数据，刷新后可以恢复。

## 4. GitHub 与 Obsidian 的产品表达

### 4.1 推荐关系

用户的私有 `Sage-knowledge` Git repository 是 canonical Wiki。Obsidian 可以直接打开该 repository 或其中的 Markdown 目录。

```text
Sage-knowledge/
├── .sage/
├── wiki/          # Sage 自动形成的知识页面
├── notes/         # 用户在 Obsidian 中维护的笔记
├── index.md
└── log.md
```

这样 GitHub 和 Obsidian 是同一份知识资产的两个入口，不是两套互相覆盖的数据。

### 4.2 页面必须说清的能力边界

| 接入方式 | 用户看到的能力 |
| --- | --- |
| 文件/文件夹 | 浏览器一次性导入，适合快速建库 |
| GitHub | 授权后持续同步 repository 更新 |
| Obsidian + Git | Vault 推送到 GitHub 后持续同步 |
| Local Companion | 未来直接观察本地 Vault，V8 开放 |

浏览器关闭后无法持续读取任意本地目录，因此 UI 不能把一次性文件夹上传描述成“持续监控 Obsidian”。

大 PDF、图片和视频也不应默认塞入普通 Git history。页面只展示它们已被解析和引用；云端原件由对象存储保存，Git Wiki 保存 Markdown、元数据与 revision。

## 5. 最终信息架构

### 5.1 顶部产品栏

只保留：

- Sage；
- 对话；
- 知识；
- 来源；
- 设置；
- 全局搜索；
- 同步状态；
- 用户菜单。

“智能体、笔记、学习计划、数据源”是否作为独立一级导航由主产品 Shell 决定，本轮不修改共享导航。

### 5.2 Knowledge 页面三栏

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Sage  对话  知识  来源  设置        全局搜索            ● 已同步  用户菜单 │
├──────────────┬──────────────────────────────────────┬──────────────────────┤
│ 知识空间      │ Overview / Wiki / Graph / Activity  │ Inspector            │
│              │                                      │                      │
│ + 添加来源    │ 首用：拖入文件/文件夹                 │ 当前来源/页面/节点    │
│              │      连接 GitHub / Obsidian          │ 摘要、revision、引用  │
│ 全部知识      │                                      │ 关联页面、更新时间    │
│ 最近更新      │ 完成：Wiki Overview 或知识图谱        │ 在对话中使用          │
│ 自动沉淀      │                                      │ 查看历史 / 撤销       │
│ 需处理 2      │                                      │                      │
│              │                                      │                      │
│ Sources      │                                      │                      │
│ ▾ Sage Notes │                                      │                      │
│   ▸ Projects │                                      │                      │
│   ▸ Concepts │                                      │                      │
├──────────────┴──────────────────────────────────────┴──────────────────────┤
│ 发现 128/128  │ 解析 117/128 │ Vision 3 │ Wiki 更新 12 │ 索引 12 │ 查看任务 │
└────────────────────────────────────────────────────────────────────────────┘
```

桌面宽度建议：

- Sidebar：`256px`；
- Canvas：`minmax(620px, 1fr)`；
- Inspector：`340px`，可收起和调宽；
- Activity Bar：`56px`；
- Topbar：`58px`。

平板将 Inspector 改为右侧 Drawer；手机端 Sidebar 与 Inspector 都使用全屏 sheet，Activity Bar 只显示当前阶段与总进度。

## 6. 四个核心页面状态

### 6.1 空知识库

中心只显示一个完整导入入口：

- 拖入文件；
- 选择多个文件；
- 选择文件夹；
- 连接 GitHub；
- 查看 Obsidian 接入方式。

旁边简短显示支持格式：Markdown、HTML、TXT、PDF、常见图片和代码文本。

不展示：

- 空图谱；
- RAG 提问框；
- revision ID；
- 空审核列表；
- FTS/embedding 技术指标；
- rebuild index 按钮。

### 6.2 正在构建

中心区域显示可理解的构建过程：

1. 正在发现文件；
2. 正在解析文档；
3. 正在理解图片/复杂 PDF；
4. 正在形成 Wiki；
5. 正在更新检索索引；
6. 已完成。

展示总数、当前文件、成功、跳过、异常与预计剩余量。用户可以关闭导入 Dialog 或离开 Knowledge 页面，底部 Activity Bar 继续显示进度。

失败文件不阻塞整个任务。文案使用“125 项已完成，3 项需要处理”，不弹出 128 次确认。

### 6.3 构建完成

默认进入 `Overview`，显示：

- 新形成的主题；
- 最近更新的项目/页面；
- 来源数量和最近同步；
- 本次形成/更新的 Wiki 页面；
- 可能缺失的知识；
- 需要处理的异常数量。

用户点开主题或页面后进入 Wiki 阅读。只有图谱数据达到有效密度并且 Graph API 已完成时，才开放 Graph Tab。

### 6.4 存在异常

左侧“需处理”显示数量，中心异常页按原因分组：

- GitHub 授权失效；
- 文件损坏或格式不支持；
- 需要 OCR/视觉模型；
- 低置信解析；
- 来源冲突；
- 敏感信息；
- 删除、公开或大范围覆盖。

普通成功内容永远不出现在异常列表。

## 7. 中心视图

### 7.1 Overview

Overview 是默认首页，不使用装饰性卡片墙。采用四个未装框的纵向区域：

1. 本次变化摘要；
2. 知识主题与项目；
3. 最近 Wiki 更新；
4. 来源健康与异常。

首次导入成功后，导入空状态原位过渡为 Overview，不执行整页闪白或突然跳路由。

### 7.2 Wiki

左侧 Source Tree 同时承担 Wiki 目录。中心正文：

- 标题、摘要和更新时间；
- Markdown 正文；
- 代码高亮、表格和图片引用；
- 页面内 heading outline；
- 引用标记可打开 Inspector；
- 页面顶部不显示内部 proposal ID。

历史 revision、Git commit、解析器版本和 rollback 放在 Inspector 的“历史”标签中。

### 7.3 Graph

Graph 是最终结果视图，但不阻塞 V7.4 首轮 UI：

- Node：Source、Page、Project、Concept、Decision、Tool、Person；
- 社区颜色与节点类型必须有图例；
- 默认只显示一层主要关系，避免毛线团；
- 点击节点后 Inspector 显示摘要、来源、关联页面和置信度；
- Filter、Layout、Fit、Fullscreen 使用图标按钮并提供 tooltip；
- 没有真实 Graph API 时不显示模拟节点。

### 7.4 Activity

Activity 展示用户可理解的同步历史：

- 哪个来源发生了变化；
- 新增、修改、重命名、删除数量；
- 形成或更新了哪些 Wiki 页面；
- 是否完成索引；
- 是否有异常；
- 何时发生、耗时多久。

技术日志和 stack trace 只在展开详情后显示。

## 8. Inspector

Inspector 根据选中对象切换内容，不打开嵌套 Modal。

### 来源 Inspector

- 来源名称、类型和连接状态；
- 最近同步、branch/path 和 cursor 摘要；
- 文件数、页面数和异常数；
- 立即同步、断开连接；
- 查看本次变化。

### 页面 Inspector

- 页面摘要；
- 原始来源与引用；
- 当前 page/source revision；
- 关联概念和页面；
- 最近学习/修改；
- 在对话中使用；
- 历史、diff 和撤销。

### 图节点 Inspector

- 节点类型和社区；
- 摘要；
- 证据来源；
- 关联节点；
- 置信度与最近更新时间；
- 在对话中使用。

“在对话中使用”只发出选中 `workspace/page/source/node` 的 scope 事件，具体 Agent 如何检索和回答由另一个会话设计。

## 9. 导入体验

### 9.1 Import Dialog

Import Dialog 使用四个 tab：

- 文件；
- 文件夹；
- GitHub；
- Obsidian。

文件/文件夹 tab 显示待导入数量、类型、总体积、重复项和不支持项。用户点击一次“开始构建”，之后不再逐文件确认。

GitHub tab 显示授权、repository、branch、包含/排除目录和同步方式。

Obsidian tab 提供两个选择：

- 推荐：通过私有 GitHub repository 持续同步；
- 未来：安装 Local Companion 直接观察 Vault。

### 9.2 文件状态

每个文件只使用以下用户态：

```text
等待 / 处理中 / 已完成 / 已跳过 / 需要处理 / 失败
```

内部 parsing、understanding、policy、projection 状态通过任务详情展示，不直接堆在文件列表主列。

## 10. 视觉系统

### 10.1 色彩

| 用途 | 色值 |
| --- | --- |
| 顶栏 | `#0D1420` |
| 页面背景 | `#F7F9FB` |
| Surface | `#FFFFFF` |
| 主文字 | `#17202A` |
| 次文字 | `#66717F` |
| 边框 | `#DFE4EA` |
| Sage green | `#20A878` |
| Process blue | `#348FE2` |
| Attention amber | `#E7A132` |
| Blocked coral | `#E6665A` |
| Community violet | `#7967E8` |

页面以黑白灰为主。绿色只表示 ready/synced，蓝色表示 processing，黄色表示 attention，红色表示 blocked，紫色只用于图谱社区。

### 10.2 字体与密度

- 页面主标题：20px；
- 区域标题：16px；
- 正文：15–16px；
- 导航：13–14px；
- 元数据：12px，不再使用更小字体；
- 行高：正文 1.65，导航 1.4；
- 卡片圆角不超过 8px；
- 页面分区优先使用边界、轨道与留白，不使用套娃卡片。

### 10.3 动效

- 空状态到构建态：内容淡出 120ms，进度轨道展开 180ms；
- 构建态到 Overview：保持同一 Canvas 容器，用 cross-fade 与高度插值完成；
- Sidebar 新页面使用 160ms 高亮和计数更新；
- Activity Bar 阶段变化只移动进度，不闪烁整个区域；
- 遵守 `prefers-reduced-motion`；
- 动画不能延迟真实状态显示。

## 11. 前端组件拆分

不继续扩张当前 600+ 行 `KnowledgeView.vue`：

```text
KnowledgeWorkspaceView.vue
├── KnowledgeTopbar.vue
├── KnowledgeSidebar.vue
│   ├── KnowledgeNav.vue
│   └── SourceTree.vue
├── KnowledgeCanvas.vue
│   ├── KnowledgeEmptyState.vue
│   ├── KnowledgeBuildProgress.vue
│   ├── KnowledgeOverview.vue
│   ├── WikiReader.vue
│   ├── KnowledgeGraph.vue        # Graph API 完成后开放
│   ├── KnowledgeActivity.vue
│   └── KnowledgeAttention.vue
├── KnowledgeInspector.vue
│   ├── SourceInspector.vue
│   ├── PageInspector.vue
│   └── GraphNodeInspector.vue
├── KnowledgeImportDialog.vue
└── KnowledgeActivityBar.vue
```

Store 只保存 Knowledge UI 状态：

```text
workspaceSummary
sources / wikiTree / selectedObject
syncRuns / activityCursor / attentionCount
canvasMode
sidebarState / inspectorState
importDraft
```

Chat timeline、Agent run、Memory 和模型状态不得进入 Knowledge Store。

## 12. 前端所需数据契约

本轮只定义 UI 需要什么，不规定后端或 Agent Harness 内部如何实现。

### KnowledgeWorkspaceOverview

```text
workspace_id
status
source_count
page_count
attention_count
last_synced_at
active_run
recent_changes[]
topics[]
```

### KnowledgeSourceSummary

```text
source_id
kind
label
status: ready | syncing | attention | disconnected
last_synced_at
object_count
page_count
attention_count
```

### KnowledgeSyncRunSummary

```text
run_id
source_id
status
stage
total_items
processed_items
succeeded_items
skipped_items
attention_items
failed_items
latest_sequence
started_at
completed_at
```

### KnowledgeActivityItem

```text
event_id
sequence
run_id
kind
status
title
detail
occurred_at
affected_page_ids[]
```

### KnowledgePageSummary

```text
page_id
path
title
summary
current_revision
updated_at
source_count
citations[]
related_page_ids[]
```

## 13. 前端开发阶段

### UI-0：现有能力重新编排

- 拆分 `KnowledgeView.vue`；
- 移除页面中心的 RAG 提问和 citation 调试列表；
- 复用现有 summary、pages、jobs、proposal 和 index API；
- 完成三栏 Shell、Overview、Activity Bar、异常入口和 Page Inspector；
- 手动批量任务先包装为“添加来源/开始构建”。

验收：现有后端不改也能得到明显不同的正式产品页面，用户不再看到内部调试流程。

### UI-1：多文件/文件夹 ImportSession

- 文件拖拽、文件夹选择和导入预检；
- Import Dialog 四个 tab；
- 大任务持久进度恢复；
- 部分失败和重试；
- 构建完成后原位过渡到 Overview。

验收：一次选择一个混合文件夹，只点击一次开始，普通文件全部自动处理。

### UI-2：GitHub/Obsidian 来源体验

- GitHub 授权、repo/branch/path 选择；
- Source health、最近同步和立即同步；
- Obsidian Git 引导；
- 授权失效与 connector 异常页；
- 来源 Inspector 与变化摘要。

验收：GitHub 来源配置后，页面以“已同步/同步中/需要处理”表达状态，不暴露 token 或服务器路径。

### UI-3：真实 Graph Workspace

- 接入真实 Graph API；
- Canvas、筛选、布局、图例与 Inspector；
- 平板/手机降级为主题列表和节点详情；
- 大图虚拟化或 WebGL 渲染；
- 无真实数据时保持 Wiki Overview，不显示假图。

## 14. 与另一个开发会话的文件边界

本 Knowledge UI 会话只修改：

- `frontend/src/views/Knowledge*`；
- 新的 `frontend/src/components/knowledge/**`；
- 新的 Knowledge UI store、API 和 types；
- Knowledge 页面测试和视觉截图；
- 本设计文档。

不修改：

- Coding/Assistant timeline；
- 主 Chat Composer；
- Agent Loop、Tool Executor 或个人助手策略；
- Memory/Dream；
- 另一个会话正在编辑的主对话组件。

Router、全局导航和全局 Design Token 如需调整，最后由 Integration 提交统一处理。

## 15. 验收

### 产品场景

- 空知识库第一次导入；
- 多文件/文件夹混合导入；
- 页面离开、刷新和回来后继续观察；
- 同一批次部分失败但成功内容可用；
- GitHub 正常同步与授权失效；
- 自动完成不出现逐条审核；
- 异常可以按来源和原因定位；
- Wiki 页面、来源和 revision 能相互跳转；
- 点击“在对话中使用”只传 scope，不在 Knowledge 页面生成第二套 Chat。

### 视觉与无障碍

- `1440×900`：三栏与底部 Activity Bar 完整可见；
- `1024×768`：Inspector Drawer，不遮挡主 Canvas；
- `390×844`：Sidebar/Inspector 全屏 sheet，长中文和路径不溢出；
- 键盘可完成添加来源、切换视图、打开/关闭 Inspector；
- Dialog/Sheet 具备焦点圈定、Escape 和焦点返回；
- 浅色/深色均有足够对比；
- 200+ 文件进度更新不引发布局跳动；
- `prefers-reduced-motion` 下无非必要动画。

## 16. 明确不做

- 不设计个人助手 Agent Harness；
- 不在 Knowledge 页面实现聊天；
- 不删除后端检索能力；
- 不把浏览器一次上传宣传成持续本地同步；
- 不在 Graph API 完成前绘制模拟节点；
- 不把所有技术指标和运维按钮放到首屏；
- 不让普通成功项进入审核队列；
- 不与另一个会话同时编辑共享主对话文件。
