# Sage V6.9 工作台运行体验收口设计

## 1. 背景与目标

Sage V6.9 已完成聊天时间线、运行恢复、Markdown 代码高亮和基础 Hermes 风格迁移。本阶段继续收口开发者工作台的核心体验，解决以下可见问题：

1. 页面进入后底部出现与工作流无关的大块空白，消息区和输入区没有稳定占满可用高度。
2. 新消息发送后，用户消息、运行状态和工具过程的视觉顺序不稳定。
3. `run_shell` 把正常的复合验证命令误判为普通文件读取并拒绝。
4. 流式文本和工具结果增长时不能可靠贴底，用户主动查看历史时又不能被强制拉回底部。
5. 模型目录和上下文能力分别配置，导致模型可选但页面显示“模型未配置”。
6. 工具参数和结果缺少专业工作台需要的连续时间线和可复制代码面板。

本阶段以用户提供的专业工作台截图为已批准视觉参考，参考其信息架构、密度和交互，不复制 Hermes Studio 源码、CSS、类名或素材。Sage 继续使用现有 Vue 3、Pinia、Sage token 和 timeline contract 独立实现。

## 2. 范围与非目标

### 2.1 本阶段交付

- 使用服务端 TOML 文件统一声明可选模型及其显式能力。
- 在聊天工作台右上区域展示真实上下文用量、总窗口、剩余预算和进度条。
- 修复聊天工作台高度、滚动容器和 Composer 布局，消除页面级空白。
- 保证新回合的视觉顺序为“用户消息 -> 思考中 -> 工具过程 -> 最终回答”。
- 建立粘性自动贴底规则和回到底部入口。
- 收紧 `run_shell` 普通读取优化规则，允许正常复合验证命令。
- 将工具参数和结果渲染为可展开、可复制的代码面板。
- 保持 `markdown-it + highlight.js` 对 JSON、Java、Python 等 fenced code 的渲染。

### 2.2 明确不做

- 不展示、伪造、持久化模型原始思维链。
- 不在未确认 provider 参数协议时为 DeepSeek 增加虚假的 reasoning 开关。
- 不静默猜测未知模型的上下文窗口。
- 不改变危险命令审批、权限模式、计划模式、路径约束、环境过滤和超时门禁。
- 不处理公网多租户、云 workspace、Linux 文件打开协议或移动端原生应用。
- 不复制 Hermes Studio 受许可证约束的实现代码。

## 3. 模型能力清单

### 3.1 单一配置源

新增仓库内服务端配置 `config/coding_models.toml`。生产部署可通过 `SAGE_CODING_MODELS_FILE` 指向另一个 TOML 文件。启动时只读取一次并生成：

- 模型目录：供 `/api/v1/coding/models` 和模型白名单使用。
- `ModelCapabilityRegistry`：供 runtime 创建、模型切换和上下文控制器使用。

注入 `coding_model_catalog` 或 `coding_model_capabilities` 的测试和嵌入调用保持兼容；只有两者都未注入时才使用 TOML。这样现有测试夹具不需要依赖磁盘配置。

### 3.2 TOML 结构

```toml
version = 1
default_model = "deepseek:deepseek-v4-flash"

[[models]]
id = "deepseek:deepseek-v4-flash"
label = "DeepSeek V4 Flash"
provider = "deepseek"
context_window_tokens = 128000
output_reserve_tokens = 20000
reasoning_modes = []
```

约束：

- `version` 必须为 `1`。
- `default_model` 必须存在于 `models`。
- 模型 `id` 必须唯一，数量不超过 256。
- `label`、`provider` 和 `id` 必须是非空字符串。
- `context_window_tokens` 与 `output_reserve_tokens` 必须同时出现或同时省略。
- 窗口上限、正整数和 reserve 小于 window 的检查复用现有 capability 校验。
- `reasoning_modes` 只允许已知枚举；空数组表示不支持。
- 文件不存在、TOML 无效或 schema 无效时启动失败，不以猜测值继续运行。

### 3.3 reasoning 边界

“思考中”是前端运行状态，表示服务端已接受回合且尚未完成，不代表模型思维链。

本阶段 API 暴露模型的 `reasoning_modes` 能力，但默认清单为空。前端只有在当前模型至少声明一种模式时才显示 reasoning 控件。由于当前 DeepSeek V4 Flash/Pro 的实际 provider 参数契约尚未在项目中得到验证，本阶段不声明能力、不传请求参数，也不显示开关。后续接入时必须同时完成：provider 参数映射、session/run 请求字段、持久化和端到端测试，不能只增加前端控件。

## 4. 上下文预算投影

### 4.1 服务端所有权

上下文 token 计数、effective limit、output reserve、压缩状态和阈值继续由服务端持有。前端只消费 `CodingContextSnapshot`，不自行估算模型窗口。

### 4.2 展示规则

上下文摘要放在工作台内容标题栏右侧，桌面显示：

```text
252.9k / 1.0M · 剩余 747.1k
```

同时显示细进度条。格式使用紧凑单位，保留最多一位小数；剩余量按 `max(model_limit_tokens - used_tokens, 0)` 计算。详细 tooltip 继续提供 effective limit、output reserve、估算标记和压缩状态。

Composer 底部不再显示“模型未配置”占位。只有服务端明确配置上下文时才渲染摘要或压缩入口；配置缺失时保持界面安静。移动端只保留紧凑用量文本和进度条，不能挤压标题或操作按钮。

## 5. 工作台布局

### 5.1 高度模型

根视图使用 `100dvh`，整体禁止页面级滚动。工作台纵向结构为：

```text
全局头部（紧凑屏幕）
计划横幅（条件渲染）
工作台主体 minmax(0, 1fr)
  会话标题栏
  消息滚动区 minmax(0, 1fr)
  Composer auto
```

`pane-center`、`chat-shell`、消息滚动区都必须具备 `min-height: 0`。Composer 是 grid 最后一行并始终贴住工作台底边，不使用覆盖消息的 fixed 定位。消息区底部 padding 仅提供正常视觉间距，不模拟 Composer 高度。

### 5.2 宽度和密度

- 消息内容和 Composer 共用一个 `--chat-content-max: 1120px` 宽度约束。
- 桌面正文使用开放画布，不把整个页面包进装饰卡片。
- 用户消息右对齐为紧凑气泡；助手正文左对齐。
- 空会话状态在剩余消息区内居中，不通过固定 `48vh` 人为撑出空白。
- 390 x 844 下单栏显示，Composer、发送按钮、模型选择和正文均不得横向溢出。

## 6. 消息投影和运行状态

### 6.1 乐观用户消息

发送时立即创建带稳定 client id 的本地用户消息，并设置运行状态为“思考中”。timeline 收到对应 `run_started/user` 事件后，用服务端事件替换该本地消息，不能先删除再添加，也不能重复显示。

去重优先使用后端可关联的 run id；在 run id 尚未返回的窄窗口内，使用当前 session 的单个 pending optimistic message。Sage 当前同一 session 同时只允许一个 active run，因此不需要维护并发 pending 队列。

### 6.2 顺序规则

一个活动回合的渲染顺序固定为：

1. 用户消息。
2. `CodingThinkingIndicator`，文案为当前运行阶段，但不出现“准备执行”或“执行过程”这类漂浮到错误列的独立标签。
3. 工具调用时间线及审批。
4. 流式助手正文和最终回答。

服务端 timeline 仍是历史记录的唯一事实源。乐观消息仅用于补齐事件往返延迟，不写入持久记录。

## 7. 粘性自动贴底

消息区维护 `followOutput`：

- 初始化、新 session、用户主动点击回到底部或距离底部小于 80px 时为 `true`。
- 用户滚动离开底部超过阈值时为 `false`。
- `followOutput` 为 `true` 时，用户消息、思考阶段、工具状态、参数/结果增长、审批和流式正文变化均在 DOM 更新后的 animation frame 贴底。
- `followOutput` 为 `false` 时不改变 `scrollTop`，显示“回到底部”入口；新增完整消息时累计未读数，纯流式增量也显示入口但不虚增消息数。
- 组件卸载、session 切换和重复调度时取消旧 RAF，避免写入已销毁节点或旧 session。
- 加载更早记录继续使用锚点恢复，不触发贴底。

## 8. `run_shell` 策略

现有正则只要在任意 `;`、`&&` 或 `||` 后发现 `ls`、`head` 等命令就拒绝，误伤 `echo ...; pwd; ls -la` 和版本/测试验证。

新规则只在整条命令的主要目的明确是单一普通 workspace 读取/搜索时拒绝：

- 拒绝：`grep -R alpha .`、`rg pattern src`、`ls -la`、`find . -name '*.py'`、`cat app.py`。
- 允许：`pytest -q | tail -5`、`python3 --version; pwd; ls -la`、`echo Hello; pwd; ls -la`、构建或测试命令后的结果截取。
- 用 shell 运算符分段后，只要存在非读取段，就视为复合验证命令并允许；管道右侧的 `head`/`tail` 不单独触发拒绝。
- 该优化规则不是安全边界。危险命令识别、审批、workspace 路径和权限模式继续由原有层处理。

## 9. 工具时间线和代码面板

- 每个工具调用默认以可折叠行展示工具名、状态和耗时/结果摘要。
- 活动工具默认展开，完成工具可收起；用户展开状态不应被流式更新重置。
- 参数和结果分别使用带标题栏的面板，标题显示 `参数 · JSON`、`结果 · TEXT/JSON`，右侧为图标复制按钮并有 tooltip。
- 参数使用 JSON 稳定格式化；结果若可解析为 JSON 则格式化并高亮，否则按纯文本保留换行。
- 面板使用现有 `highlight.js` 能力，不新增自写语法解析器。
- 工具调用以细竖线形成连续时间线，不能漂浮到消息列左侧或页面中央。
- 默认只展示对用户有意义的工具过程；内部 parser/model 生命周期事件继续隐藏。

## 10. 错误处理

- TOML 配置错误在应用启动时给出包含文件路径和字段原因的异常，不记录 API key 等敏感信息。
- 模型切换失败不更新前端当前模型。
- 上下文刷新失败保留最近一次成功快照；显式压缩失败继续显示可操作错误。
- 复制失败不影响展开状态，并通过按钮状态或现有消息桥提示。
- timeline 重连后由服务端投影替换乐观状态；若 run 创建失败，本地消息保留并在其后显示错误，便于用户重试。

## 11. 测试与验收

### 11.1 后端

- TOML：默认文件、覆盖路径、重复 id、未知字段、无效窗口、默认模型缺失、reasoning 枚举。
- 模型 API：目录和 capability 来自同一清单，未知模型没有上下文能力。
- runtime：模型切换后上下文控制器按清单重建或禁用。
- shell policy：单一读取拒绝，复合版本/验证/测试命令允许，原审批和危险命令测试不回归。

### 11.2 前端

- 用户消息在思考状态之前立即出现，timeline 到达后不重复、不闪退。
- 上下文摘要显示使用量、总量、剩余量和进度条；未配置时无占位。
- 工作台根、消息区和 Composer 高度约束在桌面与移动端成立。
- 自动贴底覆盖流式正文、工具结果和思考阶段；主动上滚后不抢滚动，入口可恢复贴底。
- 工具参数/结果可展开、复制并保留 JSON/纯文本格式。
- JSON、Java、Python fenced code 保持第三方语法高亮。

### 11.3 门禁和真实页面

- 前端全量测试。
- 后端 context/runtime/tool/API 定向测试。
- 前端生产构建。
- `git diff --check`。
- Logic Lens 审查 TOML 输入、shell 策略、消息投影和 RAF 清理。
- Browser 在桌面和 390 x 844 验证页面身份、非空、无框架错误、console 健康、关键交互和截图。
- 至少实测：新会话发送顺序、正常 `run_shell`、流式贴底、手动上滚、上下文摘要和 fenced code。

## 12. 合并与收口

实现分支在隔离 worktree 完成全部门禁后，审查与 `dev/sage-v6` 的共同祖先、共享 API/store 和未提交文件。可 fast-forward 时合入 `dev/sage-v6`，在集成 worktree 重跑受影响验证。合并后更新 Obsidian `sage-learning`，记录 source commit、验证证据、关闭风险、遗留的 reasoning provider 参数契约和下一阶段边界。只有短期分支已被集成分支包含且 worktree 干净时才删除。
