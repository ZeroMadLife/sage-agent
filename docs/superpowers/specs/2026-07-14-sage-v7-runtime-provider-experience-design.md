# Sage V7 运行轨迹、动态角色与 Provider 体验设计

## 1. 背景

Sage V6.9 已完成聊天主流程、工具审批、上下文预算、Provider 非敏感配置和用量统计，并已合入 V7。当前仍有三类上线前问题：

1. 一个回合内的工具参数和结果逐项展开，长任务会占满消息区；完成后缺少紧凑、可追溯的运行摘要。
2. “正在思考”仍像普通头像状态，角色本身没有动作，不能形成稳定的 Sage 产品识别。
3. Provider 与模型入口分离，首次进入聊天时模型可能为空；云端用户也不能安全地配置自己的 API Key、Base URL 和 API 格式。

本设计选择“审计摘要闭环”方案：保留完整运行证据，以一个默认折叠的回合级运行面板呈现；使用用户提供的小女孩原图制作真正的多帧角色动画；为 V7 增加账号级加密 Provider 凭据和统一模型入口。

## 2. 目标与非目标

### 2.1 本阶段交付

- 每个回合只显示一个工具运行面板，默认折叠。
- 运行中在折叠标题中显示当前工具；完成后显示确定性审计摘要。
- 展开一次即可查看该回合全部工具步骤，不再要求逐项点击。
- 长参数和长输出只显示有界首尾预览，并明确标记截断。
- 使用多帧状态资源让小女孩本身眨眼、呼吸、改变视线和姿态。
- 合并 Provider 与模型设置，支持账号级自定义 API Key、Base URL 和模型。
- 首版支持 `openai_chat_completions`、`openai_responses`、`anthropic_messages`。
- 修复首次进入聊天时默认模型未初始化，必须进出设置才显示的问题。

### 2.2 明确不做

- 不展示、生成或持久化模型内部思维链。
- 不把“运行摘要”伪装成模型 reasoning；摘要只来自可验证事件。
- 不在浏览器回显已保存的 API Key，也不把 Key 写入 timeline、日志或 `.sage/settings.json`。
- 不在本阶段引入完整桌宠、拖拽宠物、Petdex 或通用动画商城。
- 不在本阶段重做“查看变更”抽屉。
- 不在本阶段实现工作区下的会话隔离；该功能在本阶段合入 V7 后单独立项。

## 3. 方案比较与结论

### 3.1 纯前端折叠

只调整 Vue 展示和 CSS。交付快，但跨刷新摘要不稳定，无法形成云端 Provider 闭环。

### 3.2 审计摘要闭环（选定）

服务端从持久化 timeline 派生有界审计摘要，前端按回合聚合展示；账号级凭据由服务端加密保存。它复用现有 run store、workspace diff 和 V7 加密基础设施，能够在不引入独立运行中心的前提下支持部署。

### 3.3 完整运行控制台

新增独立 Run 页面、日志制品和下载系统。扩展性最高，但会把 V7 上线前收束扩大成新的运行平台，本阶段不采用。

## 4. 聊天运行体验

### 4.1 回合顺序

一个活动回合保持以下顺序：

1. 用户消息。
2. 动态 Sage 角色与当前阶段。
3. 一个回合级运行面板或审批卡。
4. 助手流式正文和最终回答。
5. 完成后的运行审计摘要。

审批卡保持直接可操作，不能藏进折叠面板。批准后，同一工具步骤从 `waiting` 进入 `running`，不能生成重复步骤。

### 4.2 单一运行面板

新增 `CodingRunTrace`，取代每条工具单独展开的布局。

折叠标题在运行中显示：

```text
正在执行 · run_shell · 18s
```

完成后显示：

```text
运行过程 · 3 项 · 全部完成 · 修改 2 个文件 · 42s
```

面板默认关闭。用户展开一次后看到按时间排序的全部工具步骤，每步直接显示：工具名、可读动作、状态、耗时、参数预览、结果预览和截断标记。步骤内部不再增加第二层 disclosure。

页面级“显示工具过程”偏好改为“显示运行摘要”：关闭时隐藏历史摘要，但当前审批和当前运行状态仍可见。

### 4.3 审计摘要

服务端在读取 run 时从既有事件派生 `CodingRunAuditSummary`：

```text
run_id
status
headline
tool_count
completed_tool_count
failed_tool_count
approval_count
duration_ms
changed_files[]
steps[]
```

每个 `step` 包含：

```text
tool
status
action_summary
result_summary
duration_ms
arguments_preview
result_preview
arguments_truncated
result_truncated
```

摘要必须是确定性投影：工具名、参数中的安全字段、exit code、错误状态、持续时间和 workspace diff。禁止为摘要额外调用模型，禁止读取或保存 reasoning 内容。

既有 trace 继续作为完整事实源。本阶段不改变原始证据持久化格式，只限制摘要响应和 UI 展示：参数与结果预览各不超过 4 KiB，超限时保留首 3 KiB、尾 1 KiB和省略字节数。

## 5. 动态 Sage 角色

### 5.1 资源策略

角色必须使用用户提供的小女孩原图作为唯一身份基准。不是让圆形头像整体漂浮，而是制作透明背景的多帧 sprite sheet，让人物内部发生动作。

状态行固定为：

- `idle`：轻微呼吸和自然眨眼。
- `thinking`：视线游移、眨眼、发卡微光和思考气泡。
- `tool`：眼神聚焦、手部或肩部有轻微工作动作。
- `waiting`：动作暂停并出现暖黄色等待提示。
- `done`：短暂确认动作后回到 idle。
- `failed`：短暂受阻动作，随后保持可读错误状态。

每行 6 帧，统一 `frameW`、`frameH` 和透明背景。资产制作必须以身份一致性为验收门槛；如果生成帧改变发色、眼睛、服装、发卡、线条或比例，则拒绝该帧，不能用“相似角色”替代。

### 5.2 渲染组件

新增 `SageThinkingCharacter.vue`，使用 Canvas 从 sprite sheet 逐帧绘制。组件只接收 `state`、`phase` 和 `reducedMotion`，不读取 store 内部状态。

- 状态变化时从当前循环安全切到目标行。
- `prefers-reduced-motion: reduce` 时只显示该状态首帧。
- 组件有固定尺寸，不因文案、帧尺寸或加载状态导致消息布局跳动。
- 图片加载失败时退化为静态原图和文本状态，不影响聊天流程。
- 动画不出现在 transcript、timeline 或审计记录中。

Hermes 的可复用思想仅限“状态行 × 多帧 × Canvas 绘制”；不复制其源码、资源、类名或桌宠系统。

## 6. Provider 与模型统一入口

### 6.1 账号级所有权

云端 Provider 配置归属于登录用户，可跨工作区复用。工作区只在后续阶段保存默认 Provider/模型覆盖；当前阶段支持账号默认和会话内切换。

GitHub OAuth 凭据与 LLM Provider Key 必须使用不同表和用途标识，避免身份凭据与推理凭据混用。新增：

- `CloudModelProviderRecord`：用户、名称、API 格式、Base URL、加密 Key、Key 提示、状态和时间戳。
- `CloudModelRecord`：Provider、模型 ID、显示名、上下文窗口、输出预留和 reasoning 能力。
- `CloudModelPreferenceRecord`：用户级默认 Provider/模型。

Key 使用现有 `SecretCipher` 与独立 purpose 加密。创建和更新响应只返回 `key_configured`、`key_hint`、`last_tested_at` 和连接状态。

### 6.2 API 格式

首版只允许：

- `openai_chat_completions`
- `openai_responses`
- `anthropic_messages`

运行时不能把解密后的 Key 写入全局环境变量。请求创建 Provider client 时通过短生命周期的结构化 credential 显式传入，调用结束后不进入日志、异常详情或 timeline。

### 6.3 API

新增账号级接口：

```text
GET    /api/v1/cloud/model-providers
POST   /api/v1/cloud/model-providers
PATCH  /api/v1/cloud/model-providers/{id}
DELETE /api/v1/cloud/model-providers/{id}
POST   /api/v1/cloud/model-providers/{id}/test
POST   /api/v1/cloud/model-providers/{id}/discover-models
PUT    /api/v1/cloud/model-default
```

`test` 使用最小非生成请求验证认证和协议。`discover-models` 优先调用兼容模型目录；失败时返回可读错误，但不阻止用户手工添加模型。

本地未登录模式继续支持 `.sage/settings.json` 和服务端环境变量。云端登录用户优先使用账号级配置，不能让项目文件覆盖账号密钥。

### 6.4 界面

设置页合并“Provider”和“模型”为“模型与 Provider”。

- 列表以 Provider 为一级实体，模型作为 Provider 内部资源。
- “添加 Provider”弹窗包含名称、Base URL、API Key、API 格式和默认模型。
- 保存前提供连接测试；保存后 Key 输入框保持空白，只显示已配置提示。
- 模型发现失败时显示手工模型表单和上下文长度输入。
- Composer 模型菜单按 Provider 分组，显示连接状态、上下文和 reasoning 能力。

## 7. 首次进入模型初始化

当前 `loadModels()` 依赖 session 初始化，Provider 数据又主要由设置页加载，导致 Composer 可能先渲染空模型。

新增幂等 `bootstrapModelCatalog()`：

1. Coding 路由挂载即加载账号或本地 Provider 目录，不等待 session。
2. 目录返回账号默认模型，立即填充 Composer。
3. session 创建或恢复后，用 session 固化模型覆盖账号默认。
4. 切换 session 时只更新该 session 的模型状态，不污染其他 session UI state。
5. 设置页复用同一 bootstrap，不再承担“修复聊天模型状态”的副作用。

## 8. 错误与安全边界

- Provider Key 创建、更新、测试和运行接口均要求已认证用户。
- 所有 Provider 查询按 `user_id` 过滤，跨用户 ID 返回 404。
- Base URL 只允许 `https`；开发模式可显式允许 loopback `http`，禁止云端 SSRF 到私网和 metadata 地址。
- API 错误不包含请求头、Key、完整上游响应或解密异常详情。
- 删除当前默认 Provider 前必须先切换默认值，或由用户确认同时清除默认。
- 运行摘要中的参数按工具字段策略脱敏，不能显示环境变量、授权头或敏感文件内容。
- 动画资源失败、Provider 目录失败和工具摘要失败均应局部降级，不能让聊天白屏。

## 9. 测试与验收

### 9.1 后端

- 审计摘要对成功、失败、拒绝、重复工具名、长输出和 workspace diff 的投影测试。
- 账号级 Provider CRUD、跨用户 404、Key 不回显、密文落库和更新不传 Key 时保留原密钥。
- 三种 API 格式的 client 路由测试。
- Base URL SSRF 约束、连接测试错误脱敏和模型发现回退测试。
- 默认模型、session 固化和本地配置兼容测试。

### 9.2 前端

- 一个回合只有一个默认折叠的运行面板。
- 展开一次显示全部工具步骤，长输出显示首尾和截断信息。
- 当前审批始终可见，批准后不重复工具步骤。
- 动画状态随 `thinking / tool / waiting / done / failed` 切换。
- reduced motion、资源失败和移动端固定尺寸测试。
- 首次进入聊天无需打开设置即可显示默认模型。
- Provider 弹窗、连接测试、模型发现、手工回退和 Key 不回显测试。

### 9.3 浏览器验收

- 桌面与 `390 × 844` 无横向溢出或 Composer 跳动。
- 发送消息后角色本身发生动作，工具面板默认折叠。
- 执行安全 shell 并授权，完成后摘要、耗时、exit code 和变更文件正确。
- 刷新 session 后摘要仍可追溯。
- 新建 Provider 后 Composer 立即显示默认模型，无需进出设置。
- 浏览器控制台无相关 error/warn。

## 10. 交付顺序

1. 运行审计摘要契约与单一运行面板。
2. 多帧角色资产、Canvas 组件和状态映射。
3. 账号级 Provider 数据模型、加密 API 和 Provider client 注入。
4. Provider/模型统一设置与首次进入初始化修复。
5. 全量门禁、浏览器验证、V7 合入和 Obsidian 复盘。
6. 另开阶段实现工作区下的会话隔离。

## 11. 完成定义

只有以下条件同时满足，才能认定本阶段完成：

- 工具过程默认折叠、单回合单面板、完成摘要可刷新追溯。
- 小女孩角色内部动作通过视觉验收，不是整体头像位移。
- API Key 服务端加密且任何响应、日志、timeline 和前端状态都不回显明文。
- 首次进入聊天显示账号默认模型。
- 三种 API 格式有契约与测试覆盖。
- 前后端测试、生产构建、静态检查、`git diff --check` 和真实浏览器用例通过。
- 短期分支合入 V7，目标 worktree 干净，并更新 `sage-learning`。
