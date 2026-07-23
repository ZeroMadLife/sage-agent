# Sage UI Style Guide

> 版本：S1 / K2 / A1 视觉收束稿
>
> 日期：2026-07-22
> 适用范围：Sage 私人工作台、Knowledge、设置；公开门面可复用排版和状态色，但必须保持公开隔离

## 1. 品牌语气

Sage 是一个个人 AI 学习伴侣，不是普通聊天机器人，也不是数据监控后台。界面需要让用户感到：

- 我知道现在的目标是什么；
- 我能看懂 Sage 做过的事实；
- 我可以决定什么被保存；
- 我的知识结构可以被探索，但不会被装饰性噪音淹没。

关键词：`安静`、`清楚`、`可追溯`、`有行动出口`。

禁止的视觉语气：满屏纯绿、广告式渐变、卡片套卡片、伪造实时思考、过大的统计数字、彩虹图谱、把所有设置堆在一个长页面。

## 2. 颜色

### 2.1 基础色

| Token | Hex | 用途 |
| --- | --- | --- |
| `--sage-bg` | `#F8FAFC` | 应用背景、设置背景 |
| `--sage-canvas` | `#FBFCFD` | Knowledge 图谱画布 |
| `--sage-surface` | `#FFFFFF` | 输入框、浮层、主要操作表面 |
| `--sage-surface-muted` | `#F1F5F3` | 选中行、弱提示区 |
| `--sage-text` | `#1E293B` | 主文字 |
| `--sage-text-secondary` | `#64748B` | 辅助说明、元数据 |
| `--sage-text-muted` | `#68766F` | 非主要提示、时间戳 |
| `--sage-border` | `#D7E0DC` | 普通分隔线、输入框边框 |
| `--sage-border-strong` | `#CBD5E1` | 焦点、浮层边界 |

### 2.2 语义色

| Token | Hex | 语义 | 使用范围 |
| --- | --- | --- | --- |
| `--sage-brand` | `#2F6B50` | Sage 品牌、已确认、已验证 | 品牌图标、主按钮、完成状态 |
| `--sage-brand-soft` | `#E1EEE7` | 品牌弱背景 | 导航选中、目标摘要 |
| `--sage-research` | `#3177C9` | 检索、证据、恢复、链接 | tool/event、citation、路径 |
| `--sage-research-soft` | `#E8F1FB` | 证据弱背景 | 证据条、恢复提示 |
| `--sage-approval` | `#8A5A12` | 审批、知识缺口、待确认 | proposal、gap、需用户决定 |
| `--sage-approval-soft` | `#FFF9ED` | 审批弱背景 | 审批行、警示说明 |
| `--sage-danger` | `#C2413B` | 失败、拒绝、不可恢复 | error、reject、越权 |
| `--sage-danger-soft` | `#FFF1F0` | 失败弱背景 | 错误提示、恢复失败 |

### 2.3 图谱社区色

图谱社区颜色必须保持低饱和，并且在白色画布上有清晰对比。建议顺序：

```text
#2AAE9B  teal
#7B9853  olive
#E46A63  coral
#5FA77B  sage
#8B5FEA  violet
#D78C38  amber
#3F80E8  blue
```

社区色只表示群组，不表示完成度、质量或优先级。选中态使用描边和外环，不通过把节点换成白色表达。

## 3. 字体与排版

### 3.1 字体栈

```css
--sage-font-sans: "IBM Plex Sans", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
--sage-font-serif: "Source Han Serif SC", "Noto Serif SC", "Songti SC", serif;
--sage-font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
```

- 默认使用无衬线，保证操作和扫描；
- `serif` 仅用于公开门面或少量知识标题，不用于按钮和状态；
- mono 仅用于完整运行详情中的 `run_id`、revision、path、event kind、source id；日常界面不回显这些技术标识；
- 不使用 viewport 参与字号计算，字距固定为 `0`，不使用负字距。

### 3.2 字号层级

| 名称 | Size / Line height | 用途 |
| --- | --- | --- |
| Display | `32 / 40` | 页面标题，仅一处 |
| Heading | `22 / 30` | Surface 标题、目标标题 |
| Subheading | `16 / 24` | 区块标题 |
| Body | `14 / 22` | 普通内容 |
| Small | `12 / 18` | 元数据、辅助说明 |
| Micro | `11 / 16` | 图谱计数、receipt、时间 |

移动端只缩减 Display/Heading 一级，不低于 Body `14px`，不能为了塞入卡片而压缩到不可读。

## 4. 间距、尺寸与形状

### 4.1 间距

基础单位为 `4px`，常用节奏：`4 / 8 / 12 / 16 / 24 / 32 / 40 / 48`。

- 页面内边距：桌面 `24-32px`，移动 `16px`；
- Surface 区块之间：`24px`；
- 表单行之间：`12-16px`；
- 图谱画布不放大面积说明面板，说明间距优先使用 `16px`。

### 4.2 半径

| Token | Value | 用途 |
| --- | --- | --- |
| `--sage-radius-sm` | `4px` | 标签、紧凑按钮 |
| `--sage-radius-md` | `6px` | 输入框、普通按钮、行 |
| `--sage-radius-lg` | `8px` | 浮层、主要卡片 |
| `--sage-radius-pill` | `999px` | 状态点、圆形图标容器，不放长文本 |

页面 section 不做大圆角框；卡片只用于真正独立的重复项、审阅项和浮层。

### 4.3 阴影

```css
--sage-shadow-float: 0 12px 32px rgba(30, 41, 59, 0.10);
--sage-shadow-focus: 0 0 0 3px rgba(49, 119, 201, 0.18);
--sage-shadow-node: 0 3px 10px rgba(30, 41, 59, 0.12);
```

默认列表没有阴影。浮层必须有边框 + 阴影，避免只靠阴影区分层级。

## 5. 组件规则

### 5.1 按钮

- 主操作：Sage 绿实心，文字白色，最小高度 `36px`；
- 次操作：白底、边框、主文字；
- 危险操作：红色只用于确认后的 destructive action；
- 工具按钮使用 Lucide 图标，熟悉符号不再包一层长文字圆角矩形；
- 不熟悉的图标必须有 tooltip 和 `aria-label`；
- 主操作使用“开始研究”“导入知识库”“继续目标”等明确动词，不使用“确定”作为唯一文案。

### 5.2 导航

- 导航选中使用浅色背景 + 左侧 2px 线或图标色，不使用整块深绿填充；
- 主导航最多 3 个主要入口，设置固定底部；
- 折叠导航后仍保留 tooltip、当前态和键盘名称。

### 5.3 输入与 Composer

- Composer 是页面最重要的操作表面，保持白底、边框和轻微焦点环；
- 会话页固定在应用视口内，只有消息时间线滚动；Composer 在切页返回和长回答场景中始终停靠在底部；
- 桌面会话 Composer 输入区最小高度约 `96px`，移动端不低于 `72px`；
- 发送按钮使用 familiar send icon，tooltip 为“发送”；
- 模型、Skill、MCP 使用 menu/segmented control，不用多个并排大卡片；
- 上下文条用 `research-soft` 或 `brand-soft`，可明确移除；
- 字数/token 计数是辅助信息，不能比输入内容更抢眼。

### 5.4 卡片与事实行

- Facts Rail 采用纵向事实行，不在事实行内再套卡片；
- Proposal、Approval、Evidence 可用独立卡片，因为它们需要审阅或决策；
- 完成状态默认收缩，等待用户动作的状态展开；
- 行内只放一项主要动作，次要动作放菜单。

### 5.5 状态行

状态结构固定为：`icon + label + short fact + action`。不要用动画图标掩盖缺少事实的问题。

## 6. Motion Tokens

| Token | Value | 用途 |
| --- | --- | --- |
| `--sage-motion-fast` | `120ms` | hover、opacity、focus |
| `--sage-motion-base` | `150ms` | 按钮、抽屉、菜单 |
| `--sage-motion-panel` | `190ms` | Facts Rail、Inspector |
| `--sage-motion-layout` | `220ms` | 导航宽度、浮层位置 |
| `--sage-motion-graph` | `900ms` 上限 | 初始布局收敛或局部重热 |

### 6.1 事实绑定

- Run 进度线只有在新的 timeline event 到达时前进；
- 工具出现时只展示真实 `stage_started`/`tool_started`；
- Proposal 卡片只在 timeline/API 返回 proposal 时出现；
- 选中节点的环和边高亮是用户交互事实，不模拟模型推理；
- ForceAtlas2 完成后停止，拖拽后的局部稳定也必须有上限。

### 6.2 Reduced motion

`prefers-reduced-motion: reduce` 时：

- 取消持续布局、弹簧和漂移；
- 侧栏和 Inspector 使用不超过 `120ms` 的 opacity/translate；
- 状态用颜色、图标、文字和进度条表达，不依赖闪烁；
- 图谱直接使用稳定坐标或短暂淡入。

## 7. Knowledge 图谱视觉令牌

```css
--graph-bg: #FBFCFD;
--graph-edge: rgba(100, 116, 139, 0.22);
--graph-edge-focus: #3177C9;
--graph-edge-path: #2F6B50;
--graph-node-ring: #FFFFFF;
--graph-node-selected: #1E293B;
--graph-label: #334155;
--graph-label-muted: rgba(100, 116, 139, 0.58);
```

行为规则：

- 全局边细而可见；
- hover 将无关节点降到约 `0.28` opacity，无关边不直接删除；
- selected 使用 `ring + zIndex`，节点填充保持社区色；
- label 只为选中、hover、路径和高权重节点显示；
- 画布中不放解释性长文案，只保留一个短 legend 或 count。

## 8. 响应式规则

### 桌面

- 1728：可同时显示主画布、Facts/Inspector 与来源栏；
- 1440：Inspector 浮层优先，Facts Rail 可收起，图谱保留主视觉；
- 全局导航收起后使用 `64px` 固定轨道，不改变内容坐标逻辑。

### 移动

- 单列、底部导航、bottom sheet；
- 触控目标不小于 `44px`；
- 图谱控制固定在右下但不遮挡选中详情；
- 列表是图谱的等价可访问入口；
- 文本超出时换行，不截断关键状态和按钮文案。

## 9. 可访问性

- 颜色不是唯一状态信号；
- 所有图标按钮具备 `aria-label`；
- Dialog 打开后焦点进入，关闭后回到触发元素；
- 图谱 hover 语义在键盘 focus、点击和列表模式可获得；
- 文字、边框和 focus ring 满足 WCAG AA 目标；
- 键盘快捷键可被命令面板发现，并可关闭。

## 10. Do / Don't

### Do

- 用留白和分隔线建立结构；
- 用真实事实决定状态；
- 把长解释放入详情或 tooltip；
- 用图谱展示关系，用主对话完成行动；
- 用一个明显的下一步按钮收束每个状态。

### Don't

- 不要把每个页面都做成 dashboard 卡片墙；
- 不要用渐变球、bokeh、装饰性 3D 图形；
- 不要把所有节点标签永久画出来；
- 不要复制第二套 Chat store/runtime；
- 不要让白色 selection layer 覆盖深色节点；
- 不要把“有 85 个 Wiki 页面”直接写成“已经掌握 85 个知识点”；
- 不要使用全屏营销 Hero 代替实际产品入口。

## 11. 新对话首页

- 新对话是任务起点，不是指标 Dashboard；首屏只回答“现在要推进什么”；
- 桌面主内容宽度不超过 `820px`，Composer 不超过 `760px`；
- 建议项最多 4 个，必须来自最近会话、知识库状态、待确认沉淀或产品固定能力，禁止伪造个性化目标；
- 主输入框使用轻边框、`15-18px` 圆角和单层阴影，不套在第二张卡片中；
- 研究、练习、导入只作为 Composer 工具或建议，不扩展成门户九宫格；
- 最近会话只保留一条明确继续入口，历史列表留在会话导航。

## 12. 社区轨道图谱

- 默认主图只投影语义节点；`source` 与 `EVIDENCED_BY` 属于来源/证据层，只在筛选或 Inspector 中显式打开；
- 每个社区选取一个真实高权重语义节点作为中心，不能渲染虚构社区圆或节点套节点；
- 度数大于 1 的节点进入内圈，叶子节点均匀分布在外圈，孤立节点在社区外围保持可见；
- 社区中心位置由稳定 seed 决定，刷新、切页和重新打开不得随机跳位；
- ForceAtlas2 仅短时处理碰撞，随后以 `ease-out-cubic` 在 `300-450ms` 内归位并停止；
- hover/selected 时邻接边增强、无关边渐隐；静止态的真实关系边始终微弱可见；
- `prefers-reduced-motion` 下直接使用稳定轨道坐标，不运行持续布局。

## 13. 技术标识可见性

- `run_id`、`graph_revision`、`sequence`、内部 stage kind 不进入主对话标题、Facts、Knowledge 头部和上下文条；
- 主层使用“运行中”“已绑定图谱”“已选节点”“知识页版本”等产品语义；
- 原始引用仅在完整运行详情、Inspector 证据页和导出审计中按需显示；
- 隐藏技术标识只改变呈现，不得删除 store、timeline 或 frozen receipt 中的原始字段。
