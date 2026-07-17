# Sage V7 运行轨迹、动态角色与 Provider 体验实施计划

**目标：** 将每个聊天回合的工具过程收敛为一个默认折叠、可刷新追溯的审计摘要；让用户指定的小女孩角色本身呈现状态动画；为 V7 增加账号级加密 Provider/模型配置，并修复聊天首屏模型目录初始化。

**架构：** `RunStore` 继续保存原始 trace，并从事件确定性投影有界 `CodingRunAuditSummary`。聊天页按 `run_id` 聚合 timeline，只渲染一个 `CodingRunTrace`。动画资源采用状态行 sprite sheet，由独立 Canvas 组件播放。云端 Provider 使用独立 owner-scoped 数据表和 `SecretCipher`，运行时把解密凭据显式注入短生命周期客户端，不写入环境变量、timeline 或前端状态。

**技术栈：** Python 3.11、FastAPI、SQLAlchemy、Pydantic、AES-GCM、LangChain OpenAI/Anthropic、Vue 3、Pinia、Vitest、Canvas、Highlight.js。

---

### Task 1: 运行级审计摘要契约

**文件：**
- 修改：`core/coding/persistence/run_store.py`
- 修改：`api/schemas.py`
- 修改：`frontend/src/types/api.ts`
- 测试：`tests/core/coding/test_run_store.py`
- 测试：`tests/api/test_coding_routes.py`

- [ ] 先覆盖成功、失败、审批、重复工具名、长参数/输出、敏感字段和 workspace diff。
- [ ] 从 `tool_call` / `tool_result` / `approval_*` / `run_finished` 派生步骤、耗时和 headline；不调用模型，不读取 reasoning。
- [ ] 参数和结果预览限制为 4 KiB，保留首 3 KiB、尾 1 KiB和省略长度。
- [ ] `list_runs()` 与 `get_run()` 返回同一审计摘要，原始 `events` 保持兼容。

### Task 2: 单一运行面板与回合顺序

**文件：**
- 新增：`frontend/src/components/coding/chat/CodingRunTrace.vue`
- 修改：`frontend/src/components/coding/chat/CodingMessageTurn.vue`
- 修改：`frontend/src/views/CodingView.vue`
- 修改：`frontend/src/stores/coding.ts`
- 修改：`frontend/src/stores/codingTimeline.ts`
- 修改：`frontend/src/components/coding/index.ts`
- 测试：相关组件/store 测试

- [ ] 每个 `run_id` 只渲染一个默认折叠面板，标题显示当前工具或完成摘要。
- [ ] 展开一次直接显示全部步骤，不再逐项 disclosure；预览保持有界并可复制。
- [ ] 保持“用户消息 -> 角色状态 -> 审批/运行面板 -> 助手正文”的顺序。
- [ ] 历史摘要可由顶部偏好隐藏，但当前运行和审批始终可见。

### Task 3: 动态 Sage 角色

**文件：**
- 新增：`frontend/src/assets/sage-thinking-sprite.png`
- 新增：`frontend/src/assets/sage-thinking-fallback.png`
- 新增：`frontend/src/components/coding/chat/SageThinkingCharacter.vue`
- 修改：`frontend/src/components/coding/chat/CodingThinkingIndicator.vue`
- 测试：`frontend/src/components/coding/chat/SageThinkingCharacter.test.ts`
- 测试：`frontend/src/components/coding/chat/CodingThinkingIndicator.test.ts`

- [ ] 以用户指定原图为身份基准生成 `idle/thinking/tool/waiting/done/failed` 状态帧。
- [ ] Canvas 播放状态行，人物内部眨眼、视线、呼吸或姿态变化；禁止只移动头像容器。
- [ ] 固定尺寸、资源失败静态回退、`prefers-reduced-motion` 首帧回退均有测试。

### Task 4: 账号级 Provider 存储与 API

**文件：**
- 修改：`db/models.py`
- 修改：`db/migrations.py`
- 新增：`core/cloud/model_providers/`
- 修改：`core/config/settings.py`
- 修改：`api/schemas.py`
- 新增：`api/cloud_model_providers.py`
- 修改：`api/main.py`
- 测试：`tests/core/cloud/model_providers/`
- 测试：`tests/api/test_cloud_model_provider_routes.py`

- [ ] 新建 Provider、Model、Preference 表，全部按 authenticated `user_id` 隔离；不复用 GitHub OAuth 表。
- [ ] Key 使用独立 purpose 的 AES-GCM 密文落库，响应只返回 `key_configured`/`key_hint`。
- [ ] 实现 CRUD、连接测试、模型发现和账号默认模型 API；跨用户 ID 统一 404。
- [ ] Base URL 云端仅 HTTPS 并阻断私网/metadata，开发模式只显式允许 loopback HTTP。
- [ ] 上游错误统一脱敏，不返回请求头、Key 或完整认证响应。

### Task 5: Provider 客户端显式注入

**文件：**
- 修改：`core/llm.py`
- 修改：`core/llm_openai.py`
- 修改：`core/llm_anthropic.py`
- 修改：`core/coding/runtime.py`
- 修改：`api/main.py`
- 测试：Provider/client 路由与运行时测试

- [ ] 为 `openai_chat_completions`、`openai_responses`、`anthropic_messages` 建立明确 client 构造分支。
- [ ] 解密 Key 只作为函数参数传递，禁止写入进程环境、日志、异常、trace 或 transcript。
- [ ] 未登录本地模式继续兼容 `.sage/settings.json` 与服务端环境变量。

### Task 6: Provider/模型统一界面与首屏初始化

**文件：**
- 修改：`frontend/src/views/SettingsView.vue`
- 修改：`frontend/src/stores/coding.ts`
- 修改：`frontend/src/api/coding.ts`
- 修改：`frontend/src/types/api.ts`
- 修改：`frontend/src/components/coding/composer/CodingComposer.vue`
- 测试：设置页、Composer、store/API 测试

- [ ] 将 Provider 和模型合并为同一入口，支持添加、编辑、测试、发现和手工模型。
- [ ] 保存后的 Key 输入框为空，只显示已配置提示；前端状态永不保存明文 Key。
- [ ] 新增幂等 `bootstrapModelCatalog()`，Coding 路由挂载即加载账号或本地目录。
- [ ] 账号默认立即填充 Composer，session 创建/恢复后再使用 session snapshot 覆盖。

### Task 7: 自审、真实浏览器 QA 与 V7 收口

**文件：**
- 新增：`/Users/zeromadlife/Desktop/Obsidian-Knowledge-Base/03_项目/tourswarm/技术沉淀/sage-learning/38-V7-runtime-provider体验复盘.md`

- [ ] 用 Logic Lens 审查 owner scope、SSRF、Key 泄漏、错误脱敏、事件配对和状态恢复。
- [ ] 跑后端定向/全量门禁、前端全量测试、production build、静态检查和 `git diff --check`。
- [ ] 使用 Browser/IAB 验证桌面与 `390 x 844`：首屏模型、发送顺序、角色内部动作、审批、安全 shell、折叠摘要、刷新追溯和控制台健康。
- [ ] 提交短期分支，审查与 `codex/feat-v7-control-plane` 的共同祖先和共享契约后合并。
- [ ] 在 V7 集成 worktree 重跑受影响门禁并更新 Obsidian；不合入 `dev/sage-v6` 或 `main`。
