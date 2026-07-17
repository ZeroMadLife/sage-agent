# Sage V6.9 Provider、真实推理与用量收束设计

## 1. 背景与目标

V6.9 已完成聊天时间线、恢复、上下文投影、工具过程和基础 Hermes 风格迁移。本次收束继续完成开发工作台的四个用户可见缺口：

1. Composer 需要采用更紧凑、低干扰的专业工作台层级，且不重新引入底部空白。
2. “正在思考”应当明确表示 Sage 正在运行，能够展示阶段和时长，但不能伪装或泄露模型内部思维链。
3. Provider、模型、推理档位和项目指令需要由 Sage 管理，并具有类似 Claude Code 的根目录 `.sage/` 配置入口。
4. 用量页面必须基于 Provider 实际返回的 token 元数据，而不是以前端估算代替真实统计。

视觉参考采用用户提供的 Hermes Studio 截图的信息架构与交互密度，不复制 Hermes Studio 源码、CSS、类名或素材。实现保持 Vue 3、Pinia、FastAPI、现有 coding runtime 和 timeline contract 的独立边界。

## 2. 范围与非目标

### 2.1 本阶段交付

- Composer 改为“输入区 + 右上 context budget + 底部 command rail”结构。
- 运行状态使用 Sage 专属的小女孩头像、阶段、秒数和低干扰流光，不显示 chain-of-thought。
- 新增 `.sage/settings.json` 非敏感 Provider/模型配置、`.sage/SAGE.md` 项目指令入口和遗留 TOML 兼容读取。
- 新增 Provider 设置页，显示 API Key 环境变量是否已配置，并允许编辑非敏感 Provider 配置。
- 新增模型 reasoning 选择；只有已声明且后端已映射的 Provider 契约才可选择并真正传入模型请求。
- 新增本地 SQLite 用量账本、聚合 API 和“设置 -> 用量”页面。
- 保持现有 Markdown、JSON/Java/Python fenced code 高亮、工具时间线、自动贴底和权限审批行为。

### 2.2 明确不做

- 不显示、存储、转发或总结模型原始思维链。
- 不让浏览器输入、保存、回显或复制 API Key。
- 不实现 Hermes 截图中尚未被 Sage 后端验证的 `responses`、Bedrock、App Server 等协议。
- 不为当前未验证的 DeepSeek V4 Flash/Pro 添加 reasoning 开关。
- 不伪造 token、缓存命中率或费用；Provider 没有返回时明确显示“未提供”或 `--`。
- 不改变危险命令审批、权限模式、路径约束、计划模式、timeline 租约和历史恢复契约。
- 不把 V7 多租户、云密钥托管、服务器部署或 CI/CD 混入本次 V6.9 分支。

## 3. 配置边界

### 3.1 目录和文件

项目根目录的 `.sage/` 为本地工作台配置入口：

```text
.sage/
  settings.json       # 非敏感 Provider、模型、协议、上下文和 reasoning 能力
  SAGE.md             # 项目级 Sage 指令
  usage.sqlite3       # 本地用量账本，不提交
```

- `settings.json` 不含 API Key、access token、Cookie、请求头或用户消息。它只允许声明 `api_key_env`，例如 `DEEPSEEK_API_KEY`。
- `SAGE.md` 是项目指令。runtime 的提醒加载顺序固定为 `.sage/SAGE.md`、根目录 `SAGE.md`、根目录 `AGENTS.md`；同一条指令只按该顺序拼接一次，单文件最多读取 12,000 字符。
- `usage.sqlite3`、其 `-wal` 和 `-shm` sidecar 加入 `.gitignore`。它只保存可聚合的用量元数据，不保存 prompt、completion、工具结果或密钥。

### 3.2 配置来源与兼容

应用启动时按以下顺序确定 Provider 配置：

1. 管理员显式设置的 `SAGE_CODING_SETTINGS_FILE` JSON 路径。
2. coding workspace 根目录的 `.sage/settings.json`。
3. 旧 `SAGE_CODING_MODELS_FILE` 或仓库 `config/coding_models.toml`，只读兼容模型目录和上下文能力。

第一、二项使用严格 JSON schema；第三项保持现有严格 TOML manifest。若 JSON 文件存在但无效，应用启动失败，不回退到猜测的模型配置。部署环境变量指定的外部配置被标记为“部署托管”，网页只能查看脱敏后的目录，不能覆盖该文件。

设置 API 只读写当前 coding workspace 根目录的 `.sage/settings.json`，不接收浏览器传来的文件路径，不跟随 symlink，也不允许越过 workspace root。首次在本地 UI 保存时，以现有 TOML 目录为基础生成该 JSON，确保升级不丢失当前可选模型。

### 3.3 `settings.json` 结构

```json
{
  "version": 1,
  "default_model": "deepseek:deepseek-v4-flash",
  "providers": [
    {
      "id": "deepseek",
      "label": "DeepSeek",
      "api_mode": "openai_chat_completions",
      "base_url": "https://api.deepseek.com/v1",
      "api_key_env": "DEEPSEEK_API_KEY",
      "models": [
        {
          "id": "deepseek:deepseek-v4-flash",
          "label": "DeepSeek V4 Flash",
          "context_window_tokens": 1000000,
          "output_reserve_tokens": 64000,
          "reasoning": { "kind": "unsupported" }
        }
      ]
    }
  ]
}
```

严格校验规则：

- 根字段固定为 `version`、`default_model`、`providers`；未知字段、重复 provider/model id 和空字符串都拒绝。
- Provider id 与模型 id 采用受限标识符；`api_key_env` 只接受环境变量名格式，不接受值。
- `base_url` 仅允许 `https`，本地开发显式允许 loopback `http`。禁止 userinfo、fragment 和控制字符。
- `api_mode` 仅允许 `openai_chat_completions`、`anthropic_messages`。未实现的协议不写入可选列表。
- 每个模型有且仅有一个 provider 前缀，`default_model` 必须指向已声明模型。
- `context_window_tokens` 与 `output_reserve_tokens` 必须同时出现，且 reserve 小于 window。
- JSON 格式化、原子写入和文件权限收紧均在服务端完成；写入错误保持旧文件不变。

## 4. Provider 与真实 reasoning

### 4.1 支持矩阵

| API 模式 | 客户端 | 可支持的真实 reasoning | V6.9 行为 |
| --- | --- | --- | --- |
| `openai_chat_completions` | `ChatOpenAI` | `reasoning_effort=low|medium|high`，仅模型声明该 kind 时 | 请求创建时传入 `reasoning_effort`，并启用响应 usage 流 |
| `anthropic_messages` | `ChatAnthropic` | `thinking` budget 或 `effort`，必须由模型声明固定映射 | 按声明传入 thinking/effort；thinking content 不进入聊天 UI |

当前 `config/coding_models.toml` 中 DeepSeek Flash/Pro 的 `reasoning_modes = []` 保持不变。因此它们在 V6.9 默认仍显示“未支持”，不会因为加入 UI 而产生伪推理开关。

### 4.2 能力声明

每个模型使用一个明确的 reasoning descriptor，禁止使用“模型名称包含 reason”之类的推测：

```json
{ "kind": "openai_reasoning_effort", "modes": ["low", "medium", "high"] }
```

或：

```json
{
  "kind": "anthropic_thinking_budget",
  "budgets": { "low": 1024, "medium": 4096, "high": 8192 }
}
```

`unsupported` 是默认状态。schema 将 descriptor 转换为现有模型目录的 `reasoning_modes`，使 `/models` 继续只返回可真实选择的枚举。模型、协议和 descriptor 任一不兼容时，配置加载失败。

### 4.3 会话选择与请求链路

新增 `reasoning_mode` 会话字段，默认 `off`。前端仅为当前模型声明的档位显示分段控制；切换模型时若所选档位不再支持，服务端原子地回退为 `off` 并返回最终状态。活动 run、上下文压缩或恢复期间拒绝切换。

```text
Composer
  -> PATCH /coding/{session}/reasoning
  -> CodingRuntime 持久化 model_spec + reasoning_mode
  -> model factory(model_id, reasoning_mode)
  -> ChatOpenAI / ChatAnthropic 的已验证参数
  -> Engine astream/ainvoke
  -> response usage normalizer
  -> UsageStore（request_id = run_id + model attempt，幂等）
```

runtime 与 worker 创建模型时使用当前 session 的 `model_id + reasoning_mode`，不能让后台 worker 悄悄回落到默认模型。模型响应中的 thinking block 只用于 Provider 协议完成，不进入 `TextDeltaEvent`、transcript、timeline、run trace、日志或 UsageStore。

## 5. 用量账本与 API

### 5.1 记录方式

`UsageStore` 保存到 `.sage/usage.sqlite3`。每次实际模型请求结束后，从 LangChain message/chunk 的 `usage_metadata` 或 `response_metadata.token_usage` 归一化以下字段：

- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_creation_tokens`
- `total_tokens`（仅 Provider 明确返回时）

每一条记录以 `request_id = <run_id>:<model_attempt>` 为主键。重复流式尾包、重连或同一事件重放执行 `INSERT OR IGNORE`，不会重复计费或重复累计。请求没有任何可信 usage 字段时不创建全零记录。

账本记录 `request_id`、session id、run id、provider id、model id、时间和 token 数值/可用性；绝不保存模型内容、完整 URL、环境变量值或响应 headers。数据库使用 WAL、busy timeout、原子建表和范围受限查询；读写异常不会中断用户模型回复，但会以受控日志记录错误类型。

### 5.2 聚合接口

新增只读接口：

```text
GET /api/v1/coding/usage?range=7d|30d|90d|365d
```

返回总输入/输出/缓存、真实字段可用性、不同模型分布、每日用量和 distinct session 数。没有 Provider usage 时指标字段为 `null`，而不是 `0`。费用仅当配置中存在显式、版本化的价格表并覆盖该模型时计算；默认 `cost` 为 `null`，页面显示 `--`。

## 6. 前端体验

### 6.1 Composer

Composer 保持现有满高工作台中的底部行，不使用 `fixed` 覆盖消息区。结构为：

```text
输入文本区
  右上：已用 / 总窗口 / 剩余量 + 进度条
底部 command rail
  附件入口 | 权限模式 | 设置入口 | 模型选择 | reasoning（有能力时） | 发送/停止
```

输入区最小高度稳定，command rail 的按钮尺寸稳定；窄屏自动折叠次级文案，不能使模型名称或发送按钮溢出。context 未配置时不显示“模型未配置”占位。现有 slash skill 菜单、Enter 发送、Shift+Enter 换行、停止和权限抽屉继续工作。

### 6.2 运行头像与工具过程

`CodingThinkingIndicator` 改为左对齐的 Sage 小女孩头像、`正在思考`、阶段和经过秒数。视觉气质采用用户确认的软萌金发 Q 版、轻松回应感；角色使用 Sage 原创面部、服装与标志元素，不直接复刻菲比或其他现有角色。流光仅覆盖状态条，遵守 `prefers-reduced-motion`。头像使用 Sage 自主生成或明确授权的位图资源，圆形裁切由 UI 完成；不裁切 Hermes 截图中的角色。

这不是 reasoning 内容面板。它只表达 run 的公开阶段，例如“正在请求模型”“正在执行工具”“等待审批”。固定的视觉顺序仍为：用户消息 -> 运行状态 -> 工具/审批 -> 流式助手正文 -> 最终回答。工具参数与结果继续可展开、可复制；实际思维块不会作为工具或 Markdown 渲染。

### 6.3 设置页

`SettingsView` 新增：

- **Provider**：卡片展示 API 模式、base URL、默认模型、context、可选 reasoning 档位和 `API Key 已配置/未配置`。不显示 key 本身。
- **模型**：保留会话模型选择，并显示已验证的 reasoning 能力。
- **用量**：范围切换、总 token、会话数、缓存命中率、模型分布和每日用量。没有真实数据时展示安静的空状态。

Provider 编辑器只接收 label、base URL、API mode、环境变量名、模型和显式能力。保存前服务端重跑 schema 校验并重新构建 catalog/capability registry；失败时当前运行配置保持不变。出于 V6.9 的单工作区边界，编辑配置仅影响后续新建/切换模型和后续 run，不在活动 run 中热切换。

## 7. 错误处理和安全

- 未设置 `api_key_env` 时，Provider 卡片显示未配置；实际调用返回不含 key 的可操作错误。
- 任何 API 响应、WebSocket event、timeline、测试快照和日志都不得出现 API Key 或环境变量值。
- Provider 编辑 API 使用枚举、长度限制、严格 schema 和 workspace 根路径固定，不接受任意文件路径或请求 headers。
- reasoning 请求参数无法被所选 API mode 消费时，在配置加载或会话切换时失败，不能静默忽略。
- usage 解析失败、SQLite 锁定或 Provider 没有 usage 时不影响回复；页面以可区分的“未提供/暂不可用”展示。
- 代码块继续复用 `markdown-it + highlight.js`；不得引入自制 JSON/Java/Python 语法解析器。

## 8. 测试与验收

### 8.1 后端

- JSON schema：未知字段、重复 id、非法 base URL、非法 env 名、无效 context、错误 default model、无效 reasoning descriptor、JSON 与旧 TOML 回退顺序。
- 模型工厂：OpenAI `reasoning_effort` 和 Anthropic thinking/effort 映射只在声明时传递；DeepSeek 默认无参数；不支持模式拒绝。
- runtime：reasoning selection 持久化、模型切换回退、活动 run 拒绝、worker 继承当前选择。
- usage：流式和非流式 metadata 正规化、没有 usage 不写零记录、同一 `request_id` 幂等、日/模型聚合、范围校验、无价格表时费用为空。
- API：Provider 脱敏响应、禁止 key 字段、部署托管只读、配置写入原子失败不污染当前 registry。

### 8.2 前端

- Composer 的 context 摘要、command rail、模型/推理选择和窄屏布局。
- reasoning 只有模型声明时出现；切换到不支持模型后回到 `off`。
- 头像状态显示阶段与秒数，且不渲染 reasoning 文本；减少动画偏好关闭流光。
- Provider 页不渲染 API Key 输入框或值；用量空态、真实数据、未知费用和范围切换。
- 保留工具展开/复制、自动贴底、上滚不抢滚、Markdown/代码高亮回归。

### 8.3 真机与门禁

- 浏览器验证桌面和 390 x 844：满高工作台、Composer、不重叠、运行态、工具展开、Provider 只读/编辑、用量空态与有数据态。
- 使用一个真实声明支持的测试 Provider 做请求参数和 usage smoke；若本地没有该 Provider credential，使用不含密钥的 mock contract 测试，不能宣称远端能力已验证。
- 前端全量测试、生产构建、受影响后端测试、`ruff`、`mypy`、`compileall`、`git diff --check`。
- Logic Lens 审查 JSON 输入、环境变量边界、运行态信息泄露、UsageStore 幂等性、SQLite 路径与 API 脱敏。

## 9. 合并与收口

在隔离分支完成实现后，先自审 V6.9 功能是否已实际交付，不把设计或 mock 当成完成。随后：

1. 审查与 `dev/sage-v6` 的共同祖先、共享 API/store、工作树和未提交文件。
2. 通过门禁后合入 `dev/sage-v6`，在集成 worktree 重跑受影响验证。
3. 更新 Obsidian `sage-learning`，记录 source commit、验证证据、关闭风险和遗留边界。
4. 只有短期分支被集成分支包含且工作树干净后才删除。

V6.9 完整收束后，再将已验证的集成提交合入独立 V7 线。V7 后续才处理服务器 Web 部署、CI/CD、多租户与服务端密钥托管。
