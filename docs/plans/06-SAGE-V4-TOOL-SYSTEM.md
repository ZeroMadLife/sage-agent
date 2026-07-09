# Sage V4 工具系统强化 - 任务清单

> 基于对 Hermes 工具系统的深度分析，提炼出可落地到 Sage 的改进点。
> 按优先级排序，每个 Task 独立可交付。
> 更新日期：2026-07-09 - 文件路径已同步 V5.0 结构解耦重构后的新包路径。

---

## 优先级总览

| 优先级 | Task | 来源 | 价值 | 前置 |
|:---:|------|------|------|------|
| P0 | AST 自动工具发现 | Hermes registry.py | 新增工具零配置注册 | 无 |
| P0 | 跨 Agent 文件状态协调 | Hermes file_state.py | 并发安全基石 | 无 |
| P0 | 并发锁 + 异步安全 | Hermes delegate_tool.py | 多 Agent 不死锁 | Task 2 |
| P1 | 辅助 LLM 自动审批 | Hermes approval.py | 减少用户打断 | 无 |
| P1 | 子 Agent 审批策略 | Hermes delegate_tool.py | 子 Agent 不死锁 | Task 3 |
| P1 | Explore 只读子 Agent | Hermes delegate_tool.py | 安全搜索 | Task 5 |
| P2 | 并行子 Agent + 线程池 | Hermes delegate_tool.py | 并行任务加速 | Task 5, 6 |
| P2 | MCP 集成 | Hermes mcp_tool.py | 外部工具生态 | 无 |
| P2 | 前端审批 UI 优化 | Sage 自身 | 用户体验 | V5.1 |

> **与 V5.1 的关系：** V5.1（plan mode + skill 菜单）是前端体验优先路径，V4（工具系统强化）是后端能力优先路径。两者可并行推进，但 V4 Task 7（前端审批 UI）依赖 V5.1 的前端基础设施。

---

## V5.0 后的新文件路径映射

V5.0 重构后，以下路径已变更，本计划中所有引用已同步：

| V4 计划旧路径 | V5.0 新路径 |
|------|------|
| `core/coding/approval.py` | `core/coding/tool_executor/approval.py` |
| `core/coding/tool_policy.py` | `core/coding/tool_executor/policy.py` |
| `core/coding/permissions.py` | `core/coding/tool_executor/permissions.py` |
| `core/coding/tool_executor.py` | `core/coding/tool_executor/executor.py` |
| `core/coding/worker_manager.py` | `core/coding/multiagent/manager.py` |
| `core/coding/worker_runtime.py` | `core/coding/multiagent/runtime.py` |
| `core/coding/engine.py` | `core/coding/engine/engine.py` |
| `core/coding/workspace.py` | `core/coding/context/workspace.py` |

---

## Task 1：AST 自动工具发现（P0）

**现状：** `core/coding/tools/registry.py` 硬编码 `TOOL_MODULES` 列表，新增工具需要手动加。

**目标：** 扫描 `core/coding/tools/` 目录，AST 解析自动发现含 `@register_tool` 的模块。

```python
# 现状
TOOL_MODULES = (
    "core.coding.tools.file_tools",
    "core.coding.tools.shell_tool",
    ...
)

# 目标 - 参考 Hermes 的 discover_builtin_tools
def discover_tool_modules(tools_dir: Path) -> list[str]:
    """AST 扫描 tools/ 目录，返回含 @register_tool 调用的模块名。"""
    for path in sorted(tools_dir.glob("*.py")):
        tree = ast.parse(path.read_text())
        if _has_register_tool_decorator(tree):
            modules.append(f"core.coding.tools.{path.stem}")
    return modules
```

**改动文件：**
- 修改 `core/coding/tools/registry.py` - 新增 `discover_tool_modules()`
- 新增 `tests/core/coding/test_tool_discovery.py`

---

## Task 2：跨 Agent 文件状态协调（P0）

**现状：** `core/coding/context/workspace.py` 的 `WorkspaceContext` 只追踪单 Agent 的读写指纹。多 Agent 并发时会覆盖。

**目标：** 进程级 `FileStateRegistry` 单例，追踪所有 Agent 的文件读写状态。

```python
# 新文件：core/coding/multiagent/file_state.py
class FileStateRegistry:
    """进程级跨 Agent 文件状态协调器。

    追踪：
    - per-agent 读戳: {agent_id: {path: (mtime, read_ts, partial)}}
    - 全局最后写入者: {path: (agent_id, write_ts)}
    - per-path 锁: 读->改->写 临界区

    三个钩子：
    - record_read(agent_id, path)  - read_file 时调用
    - note_write(agent_id, path)   - write_file/patch 后调用
    - check_stale(agent_id, path)  - write_file/patch 前调用
    """

    def check_stale(self, agent_id: str, path: Path) -> tuple[bool, str]:
        """检查文件是否被其他 Agent 修改过。

        Returns:
            (is_stale, message)
            is_stale=True 时调用方必须重新 read_file
        """
        last_writer = self._global_writers.get(str(path))
        if last_writer and last_writer[0] != agent_id:
            my_read = self._read_stamps.get(agent_id, {}).get(str(path))
            if my_read and last_writer[1] > my_read[1]:
                return True, f"文件被 Agent {last_writer[0]} 修改过，请重新读取"
        return False, ""
```

**集成到 ToolPolicyChecker：**

```python
# core/coding/tool_executor/policy.py 改动
class ToolPolicyChecker:
    def __init__(self, workspace, file_registry=None, agent_id="main"):
        self._file_registry = file_registry
        self._agent_id = agent_id

    def check(self, tool, args):
        if tool.name in ("patch_file", "write_file"):
            path = self.workspace.path(args.get("path", ""))
            # 检查1：自己是否读过（原有逻辑）
            if not self._has_fresh_read(path):
                return deny("prior_read_required")
            # 检查2：其他 Agent 是否改过（新增）
            if self._file_registry:
                is_stale, msg = self._file_registry.check_stale(self._agent_id, path)
                if is_stale:
                    return deny("file_modified_by_other_agent", msg)
        return allow()
```

**改动文件：**
- 新增 `core/coding/multiagent/file_state.py`
- 修改 `core/coding/tool_executor/policy.py` - 集成 FileStateRegistry
- 修改 `core/coding/tools/file_tools.py` - read_file 后 record_read，write/patch 后 note_write
- 新增 `tests/core/coding/test_file_state.py`

---

## Task 3：并发锁 + 异步安全（P0）

**现状：** 子 Agent 在线程池中运行（`core/coding/tools/base.py` 的 `_TOOL_EXECUTOR`），但审批回调存在主线程上下文，子线程不继承 -> 死锁。

**目标：** 子 Agent 的危险命令自动拒绝（不调用交互式审批），主 Agent 的审批走 WebSocket。

```python
# 新增到 core/coding/tool_executor/approval.py
def subagent_auto_deny(command: str, description: str, **kwargs) -> str:
    """子 Agent 危险命令自动拒绝 - 防止死锁。

    子 Agent 在线程池中运行，不能调用交互式审批（会死锁主线程）。
    所有危险命令自动拒绝，子 Agent 需自行降级处理。
    """
    logger.warning(
        "子 Agent 危险命令自动拒绝: %s (%s)",
        command, description,
    )
    return "deny"

class ApprovalManager:
    def submit_for_subagent(self, session_id, tool, args, description, pattern_key):
        """子 Agent 的审批请求 - 立即拒绝而非阻塞。"""
        return "deny"  # 不创建 ApprovalEntry，不阻塞
```

**改动文件：**
- 修改 `core/coding/tool_executor/approval.py` - 新增 subagent_auto_deny
- 修改 `core/coding/multiagent/manager.py` - 子 Agent 审批回调注入
- 新增 `tests/core/coding/test_subagent_approval.py`

---

## Task 4：辅助 LLM 自动审批（P1）

**现状：** 所有危险命令都需要用户手动确认，频繁打断。

**目标：** 用轻量 LLM（DeepSeek）判断命令风险等级，低风险自动允许，高风险仍需人工。

```python
# 新增到 core/coding/tool_executor/approval.py
class SmartApprovalManager(ApprovalManager):
    """辅助 LLM 自动审批。

    流程：
    1. 危险命令模式匹配 -> 命中
    2. 辅助 LLM 判断风险等级（low/medium/high/critical）
    3. low -> 自动允许（如 rm -rf node_modules）
    4. medium -> 需要审批但可以延迟（不阻塞）
    5. high/critical -> 必须人工确认（如 rm -rf /, git push --force）
    """

    CANNOT_AUTO_APPROVE = {"rm_recursive_root", "git_force_push_main"}

    async def check_with_llm(self, command: str, pattern_key: str) -> str:
        """用 LLM 判断是否可以自动审批。

        Returns:
            "auto_allow" / "needs_human" / "auto_deny"
        """
        # 灾难性命令永远需要人工
        if pattern_key in self.CANNOT_AUTO_APPROVE:
            return "needs_human"

        prompt = f"""判断以下 shell 命令的风险等级：
命令: {command}
匹配模式: {pattern_key}

风险等级:
- low: 安全的操作（如清理 node_modules、构建产物）
- high: 可能造成不可逆损失（如删除源码、强制推送主分支）

只输出 low 或 high。"""

        response = await self._llm.ainvoke([{"role": "user", "content": prompt}])
        risk = response.content.strip().lower()
        return "auto_allow" if risk == "low" else "needs_human"
```

**LLM 审批 prompt 示例：**

```
命令: rm -rf node_modules .next dist
匹配: rm_recursive

LLM 判断: low（清理构建产物）-> 自动允许

命令: rm -rf src/
匹配: rm_recursive

LLM 判断: high（删除源码目录）-> 需要人工确认

命令: git push --force origin main
匹配: git_force_push

LLM 判断: high（覆盖远程主分支）-> 需要人工确认（CANNOT_AUTO_APPROVE 兜底）
```

**改动文件：**
- 修改 `core/coding/tool_executor/approval.py` - 新增 SmartApprovalManager
- 修改 `core/coding/engine/engine.py` - 工具执行前走 SmartApprovalManager
- 新增 `tests/core/coding/test_smart_approval.py`

---

## Task 5：子 Agent 审批策略 + Explore 只读（P1）

**现状：** 子 Agent 工具集和主 Agent 一样，没有隔离。

**目标：**
1. 子 Agent 危险命令自动拒绝（Task 3 已覆盖）
2. Explore 子 Agent 只读 - 只能 list_files/read_file/search，不能 write/patch/shell

```python
# core/coding/multiagent/manager.py 改动
SUBAGENT_BLOCKED_TOOLS = frozenset([
    "agent",           # 禁止递归委派
    "send_message",    # 禁止跨 Agent 通信
    "task_stop",       # 禁止停止其他 Agent
])

EXPLORE_ALLOWED_TOOLS = frozenset([
    "list_files",
    "read_file",
    "search",
    "tool_search",
])

def build_subagent_tools(
    workspace, tool_context, subagent_type: str
) -> dict[str, RegisteredTool]:
    """根据子 Agent 类型构建受限工具集。"""
    all_tools = build_tool_registry(workspace, tool_context)

    # 所有子 Agent 禁止递归委派
    for blocked in SUBAGENT_BLOCKED_TOOLS:
        all_tools.pop(blocked, None)

    if subagent_type == "Explore":
        # Explore 只保留只读工具
        return {
            name: tool for name, tool in all_tools.items()
            if name in EXPLORE_ALLOWED_TOOLS
        }

    # worker 类型保留除 blocked 外的所有工具
    return all_tools
```

**改动文件：**
- 修改 `core/coding/multiagent/manager.py` - 工具集隔离
- 修改 `core/coding/tools/agent_tools.py` - agent 工具支持 subagent_type
- 新增 `tests/core/coding/test_subagent_tools.py`

---

## Task 6：并行子 Agent + 线程池超时（P2）

**现状：** 子 Agent 串行执行（`core/coding/multiagent/manager.py` 的 `WorkerManager`）。

**目标：** 支持并行委派多个子 Agent，父 Agent 等待全部完成。

```python
# core/coding/multiagent/manager.py 改动
class WorkerManager:
    def __init__(self, max_workers: int = 4, default_timeout: float = 120.0):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="sage-worker",
            initializer=_set_subagent_context,
        )
        self._default_timeout = default_timeout

    def spawn_batch(self, tasks: list[dict]) -> list[dict]:
        """并行启动多个子 Agent，等待全部完成。"""
        futures = [
            self._executor.submit(self._run_worker, task)
            for task in tasks
        ]
        results = []
        for future in futures:
            try:
                result = future.result(timeout=self._default_timeout)
                results.append(result)
            except TimeoutError:
                results.append({"error": "worker timed out"})
            except Exception as exc:
                results.append({"error": str(exc)})
        return results
```

**改动文件：**
- 修改 `core/coding/multiagent/manager.py` - 并行 + 超时
- 修改 `core/coding/tools/agent_tools.py` - 新增 batch agent 工具
- 新增 `tests/core/coding/test_parallel_workers.py`

---

## Task 7：前端审批 UI 优化（P2）

> 依赖 V5.1 前端基础设施完成。

**现状：** `frontend/src/components/coding/chat/CodingApprovalCard.vue` 功能已有但体验粗糙。

**目标：**
1. 审批弹窗显示 diff 预览（write_file/patch_file 时）
2. 辅助 LLM 审批状态展示（"AI 判断：低风险"）
3. 批量审批（一次会话内同类命令不再弹窗）
4. 审批历史时间线

**改动文件：**
- 修改 `frontend/src/components/coding/chat/CodingApprovalCard.vue` - diff 预览
- 新增 `frontend/src/components/coding/chat/ApprovalTimeline.vue` - 审批历史
- 修改 `frontend/src/stores/codingStream.ts` - 审批状态管理

---

## Task 8：MCP 集成（P2 - 后续开展）

**现状：** Sage 旅游工具直接持有 client 方法（`core/coding/tools/travel_tools.py`），不走 MCP 协议。

**目标：** 支持接入外部 MCP Server（如 GitHub MCP、文件系统 MCP）。

```python
# 后续设计 - 参考 Hermes mcp_tool.py
class McpToolIntegration:
    """将外部 MCP Server 的工具注册到 Sage 工具系统。

    支持：
    - stdio 传输（子进程）
    - HTTP/StreamableHTTP 传输
    - 动态工具发现（tools/list）
    - 环境变量过滤（不泄露 API Key）
    """
```

**改动文件：**
- 新增 `core/coding/mcp_integration.py`
- 修改 `core/coding/tools/registry.py` - MCP 工具注册
- 后续规划，不阻塞 V4

---

## 执行顺序建议

```
Phase A（安全基石）:
  Task 1 (AST 发现) -> Task 2 (文件状态协调) -> Task 3 (并发锁)

Phase B（智能审批）:
  Task 4 (辅助LLM审批) -> Task 5 (子Agent隔离)

Phase C（并行能力）:
  Task 6 (并行子Agent)

Phase D（体验 + 生态）:
  Task 7 (前端审批UI, 依赖V5.1) -> Task 8 (MCP集成)
```

## 约束

- commit 消息用中文
- 不破坏现有 357 backend tests + 70 frontend tests
- 所有新代码有 type hints + mypy strict + ruff
- 测试用 mock，不依赖真实 LLM/外部服务
- 参考 Hermes 设计但不照搬 - Sage 面向 Web，Hermes 面向 CLI
