# Sage V7.2 可审核知识沉淀设计

> 日期：2026-07-15
> 集成分支：`dev/sage-v7`
> 短期分支：`codex/feat-v7-2-auditable-knowledge`
> 知识仓库：`git@github.com:ZeroMadLife/Sage-knowledge.git`

## 1. 目标

V7.2 第一阶段交付一个可运行的知识写入闭环：

```text
Obsidian Markdown
  -> immutable raw snapshot
  -> Wiki Proposal + diff
  -> approve / reject
  -> versioned Markdown Wiki + Git commit
  -> rollback proposal
  -> approve rollback as a new revision
```

知识仓库采用 Karpathy LLM Wiki 的三层边界：Raw Sources、Wiki、Schema，并保留 `index.md` 与 append-only `log.md`。Sage 增加异步审核、乐观锁、稳定来源 revision 和 Git 版本记录，但不复制 `nashsu/llm_wiki` 的 GPLv3 源码、UI 或资源。

## 2. 仓库边界

`Sage-knowledge` 是独立 Knowledge Repository：

```text
Sage-knowledge/
├── purpose.md
├── schema.md
├── index.md
├── overview.md
├── log.md
├── raw/sources/
├── wiki/sources/
├── wiki/projects/
├── wiki/concepts/
├── wiki/decisions/
├── wiki/queries/
├── wiki/learning/
└── reviews/
```

应用数据库只保存 proposal、审核事件和 revision 元数据。Raw 与 Wiki Markdown 进入 Git；检索索引以后可以从 Git 产物重建，不作为 canonical truth。

## 3. 安全模型

- 本阶段只开放配置好的 source root，默认白名单为 Obsidian 的 `sage-learning`。
- API 只接受 `source_root_id` 和相对路径，不接受浏览器提交任意绝对路径。
- 拒绝 `..`、符号链接、非普通文件、非 UTF-8、非 Markdown 和超过 2 MiB 的来源。
- Raw snapshot 使用 SHA-256 内容寻址，同一 revision 不重复写入且永不覆盖。
- 所有 Wiki 写操作先生成 proposal；`approve/reject` 使用 `expected_revision`。
- Wiki 页面在 proposal 生成后发生变化时，审批必须冲突失败，不能覆盖新 revision。
- 回滚不是 `git reset`，而是从旧 revision 创建新 proposal，审批后产生新的 Git commit。
- 本阶段不自动 push Knowledge Repository；本地 Git commit 通过后再由显式同步或 CI/CD 推送。
- 云端多租户尚未完成时，Knowledge 写 API 只开放本地开发模式，生产环境 fail closed。

## 4. V7.2-P2.1 公共 API

- `GET /api/v1/knowledge`：workspace、source、page、pending proposal 摘要。
- `POST /api/v1/knowledge/ingest`：读取一个白名单 Markdown，生成 raw snapshot 与 proposal。
- `GET /api/v1/knowledge/proposals`：按状态查看提案。
- `POST /api/v1/knowledge/proposals/{id}/approve`：乐观锁批准并投影到 Git Wiki。
- `POST /api/v1/knowledge/proposals/{id}/reject`：拒绝并保留审计事件。
- `GET /api/v1/knowledge/pages`：页面与 bounded revision 历史。
- `POST /api/v1/knowledge/pages/{page_id}/rollback`：从旧 revision 生成回滚 proposal。

## 5. 本阶段非目标

- 不做文件夹递归监听和批量摄取队列；
- 不调用 LLM 自动综合概念页，先固定审计状态机与 Git 投影；
- 不做 embedding、pgvector、RRF 或 Agentic Retrieval；
- 不接飞书真实凭据；飞书后续实现 `KnowledgeSourceAdapter`，输出同样的 immutable snapshot；
- 不自动上传整个 Obsidian Vault，也不上传未通过密钥扫描的来源；
- 不自动 push、merge 或 publish public package。

## 6. 验收证据

1. 同一 Markdown revision 重复 ingest 幂等；
2. 未批准前 Wiki 不变化；
3. approve 后存在 Git commit、Wiki 页面、index 与 log；
4. reject 后无法投影；
5. 陈旧 base revision 审批返回 conflict；
6. rollback 先 pending，批准后形成新 revision，旧 revision 仍可查询；
7. 首页 knowledge 与 wiki pending 计数来自真实 KnowledgeStore；
8. 前端可以导入单文件、审核、拒绝和生成回滚提案；
9. 路径、密钥、原始来源正文不会出现在首页摘要。
