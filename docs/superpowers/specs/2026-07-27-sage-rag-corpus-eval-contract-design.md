# Sage RAG Corpus / Eval Contract v1 设计

> 日期：2026-07-27
> 基线：`dev/sage-v7@3e799ff`
> 阶段：PR-1

## 1. 决策

Corpus、Eval 和运行报告分开版本化。worktree 只隔离代码，不为每份数据集创建一个 worktree。
可信语料必须先形成带官方来源、commit、许可证、raw hash、snapshot hash 和审批状态的
manifest；搜索结果只负责定位候选，不直接进入可信 corpus。

## 2. 首批来源

首批范围是 LangGraph 官方文档与 `BaseCheckpointSaver` 源码、PostgreSQL 官方全文检索/GIN
文档、pgvector 官方 README，以及 FastAPI 官方依赖注入文档与 dependency resolver 源码。
只截取后续 PR 会真实评测的核心段落，避免把整个上游仓库复制进项目。每个 Markdown 快照
都可回到 exact commit 的原文件复核。

## 3. Eval 约束

Eval v1 固定 80 条，`dev/calibration/test=40/20/20`。case 保存 answerability、modality、
required sources/passages/claims、forbidden claims 和未来视觉定位字段。`leakage_group` 禁止同一
意图的改写跨 split；test 只读，不能用于 Gate 选择。

## 4. 运行时门禁

`core.knowledge.datasets` 使用严格 Pydantic 合同加载 JSONL，并验证：

- 所有 manifest/case 字段完整且无额外字段；
- URL 绑定 commit，路径不能逃逸仓库；
- manifest、cases、snapshot 内容 SHA-256 一致；
- corpus ID、case ID 和规范化 query 唯一；
- answerable 与 gold evidence 一致；
- 所有引用指向 approved corpus，leakage group 不跨 split；
- 实际 split 计数与 dataset manifest 一致。

## 5. 非目标

- 本 PR 不运行 SQLite/PostgreSQL baseline，不产生检索提升数字。
- 本 PR 不实现联网自动抓取器；自动同步必须另设审批与许可证门禁。
- 本 PR 不把官方文档快照包装成企业私有语料，也不把人工 80 题描述成线上准确率。
