# Sage H2.5C Knowledge Source Proposal 实施计划

> 日期：2026-07-18
>
> 基线：`dev/sage-v7@9a4d803`
>
> 前置：H2.5B1 Web Artifact、H2.5B2 async document parsing

## 1. 目标

把已经进入当前回答的 Web Evidence 转成可审计的知识来源提案。用户确认前不改变
Knowledge revision；确认后复用现有 manifest、sync plan、Redis delivery、parser、Wiki
proposal、index 和 graph 链路。

## 2. 不做的内容

- 不新增第二套 Agent Runtime、队列或前端状态源；
- 不自动保存搜索结果，不重新抓取已确认网页；
- 不在本阶段实现 Goal/Mastery/Knowledge Unit 全量模型；
- 不把 PDF 远程轮询放回请求线程；
- 不允许模型批准自己创建的 Proposal。

## 3. 纵向数据流

```text
fetch_web
  -> session/run-scoped text artifact + server-owned hash-bound metadata sidecar
  -> save_web_source(artifact_ref, reason, evidence_refs)
  -> pending source proposal (PostgreSQL + event trail)
  -> user approve(expected_revision)
  -> verify owner/session/run/artifact/hash
  -> write immutable Markdown snapshot into server-owned web source root
  -> create deterministic sync plan + durable Knowledge job
  -> parser / understanding / Wiki proposal
  -> existing Wiki approval or policy
```

## 4. 核心设计

### 4.1 Artifact

`fetch_web` 保持正文私有，只增加 server-only metadata sidecar。sidecar 包含 canonical URL、
title、retrieved_at、MIME、wire bytes 和 content hash；读取时重新计算正文 hash。

### 4.2 Proposal

PostgreSQL 保存控制面，不保存完整网页正文：

- `proposal_id/workspace_id/owner_id/thread_id/run_id`；
- `artifact_ref/canonical_url/title/media_type/content_hash`；
- `reason/evidence_refs/status/revision/last_error`；
- `target_root_id/target_relative_path/job_id`；
- created/updated/decided timestamps 与事件序列。

相同 workspace、thread、run、artifact 和 hash 幂等返回同一 proposal。

### 4.3 审批

approve/reject 使用 `expected_revision` CAS。approve 先 claim `applying`，再执行幂等快照与
Job 创建；安全失败回到 pending 并增加 revision，允许用户重试。重复或冲突决定返回 409。

### 4.4 Source Root

使用 server-owned `web-evidence` root；浏览器不提交文件系统路径。批准后的每个 proposal
写入独立目录，Knowledge Job 仅扫描该目录，避免一次确认重新处理全部网页。

## 5. 验证

1. artifact metadata/hash、symlink 与跨 scope 负例；
2. proposal create/list/get、owner/thread 隔离、幂等；
3. approve/reject CAS、重复决定与失败重试；
4. approve 后产生一个 durable Job，未 approve 时 Knowledge revision 不变；
5. worker 完成后产生现有 Wiki proposal，正文引用原 URL/hash；
6. 后端定向、全量、Ruff、mypy、前端测试/build、`git diff --check`。
