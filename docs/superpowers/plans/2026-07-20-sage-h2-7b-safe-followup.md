# Sage H2.7B 安全自动跟进实施计划

## 目标

在 H2.7A 的 revisioned Thread Goal 之上增加 durable post-turn evaluation 与受控自动跟进。自动跟进默认关闭；只有用户显式启用后，系统才允许在同一 Goal 下启动下一轮。所有决定必须来自已落盘 timeline 与公开证据，不读取私有推理，不创建第二套 Chat runtime。

## 契约

1. Goal 增加 `manual | bounded_auto` continuation policy；自动模式最多配置 1 至 4 次跟进，默认仍为 `manual`。
2. 每个 completed/error/interrupted Run 的 terminal 落盘后，使用该 Run 冻结的 Goal revision 做 post-turn evaluation；stale Goal、活动 Run 或更新的用户输入使该评估 fail-closed。
3. 结构化 evaluator 只接收 bounded public trace、完成标准和服务端生成的 evidence catalog。模型输出必须通过严格单 JSON、typed blocker、引用白名单和 criterion coverage 校验。
4. `satisfied` 必须让每条完成标准都有 `met` 结果和允许的 evidence ref；否则降级为 `continue/missing_evidence`，不能把模型自评当掌握度。
5. 自动跟进只允许 `continue + goal_not_met_yet + bounded next_action`；两次无新证据、次数上限、审批等待、运行失败、用户输入或 Goal revision 变化都会停止。
6. evaluation 与 follow-up reservation 在同一个 journal 事务中落盘。follow-up run id 由 source run 和 Goal revision 确定生成；重启或多 worker 竞争最多启动一次。
7. follow-up 复用原 Run 的 Surface Context、同一 CodingRuntime、RunCoordinator、预算、审批、Sandbox、MCP 与 timeline。内部 continuation 不伪装成用户手工消息。
8. 进程在 terminal、evaluation、reservation 或 run start 任一点退出后，session resume 只能恢复未消费 reservation，不能重复已经开始或已终态的 follow-up。

## 本轮切片

1. `core/harness`：严格 evaluator、evidence catalog、continuation state machine。
2. `SessionEventJournal`：原子 post-turn commit、follow-up reservation 与 pending recovery。
3. `api/coding.py`：terminal callback、恢复 reconcile 和共享 runtime follow-up start。
4. API：continuation policy 更新与真实 Goal 状态回显；前端仅做契约接入，不改主视觉布局。
5. 验证：结构化输出攻击、引用伪造、无进展、次数上限、用户抢占、双 worker、断线/重启和预算回归。

## 明确不做

- 不实现 H2.7C Mastery Ledger、能力权重或掌握度百分比。
- 不让公开博客 Agent 共享私有 Coding workspace、Tool、Memory 或 Knowledge corpus。
- 不以 chain-of-thought、前端动画或未持久化状态驱动 Goal 结论。
- 不把自动跟进默认打开，也不允许无限循环。

## 收口证据

- 后端全量：`1702 passed`，保留 1 条 GPT-2 tokenizer fallback warning。
- Harness/Goal 与恢复定向：关键测试组 `121 passed`，Harness/预算相关切片 `150 passed`。
- 前端：`63` 个测试文件、`463 passed`；生产构建和公开门面构建均通过。
- 静态门禁：mypy 9 个后端源文件通过，ruff changed-file 检查通过，`git diff --check` 通过。
- 已覆盖：严格 JSON 与引用白名单、输出/trace 限额、无进展停止、次数上限、用户抢占、重启恢复、双 worker 去重、receipt 防篡改、失败运行无额外模型调用、真实 evaluator usage 记账。

## 下一阶段边界

H2.7C 继续实现 `Mastery Ledger` 与能力权重，但必须消费本阶段落盘的 criteria/evidence receipt，不能让模型直接写掌握度。博客 Agent 可在本阶段合入 `dev/sage-v7` 后并行启动，但只能使用独立的公开资料集、公开能力注册表和独立用量/限流策略，禁止访问 Coding 私有 workspace、Memory、Tools 或 Knowledge corpus。
