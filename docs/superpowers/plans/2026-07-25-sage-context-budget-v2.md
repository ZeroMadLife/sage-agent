# Sage Context Budget v2 实施计划

## 阶段 1：冻结 baseline

- 增加固定合成长任务与 A0 报告；
- 记录旧六级 `compact` 首次可达 token、run budget 和 Artifact 只能 offload 的事实；
- 报告包含数据集版本、代码 commit、配置和逐 case 结果。

## 阶段 2：Graph 工作集预算

- 扩展 `HarnessConfig` 并保持旧构造调用兼容；
- 新增每次模型调用前的工作集计量事件；
- 公开检索配置使用更小的工作集和保留目标；
- 将旧 host 事件标为 hard-window scope。

## 阶段 3：循环内压缩

- 先写触发、安全 cutoff、失败保留、无效压缩和 cooldown 测试；
- 实现结构化迭代摘要与 checkpoint-safe 状态；
- 将摘要模型 usage 纳入 run budget；
- 补 Timeline 事件适配和同轮工具结果触发集成测试。

## 阶段 4：Artifact 回载

- 为 `ToolResultStore` 增加 byte-range 读取与 URI scope 解析；
- 注册 resident `load_artifact` 工具；
- 验证当前 run、历史 run、跨 session、软链接、上限和翻页游标。

## 阶段 5：消融与一次阈值优化

- 运行 A0-A3；
- 对候选工作集/保留比做固定扫描；
- 只根据预先声明的指标选择一次默认阈值；
- 用选定阈值重跑并生成 JSON/Markdown 报告。

## 阶段 6：收口

- 执行受影响 pytest、完整后端测试、前端生产构建和 `git diff --check`；
- 用 logic-lens 审查持久化、并发、失败和安全边界；
- 更新 README、简历证据文档和 Obsidian `sage-learning`；
- 分职责提交，推送 `codex/context-budget-v2`，创建中文 PR 到 `dev/sage-v7`。
