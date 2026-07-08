# Sage v3 落地记录

> 日期：2026-07-08
> 当前阶段：方向一完成；方向三完成（工具系统装饰器化）
> 参考：`docs/superpowers/prompts/2026-07-08-codex-goal-sage-v3.md`

## 目标

Sage v3 开始向 Hermes / Hermes Web UI 的设计演进。本阶段先做两块后端地基：让 coding runtime 的 system prompt 在 session 生命周期内保持 byte-stable；再把工具系统从单文件表驱动改成装饰器注册 + 分模块实现，为后续 approval / tool policy / toolset 扩展打基础。

## 本阶段改动

### ContextManager prompt caching

`core/coding/context_manager.py` 新增：

- `build_system_prompt_once()`：同一 tools 集合下复用缓存的 system prompt。
- `invalidate_system_prompt()`：压缩、记忆刷新或后续工具/skill 变化时显式失效。
- 三层 prompt 结构：
  - stable：Sage 身份、工具指引、工具列表。
  - context：当前 workspace repository 上下文。
  - volatile：日期精度的 session date。
- `normalize_text()`：对 user/history/tool 文本做 `.strip()`，保持 prompt 前缀稳定。
- `system_prompt_build_count`：测试和调试用，确认缓存是否生效。

### Runtime 生命周期修正

`core/coding/runtime.py` 现在在 session 初始化时创建一个 `self.context_manager`，并在每轮 `Engine` 里复用它。这样缓存生命周期从“单轮”提升到“session”。

### Compaction 失效点

`core/coding/compact.py` 的 `compact()` 支持可选 `context_manager` 参数。发生真实压缩时会调用 `invalidate_system_prompt()`，为后续 memory/context 刷新留出接入点。

### 工具系统装饰器化

`core/coding/tools/registry.py` 现在只负责：

- `ToolDefinition`：保存工具 schema、schema model、风险、分类、handler 等元数据。
- `@register_tool(...)`：由工具模块在函数定义处注册。
- `registered_tool_definitions()`：返回已发现工具定义，便于测试和调试。
- `build_tool_registry()`：按 workspace/session 组装 `RegisteredTool`。
- `validate_tool()`：保留 pydantic 校验和 workspace 逃逸/文件存在性等安全约束。

工具实现拆分为：

- `file_tools.py`：`list_files` / `read_file` / `search` / `write_file` / `patch_file`
- `shell_tool.py`：`run_shell`
- `todo_tools.py`：`todo_add` / `todo_update` / `todo_list`
- `plan_tools.py`：`enter_plan_mode` / `exit_plan_mode`
- `agent_tools.py`：`agent` / `send_message` / `task_stop`

`RegisteredTool` 新增：

- `category`：`file` / `shell` / `todo` / `plan` / `agent`
- `requires_approval`：默认跟随 `risky`
- `timeout`：同步 runner 的通用超时保护，避免非 shell 工具长期卡住执行链路

## 测试覆盖

`tests/core/coding/test_context_compact.py` 新增：

- 同一 session 多轮 build 只构建一次 system prompt。
- invalidate 后下一轮重建。
- volatile tier 使用日期精度，不引入分钟/秒级 cache busting。
- compact 后能让 context cache 失效。

`tests/core/coding/test_tools.py` 新增：

- 工具发现后的公开工具集保持不变。
- 装饰器注册暴露 `ToolDefinition` 和 schema model。
- `category` / `requires_approval` 元数据正确。
- 同步 runner 有通用 timeout 保护。

## 已验证

```bash
pytest tests/core/coding/test_context_compact.py -q
```

结果：`6 passed`

```bash
ruff check core/coding/context_manager.py core/coding/runtime.py core/coding/compact.py tests/core/coding/test_context_compact.py
mypy core/coding tests/core/coding/test_context_compact.py
```

结果：ruff 通过，mypy 通过。

```bash
pytest tests/core/coding/test_context_compact.py tests/core/coding/test_engine.py tests/api/test_coding_routes.py -q
```

结果：`22 passed`

```bash
pytest tests/core/coding/test_tools.py -q
```

结果：`9 passed`

```bash
mypy core/coding/tools tests/core/coding/test_tools.py
pytest tests/core/coding tests/api/test_coding_routes.py -q
```

结果：mypy 通过；coding/API 回归 `65 passed`

## 后续方向

1. Approval 系统：危险命令检测、pending approval 事件、前端 approval card。
2. Hermes Web UI 交互增强：工具结果截断、两阶段折叠防跳、文件树缓存、context ring tooltip、Skills 搜索分类。
3. Graphify 更新：完成 v3 主要方向后重新生成架构图谱。
