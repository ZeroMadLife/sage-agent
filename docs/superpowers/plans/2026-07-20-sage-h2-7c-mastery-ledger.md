# Sage H2.7C Mastery Ledger 实施计划

## 目标

把一次 Harness 运行中产生的、可验证的学习证据沉淀为跨会话的确定性投影。掌握度不是模型自评，也不把知识库页面、Memory fact 或聊天文本直接当成能力证明。

## H2.7C 范围

- `core/learning/` 提供与 Harness、Knowledge、Memory 解耦的 Ledger 和 rubric v1；
- 证据按 `workspace_id + learning_goal_id + goal_revision + capability_id` 隔离；
- 相同 `evidence_id` 重放幂等，字段不一致视为冲突；
- `code_test`、`quiz`、`explanation`、`artifact`、`project`、`citation` 使用统一证据契约；当前 Practice 子代理只产生受控 `code_test` 候选；
- 投影使用服务端固定 rubric：按结果归一化、按证据类型折算、取每类最新有效信号；单一证据类型最高只能得到部分进度；
- 只读 Knowledge API 返回能力投影、原始引用和 rubric revision；证据失效使用 expected revision CAS；
- Thread Goal 的 Learning Goal 绑定和 outbox 记录下一切片接入，绑定快照必须包含 goal revision 与能力权重。

## 持久化与恢复

评价结果先随 Thread Goal evaluation durable 写入 session journal，记录待投影的证据摘要；Ledger 写入成功后再补一条 `mastery_evidence_recorded` receipt。恢复时扫描未确认 outbox，重复写入由 `evidence_id` 幂等保护。

## 非目标

- 不允许模型直接提交掌握百分比；
- 不自动把 Wiki、Memory、聊天回答写成长期掌握证据；
- 不在本切片实现测验 UI、rubric 编辑器、Web Search 或公开 Agent；
- 不修改 Public Release Pipeline 和 Knowledge 图谱前端。

## 验收

1. 一个通过的 Practice code test 可见，但只能形成部分进度；再有一类独立有效证据才可达到“已证明”。
2. 失败测试、失效证据和新 goal revision 会重新计算，不影响其他 workspace/goal revision。
3. 同一事件重放不会产生重复证据或重复进度。
4. API 只返回 bounded summary/ref，不返回工具内部大文本。

## 本切片已交付

- Thread Goal 已冻结 Learning Goal revision、能力权重和 criterion 映射；completion criteria 变更时必须重新提交绑定，避免旧证据错挂。
- Practice candidate 只有在可验证的 `code_test`、`pass`、已完成 subagent operation ref 且引用仍在白名单时才会进入 deposit。
- session journal 的 deposit/receipt 构成恢复安全的 outbox；receipt 查询按 `source_event_id` 反连接，不依赖最近窗口。
- Ledger 批量写入在单一事务内完成，冲突会整体回滚；receipt 后续重放保持幂等。

## 下一切片

H2.7D 负责用户可见的 rubric/证据校正和 Learning Goal 进度消费：保持本切片的固定 `sage-mastery-v1` 投影不变，先新增显式 proposal/CAS 契约，再开放前端编辑；不允许通过模型输出直接改变分数。
