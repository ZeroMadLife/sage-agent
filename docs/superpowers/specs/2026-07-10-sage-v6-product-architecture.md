# Sage V6 产品架构规划

> 日期：2026-07-10
> 分支：dev/sage-v5
> 基线：406 backend tests + 118 frontend tests
> 前置：V5 完成权限治理 + Plan 审批 + 流式修复 + Provider 拆分

---

## V1-V5 回顾

```text
V1  移植 Pico runtime 到 web          ← "能跑"
V2  前端三栏 + Skills 系统              ← "能用"
V3  Prompt caching + Approval + History ← "能存"
V4  Runtime Contract + 7层 Prompt       ← "能测"
V5  权限治理 + Plan 审批 + 流式修复      ← "能控"
```

V5 建立了治理边界（4-mode 权限 + plan 审批 + 危险命令检测 + 工具循环检测 + 流式泄露修复）。agent 的控制面从"模型说了算"升级成"用户说了算"。

## V6 主线

> **V6 把 Sage 从"有治理边界的 coding workbench"推进到"可观测、可记忆、可复现的 agent 平台"。**

V5 解决了"agent 能不能被控制"。V6 解决三个新问题：

| 问题 | V6 解决方案 | 对标 |
|------|-----------|------|
| **agent 改了什么看不见** | Workspace diff 追踪 + diff 可视化 | hermes-studio workspace-diff-tracker |
| **agent 没有长期记忆** | Memory 系统（working + durable + dream） | Pico 四层记忆 + Hermes MemoryProvider |
| **前端太朴素** | Naive UI + monaco + xterm | hermes-studio 技术栈 |
| **不知道 agent 好不好** | Benchmark + 量化指标 | Pico evaluation + Claw Code parity harness |

---

## 四个阶段

### Phase A：前端体验升级（Naive UI + monaco + xterm）

**目标**：把前端从手写 CSS 升级到生产级 coding workbench UI。

| 任务 | 内容 | 依赖 |
|------|------|------|
| A1 Naive UI 引入 | 安装 + NConfigProvider 主题 + 逐个组件迁移（NLayout/NMenu/NCollapse/NInput/NSelect/NButton/NTree/NTag） | 无 |
| A2 monaco 编辑器 | 安装 + manualChunks 分包 + CodingCodeEditor.vue 替换文件预览 `<pre>` | A1 |
| A3 xterm 终端 | 安装 + 后端 WebSocket 终端端点 + CodingTerminal.vue + 右栏 Files/Terminal tab | A1 |
| A4 diff 可视化 | 工具结果 diff 自动识别高亮 + CodingDiffDrawer.vue（monaco DiffEditor） | A2 |

**验收**：
- `npm run build` 通过（monaco/xterm 分包正确，首屏不背大包）
- 文件预览有语法高亮
- 网页里能开终端跑命令
- 工具结果中的 diff 自动高亮

**不做**：
- 不引入 vue-flow / mermaid（暂不需要）
- 不做 i18n
- 不引入 hermes-studio 的 Koa BFF / Electron / Socket.IO

### Phase B：Workspace Diff 追踪

**目标**：agent 每次运行改了什么文件，能 diff 可视化。

```text
agent run 开始前
  -> WorkspaceDiffTracker.snapshot_before_run()
  -> git status --porcelain + 每个文件内容快照

agent run 结束后
  -> WorkspaceDiffTracker.snapshot_after_run(before)
  -> 对比 before/after，生成每个文件的 unified diff
  -> yield workspace_diff 事件

前端收到 workspace_diff
  -> CodingDiffDrawer 显示"本次运行修改了 N 个文件"
  -> 点击文件名 -> monaco DiffEditor 展示
```

| 任务 | 内容 | 依赖 |
|------|------|------|
| B1 后端 WorkspaceDiffTracker | `core/coding/context/workspace_diff.py`，git 快照 + diff 生成 | 无 |
| B2 事件 + API | `WorkspaceDiffEvent` 加到 events.py + run_turn 里注入 | B1 |
| B3 前端 diff drawer | `CodingDiffDrawer.vue`（NDrawer + monaco DiffEditor） | A2 + A4 |
| B4 run history 关联 | run summary 里加 `changed_files` 字段 | B1 |

**验收**：
- agent 修改文件后，前端显示 workspace diff drawer
- diff drawer 里能看每个文件的 unified diff（monaco DiffEditor）
- run history 里显示每次 run 改了哪些文件

### Phase C：Memory 系统

**目标**：agent 能跨会话记住项目约定、用户偏好、关键决策。

```text
core/coding/memory/
├── __init__.py
├── working.py         ← 工作记忆（session 内：任务摘要 + 最近文件 + 文件摘要）
├── durable.py         ← 长期记忆（磁盘：project-conventions / key-decisions / user-preferences / dependency-facts）
├── dream.py           ← 记忆整理（daily log -> durable topic，后台或手动触发）
└── manager.py         ← MemoryManager（组合 working + durable + dream）
```

| 任务 | 内容 | 依赖 |
|------|------|------|
| C1 工作记忆 | `working.py`：任务摘要 + 最近接触文件（上限 8）+ 文件短摘要（上限 6，freshness 校验） | 无 |
| C2 长期记忆 | `durable.py`：4 类 topic 文件 + MEMORY.md 索引 + `/remember` 写入 daily log | C1 |
| C3 记忆注入 prompt | `context/manager.py`：working memory 注入 volatile 层，durable topics 索引注入 context 层 | C1 + C2 |
| C4 记忆整理 | `dream.py`：daily log -> durable topic 的 LLM 整理（手动 `/dream` 触发，第一版不做 auto-dream） | C2 |
| C5 前端记忆面板 | `CodingSidebar.vue` 加 Memory section：显示 MEMORY.md 索引 + `/remember` / `/dream` 按钮 | C2 |

**验收**：
- 会话 A 说"记住这个项目用 pytest" -> `/remember` -> daily log
- `/dream` 整理 -> durable topic `project-conventions.md` 写入
- 会话 B 的 system prompt 里有 durable topics 索引
- 前端侧栏显示记忆索引

**不做**：
- 不做 auto-dream 后台定时（第一版手动 `/dream`）
- 不做 Mem0/Qdrant 集成（第一版纯文件 + LLM 整理）
- 不做跨用户记忆隔离（单用户）

### Phase D：Benchmark + 量化指标

**目标**：能量化回答"Sage 的 agent 好不好"。

```text
evals/coding/
├── scenarios/          ← 固定 coding 任务（10-20 个）
│   ├── read_and_explain.py    ← "读 README.md 解释项目"
│   ├── fix_typo.py            ← "修复 src/app.py 里的 typo"
│   ├── add_test.py            ← "给 utils.py 加单元测试"
│   └── refactor.py            ← "把 func A 重构成 func B"
├── runner.py           ← 跑场景 + 收集指标
├── metrics.py          ← 指标定义
└── results/            ← 历史结果
```

| 指标 | 定义 | 目标 |
|------|------|------|
| task_completion_rate | 场景跑完后文件状态正确的比例 | >= 70% |
| tool_call_success_rate | 非错误 tool_result 占比 | >= 85% |
| avg_tool_steps | 平均工具调用次数 | <= 8 |
| first_pass_test_success | 改完代码后测试首次通过率 | >= 60% |
| p95_turn_latency | 单轮 P95 延迟 | < 15s |
| prompt_cache_hit_proxy | 同 session system prompt rebuild 次数 | <= 1 |

| 任务 | 内容 | 依赖 |
|------|------|------|
| D1 场景定义 | 10-20 个固定 coding 任务，每个有预期文件状态 | 无 |
| D2 runner | 用 ScriptedApiClient 或真实 API 跑场景 + 收集 trace | D1 |
| D3 指标计算 | 从 trace + 文件状态计算指标 | D2 |
| D4 报告 | 生成 benchmark 报告（JSON + markdown） | D3 |

**验收**：
- `python -m evals.coding.runner` 能跑全部场景
- 输出 benchmark 报告
- 至少有 task_completion_rate + tool_call_success_rate 两个指标

---

## 执行顺序 + 依赖

```text
Phase A（前端升级）         Phase B（Workspace Diff）    Phase C（Memory）
  A1 Naive UI                 B1 WorkspaceDiffTracker      C1 工作记忆
  A2 monaco                   B2 事件 + API                 C2 长期记忆
  A3 xterm                    B3 前端 diff drawer           C3 注入 prompt
  A4 diff 可视化              B4 run history 关联           C4 记忆整理
       │                           │                              │
       └───────────┬───────────────┘                              │
                   ▼                                               │
             Phase D（Benchmark）◄──────────────────────────────────┘
               D1 场景定义
               D2 runner
               D3 指标计算
               D4 报告
```

- **A 和 C 可以并行**（前端升级和后端记忆系统不冲突）
- **B 依赖 A2 + A4**（diff drawer 用 monaco DiffEditor）
- **D 最后做**（需要 A/B/C 都稳定后才有意义）
- **每个 Phase 完成后跑全量测试 + build**

## 非目标

- 不移植 Hermes 的 provider profile / plugin / gateway / cron / 多平台
- 不移植 Claw Code 的 Lane Events / Report Schema / ApprovalToken / PolicyEngine
- 不做 live run reattach / stream reattach（V7 再考虑）
- 不做 MCP 双向（作为 server 暴露）
- 不做 Skill 自学习闭环 / Curator
- 不做多用户 / 认证 / 部署

## 面试叙事升级

```text
V1-V5 叙事："我做了个能控的 coding agent"
V6 叙事：   "我做了个可观测（diff 追踪）、可记忆（四层记忆）、可复现（benchmark）
            的 agent 平台，用 Naive UI + monaco + xterm 做了生产级 workbench UI"
```

量化指标（V6 做完后可讲）：
- task_completion_rate: X%
- tool_call_success_rate: X%
- prompt_cache_hit: session 内 rebuild <= 1 次
- workspace diff: 每次 run 的文件变更可追溯
- memory: 跨会话偏好记忆准确率
