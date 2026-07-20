# 07 - Skills 与命令系统

> 本章目标：能讲清 SKILL.md 的格式、slash 命令的解析与展开流程、8 个 bundled skill 各自做什么、以及 skill 的 `allowed_tools` 当前为什么没强制执行。

## Skill 是什么

Skill 是**可复用的 prompt 工作流**，通过 `/name [args]` 触发。它不是工具（不执行动作），而是一段展开后注入 prompt 的指令文本。

举个例子：
- 用户输入 `/review src/auth.py`
- `SkillRegistry.resolve()` 找到 `review` skill
- `Skill.render("src/auth.py")` 把 `$ARGUMENTS` 替换成 `src/auth.py`
- 展开后的文本作为 `skill_prompt` 注入当前轮 prompt（不写 history）
- 模型按这段指令执行代码审查

Skill 和 Tool 的区别：

| 维度 | Skill | Tool |
| --- | --- | --- |
| 本质 | prompt 指令文本 | 可执行函数 |
| 触发 | `/name args`（用户显式）或 LLM 自动 | LLM 在 ReAct 循环中调用 |
| 执行 | 不执行，只展开成 prompt | 真正读写文件/执行命令 |
| 权限 | 不能绕过 ToolExecutor | 受五道门约束 |
| 持久化 | per-turn 注入，不写 history | tool result 写 history |

## SKILL.md 格式

```markdown
---
name: review
description: 代码审查
allowed-tools: read_file, search, list_files
argument-hint: REQUEST
---
你正在执行 Sage 的 review skill。

审查目标：$ARGUMENTS

请按以下方式处理：
1. 先用 read_file 读取目标文件
2. 检查代码风格、潜在 bug、安全风险
3. 给出具体的改进建议，附行号
```

### Frontmatter 字段

| 字段 | 含义 | 必填 |
| --- | --- | --- |
| `name` | skill 名（`/name` 触发） | 是 |
| `description` | 一行描述（给模型看） | 是 |
| `allowed-tools` | 允许使用的工具白名单 | 否 |
| `argument-hint` | 参数提示名（如 `REQUEST` / `PATH`） | 否 |
| `user-invocable` | 是否用户可调用（默认 true） | 否 |

### 变量替换

`Skill.render(arguments)` 替换：

| 占位符 | 替换成 |
| --- | --- |
| `$ARGUMENTS` | 用户传入的参数 |
| `${SAGE_SKILL_DIR}` | skill 所在目录（访问 skill 的附属文件） |
| `${PICO_SKILL_DIR}` | 同上（兼容旧名） |
| `${argument_hint}` | 如 `${REQUEST}` 替换成参数 |

## Skill 发现顺序

`discover_skills(root, home)` 按优先级搜索：

```
1. builtin:        core/coding/skills/bundled/<name>/SKILL.md
2. home:           ~/.sage/skills/<name>/SKILL.md
3. workspace:      <workspace>/skills/<name>/SKILL.md
4. workspace:      <workspace>/.coding/skills/<name>/SKILL.md
```

**后者覆盖前者**。这样用户可以在 workspace 级覆盖 builtin skill，或新增自定义 skill。

## 8 个 Bundled Skill

```
core/coding/skills/bundled/
├── commit/SKILL.md         - Git 提交工作流
├── dream/SKILL.md          - 反思提案（调用 dream tool）
├── planmode/SKILL.md       - 进入计划模式
├── remember/SKILL.md       - 显式记忆（调用 remember tool）
├── review/SKILL.md         - 代码审查
├── test/SKILL.md           - 测试运行与验证
├── travel/SKILL.md         - 旅游行程规划（多 Agent 协作）
└── travel-planning/SKILL.md - 兼容旧入口
```

### 典型 skill：review

```markdown
---
name: review
description: 代码审查
allowed-tools: read_file, search, list_files
argument-hint: REQUEST
---
你正在使用 Sage 的 review skill。

用户需求：

$ARGUMENTS

请按以下方式处理：

1. 先确认审查目标和范围。
2. 用 read_file 读取相关文件，用 search 查找关键模式。
3. 检查代码风格、潜在 bug、安全风险、性能问题。
4. 给出具体的改进建议，引用文件路径和行号。
5. 不要直接修改文件，只给出审查意见。
```

### 典型 skill：travel（领域 skill）

```markdown
---
name: travel
description: 旅游行程规划（多Agent协作 + 预算约束 + 确定性验证）
allowed-tools: generate_itinerary, search_attractions, get_weather, get_forecast, geocode, search_nearby, get_route
arguments: REQUEST
---
你正在使用 Sage 的 travel domain skill。

用户需求：

$ARGUMENTS

请按以下方式处理：
1. 先确认目的地、天数、预算、偏好、出发时间或当前位置是否足够明确。
2. 如果关键信息不完整，只追问最关键的一项。
3. 如果用户需要完整行程，调用 `generate_itinerary`。
4. 如果用户问天气，调用 `get_weather` 或 `get_forecast`。
5. 预算敏感，推荐时优先考虑学生消费水平。
6. 输出要面向用户，不要暴露原始 JSON。
```

travel skill 的 `allowed-tools` 列出了 7 个 deferred travel tool。用户输入 `/travel 杭州 3天` 后，这些工具会被建议激活（但当前 `allowed_tools` 没强制执行，见下文）。

## Slash 命令解析与展开

```
用户输入: /review 检查 src/auth.py
  ↓
api/coding.py::coding_stream
  ↓
runtime.resolve_slash(content)
  ├── parse_slash_command("/review 检查 src/auth.py")
  │   └── command="review", arguments="检查 src/auth.py"
  ├── skill = skill_registry.get("review")
  └── return (skill, "review", "检查 src/auth.py")
  ↓
expanded = skill.render("检查 src/auth.py")
  ↓ 展开后的文本作为 skill_prompt
WebSocket 发送 skill_invoked 事件
  ↓
runtime.run_turn(expanded, skill_prompt=expanded)
  ↓
Engine 把 skill_prompt 注入 prompt（per-turn，不写 history）
  ↓
模型按指令执行
```

**关键**：slash 命令展开后的文本**不写 history**。history 里只存原始用户输入（`/review 检查 src/auth.py`）。skill_prompt 是 per-turn 注入，下轮消失。这样 history 不会被大段指令文本污染。

## `allowed_tools` 当前为什么没强制执行

SKILL.md frontmatter 里的 `allowed-tools` 被解析到 `Skill.allowed_tools` 元数据，但**当前 ToolExecutor 没有强制执行这个白名单**。

```python
@dataclass(frozen=True)
class Skill:
    allowed_tools: tuple[str, ...] = ()  # 解析了但没强制
```

当前所有工具对所有 skill 都可用。这意味着：
- `/review` skill 声明只用 `read_file, search, list_files`
- 但模型在 review 过程中可以调用 `write_file` 或 `run_shell`
- permission/policy 仍然检查，但不是 skill 级白名单

**为什么没强制**：强制 `allowed_tools` 需要在 ToolExecutor 里加一层"当前 active skill 的工具白名单"检查。这涉及：
- skill 激活时记录 active skill
- ToolExecutor 检查 `tool.name in active_skill.allowed_tools`
- skill 结束时清除 active skill
- 多 skill 嵌套的处理

这是一个明确的待办（V4 工具系统强化 Task 5），当前没做。

## Skill 与 Tool 的协作

Skill 展开成 prompt 后，模型按 prompt 指令调用工具。比如 `/travel 杭州 3天`：

```
1. 用户输入 /travel 杭州 3天
2. skill_prompt 展开（包含"调用 generate_itinerary"指令）
3. 模型看到 skill_prompt，决定调用 tool_search("travel")
4. tool_search 激活 travel 类 deferred tools
5. 下一轮模型调用 generate_itinerary(destination="杭州", duration_days=3)
6. 工具执行（走五道门），返回行程 JSON
7. 模型把 JSON 格式化成用户友好的回复
8. yield FinalEvent
```

Skill 是"指挥官"，Tool 是"执行者"。Skill 告诉模型该做什么，Tool 真正做。

## v2 的 Skill Catalog

v2 runtime 里，skill 通过 `SkillCatalog` 接入：

```python
# packages/sage_harness/sage_harness/agents/factory.py
def create_sage_agent(
    model, tools, *,
    skill_catalog: SkillCatalog | None = None,
    ...
):
    if skill_catalog is not None:
        middleware.append(SkillActivationMiddleware(skill_catalog))
```

`SkillActivationMiddleware` 负责把 active skill 的 context 注入 `SageThreadState.skill_context`。state 只保存 skill 引用和版本，不保存完整 Skill 正文。

## 和 Pico v3 / Claude Code / Hermes 的对标

| 维度 | Sage v7-beta | Pico v3 | Claude Code | Hermes |
| --- | --- | --- | --- | --- |
| Skill 格式 | SKILL.md + YAML frontmatter | SKILL.md | SKILL.md | SKILL.md |
| 触发方式 | `/name args` slash 命令 | `/name args` | `/name args` | `/name args` |
| 变量替换 | `$ARGUMENTS` + `${SAGE_SKILL_DIR}` | `$ARGUMENTS` | `$ARGUMENTS` | `$ARGUMENTS` |
| 发现顺序 | builtin -> home -> workspace | builtin -> workspace | builtin -> user -> project | AST 扫描 |
| 工具白名单 | 解析了没强制 | 无 | 无 | 强制 |
| 持久化 | per-turn，不写 history | per-turn | per-turn | per-turn |

Sage 的 skill 系统和 Pico/Claude Code 基本一致，差异在 `allowed_tools` 当前没强制（Pico/Claude Code 也没有）。Hermes 有强制的工具白名单，是 Sage V4 的改进方向。

## 第一入口

按顺序打开：

1. `core/coding/skills/skill.py::Skill` - Skill dataclass + render
2. `core/coding/skills/skill.py::discover_skills` - skill 发现
3. `core/coding/skills/skill.py::parse_slash_command` - slash 解析
4. `core/coding/skills/registry.py::SkillRegistry.resolve` - slash 命令解析
5. `core/coding/runtime.py::resolve_slash` - runtime 入口
6. `api/coding.py::coding_stream` - WebSocket 处理 slash
7. `core/coding/skills/bundled/review/SKILL.md` - 典型 skill
8. `core/coding/skills/bundled/travel/SKILL.md` - 领域 skill

## 测试证据

- `tests/core/coding/test_skills.py` - skill 发现 + slash 解析 + render
- `tests/api/test_coding_routes.py::test_coding_stream_resolves_slash_skill` - WebSocket slash
- `tests/core/coding/test_skills.py::test_skill_render_substitutes_arguments` - 变量替换
- `tests/core/coding/test_skills.py::test_workspace_skill_overrides_builtin` - 覆盖优先级

## 当前边界

> [!warning] Skill 系统有几个已知局限
> - `allowed_tools` 解析了但没强制执行（V4 Task 5 待办）
> - 没有 `sage skill install <path|git-url>` CLI（外部 skill 安装要手动放目录）
> - 没有 AST 自动扫描（Hermes 风格），靠目录遍历
> - skill 嵌套没处理（一个 skill 触发另一个 skill）
> - v2 的 `SkillActivationMiddleware` 实现完成，但 `allowed_tools` 强制未接

## 自测

1. Skill 和 Tool 的区别？为什么 Skill 不写 history？
2. SKILL.md 的 frontmatter 有哪些字段？`allowed-tools` 当前为什么没强制？
3. Skill 发现顺序是什么？为什么 workspace 级能覆盖 builtin？
4. Slash 命令的展开流程？`$ARGUMENTS` 怎么替换？
5. `/travel 杭州 3天` 这个命令从输入到最终回答，经过哪些步骤？
6. 如果要实现 `allowed_tools` 强制执行，需要改哪些地方？

下一章：[[08-memory-dream]]
