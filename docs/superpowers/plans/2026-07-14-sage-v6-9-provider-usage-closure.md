# Sage V6.9 Provider、推理和用量收束实施计划

**目标：** 在不破坏 coding runtime、timeline 和恢复契约的前提下，提供 `.sage` 非敏感配置、真实 Provider 推理控制、真实用量统计，以及经确认的 Composer/运行态/设置页体验。

**架构：** 新的 `SageProviderSettings` 负责严格解析 `.sage/settings.json` 并投影模型目录、context capability 与 reasoning descriptor；缺少该文件时继续只读兼容 `coding_models.toml`。runtime 持久化选择的 reasoning mode，由模型工厂构造带真实请求参数的 LangChain 客户端。Engine 在每次真实模型请求结束后将响应 metadata 交给单独的 SQLite `UsageStore`，前端只消费聚合 API。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、LangChain OpenAI/Anthropic、Vue 3、Pinia、Vitest、Lucide、现有 Sage token。

---

### Task 1: `.sage` Provider 配置与运行时目录

**文件：**
- 新增：`core/coding/provider_settings.py`
- 修改：`api/main.py`
- 修改：`api/coding.py`
- 修改：`api/schemas.py`
- 修改：`.gitignore`
- 测试：`tests/core/coding/test_provider_settings.py`
- 测试：`tests/api/test_coding_provider_settings_routes.py`

- [ ] 先写 schema 测试：未知字段、重复 id、无效 URL/env 名、无效 context/reasoning、默认模型缺失均失败。
- [ ] 实现 JSON settings loader、TOML fallback projection、原子写入、部署托管只读和 API Key 脱敏状态。
- [ ] 将 app 的模型目录、context registry、Provider descriptor 和默认模型从同一已验证来源构建；保留注入 catalog 的既有测试兼容。
- [ ] 新增 Provider GET/PUT API，限定为默认 workspace 的 `.sage/settings.json`，不能接受任意路径或 API key。

### Task 2: 真实 reasoning 与用量账本

**文件：**
- 新增：`core/coding/usage_store.py`
- 修改：`core/llm.py`
- 修改：`core/llm_openai.py`
- 修改：`core/llm_anthropic.py`
- 修改：`core/coding/engine/engine.py`
- 修改：`core/coding/runtime.py`
- 修改：`api/coding.py`
- 修改：`api/schemas.py`
- 测试：`tests/core/coding/test_usage_store.py`
- 测试：`tests/core/coding/test_reasoning_runtime.py`
- 测试：`tests/api/test_coding_usage_routes.py`

- [ ] 先测试 OpenAI `reasoning_effort` 和 Anthropic thinking budget 的合法映射；未声明和不兼容模型必须拒绝或为 off。
- [ ] 为 session 增加 `reasoning_mode`，模型切换时安全回退，活动 run/压缩期间拒绝切换；worker 使用当前已选模型和推理模式。
- [ ] 过滤非文本 response content block，确保 thinking block 绝不会进入 delta、transcript、timeline 或 run trace。
- [ ] 归一化 `usage_metadata`/`response_metadata.token_usage`；SQLite 以 `run_id:attempt` 幂等写入，只保存 token 聚合字段。
- [ ] 新增 usage 聚合 API 和范围验证，Provider 不返回 usage 或没有价格表时返回 null，而非伪造 0 或费用。

### Task 3: Composer、运行头像和设置页面

**文件：**
- 新增：`frontend/src/components/coding/chat/SageThinkingAvatar.vue`
- 新增：`frontend/src/assets/sage-thinking-avatar.png`
- 修改：`frontend/src/components/coding/composer/CodingComposer.vue`
- 修改：`frontend/src/components/coding/chat/CodingThinkingIndicator.vue`
- 修改：`frontend/src/views/SettingsView.vue`
- 修改：`frontend/src/router/index.ts`
- 修改：`frontend/src/api/coding.ts`
- 修改：`frontend/src/stores/coding.ts`
- 修改：`frontend/src/types/api.ts`
- 测试：`frontend/src/components/coding/composer/CodingComposer.test.ts`
- 测试：`frontend/src/components/coding/chat/CodingThinkingIndicator.test.ts`
- 测试：`frontend/src/views/SettingsView.test.ts`

- [ ] 扩展前端 API/types/store，接入 Provider 设置、reasoning 切换和 usage summary。
- [ ] 把 Composer 改为输入区右上 context、底部 command rail；保留 slash menu、权限 drawer、停止/发送和移动端稳定尺寸。
- [ ] 将运行态替换为 Sage 原创金发 Q 版头像、阶段、秒数与受 reduced-motion 约束的流光；绝不显示模型内部 reasoning 文本。
- [ ] 新增 Provider 和用量 section，Provider 无 API Key 输入框/值；用量区支持范围、空态和未知费用。

### Task 4: 真实资产和页面验证

**文件：**
- 生成/新增：`frontend/src/assets/sage-thinking-avatar.png`
- 修改：相关组件与测试（如需）

- [ ] 以“软萌金发 Q 版、轻松回应感、Sage 原创角色”为约束生成独立位图；不使用用户参考角色的面部、服装或标志元素。
- [ ] 检查生成图在圆形 28px/32px 裁切下的可辨识性和浅深色背景对比。
- [ ] 在浏览器桌面和 390 x 844 验证 Composer、运行态、Provider、用量、工具详情、自动贴底和无重叠文本。

### Task 5: 门禁、自审与集成收口

**文件：**
- 新增：`/Users/zeromadlife/Desktop/Obsidian-Knowledge-Base/03_项目/tourswarm/技术沉淀/sage-learning/37-V6.9-provider-usage收束复盘.md`

- [ ] 跑受影响后端测试、前端全量测试、前端 production build、ruff、mypy、compileall、`git diff --check`。
- [ ] 使用 Logic Lens 审查 settings 输入、环境变量脱敏、reasoning 泄漏、usage 幂等与 SQLite 路径边界。
- [ ] 审查短期分支和 `dev/sage-v6` 的共同祖先、共享 API/store、未提交文件和可合并性。
- [ ] 合并后在集成 worktree 重跑受影响验证，更新 Obsidian，只有 worktree 干净且分支被包含时才删除短期分支。
