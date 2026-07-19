# Sage H2.5B2 异步文档解析实施计划

> 日期：2026-07-18
>
> 基线：`dev/sage-v7@adf71b5`
>
> 状态：本地门禁完成，待 PR 合入 `dev/sage-v7`
>
> 官方契约：<https://mineru.net/apiManage/docs>

## 1. 问题

Sage 已有 Redis Streams Knowledge Job、MinerU Agent Adapter 和本地 PDF 文本层解析器，
但 MinerU Adapter 会在一次 worker lease 内持续轮询远程任务。一个慢 PDF 会长期占用唯一的
Knowledge worker，后续来源无法公平推进，进程重启后也只能重新提交远程任务。

## 2. 决策

H2.5B2 不建立第二套队列：

- PostgreSQL 继续是任务状态事实源；
- Redis Streams 继续只负责至少一次投递；
- MinerU 的 `task_id` 作为 server-only ticket 持久化；
- 每次 worker 只执行一次 submit 或 poll，未完成就释放 lease，并设置 `next_poll_at`；
- 到期后由 reconcile 重新发布，进程重启后继续查询同一 MinerU 任务；
- 外部解析显式启用且来源在 allowlist 时，PDF 使用 MinerU-first；
- MinerU 不可用、超时或返回失败时，尝试现有 `TextPdfParser`；本地也无法解析时才进入重试或 dead letter。

## 3. MinerU 契约

当前默认采用无需 Token 的 Agent 轻量解析 API，因为它支持本地文件签名上传：

1. `POST /api/v1/agent/parse/file` 获取 `task_id` 和签名 `file_url`；
2. `PUT file_url` 上传不超过 10 MB、20 页的文件；
3. `GET /api/v1/agent/parse/{task_id}` 查询一次状态；
4. `done` 后从受信任 CDN 下载有界 Markdown。

精准解析 API 的 200 MB、200 页和 `vlm` 模型需要用户 Token；本小版本不新增凭据页面，
后续只需增加一个实现相同 resumable port 的 Adapter，不改变队列状态机。

## 4. 状态机

```text
queued / retry_wait
  -> claimed -> parsing
       -> MinerU submit -> external_wait(next_poll_at)
       -> MinerU poll pending/running -> external_wait(next_poll_at)
       -> MinerU done -> understanding -> applying -> completed
       -> MinerU failed/timeout -> local parser
            -> success -> understanding -> applying -> completed
            -> failure -> retry_wait / dead_letter
```

`external_wait` 不持有 worker lease，也不增加 attempts；只有新的完整解析尝试才增加 attempts。

## 5. 安全与非目标

- 不在 event、API、日志或前端暴露 MinerU `task_id`、签名上传 URL 或 Token；
- 外部解析仍默认关闭，并受 source allowlist、敏感路径和 payload 大小限制；
- 只接受 MinerU 与 OpenDataLab/阿里云受信任 HTTPS 资产域名；
- 不实现回调公网入口，不让 MinerU 访问 Sage 内网；
- 不在本小版本解析 MinerU Zip 中的图片、布局 JSON 或公式资产；
- 不自动批准或写入 Wiki，完成后仍产生可审核 proposal。

## 6. 验收

1. MinerU pending 时一次 `run_once` 立即返回，item 进入 `external_wait`；
2. 第二个 Knowledge item 可以在前一个 PDF 等待期间继续执行；
3. 服务重启后复用原 `task_id`，不重复上传；
4. 轮询不增加 attempts，网络失败仍受 max attempts 限制；
5. MinerU 失败而本地文本层可读时完成，并记录 fallback；
6. 取消、超时和 dead letter 均有 durable event；
7. 定向测试、后端全量、Ruff、mypy、前端回归、生产构建和 `git diff --check` 通过。

## 7. 交付证据（2026-07-18）

- MinerU Adapter 拆为一次 submit / 一次 poll 的 resumable 契约；
- PostgreSQL 持久化 server-only ticket，Redis Streams 只承担到期投递；
- `external_wait` 释放 worker lease，等待期间后续 Markdown item 可完成；
- 服务重启复用原任务，不重复上传，轮询不增加 attempts；
- MinerU 失败或总等待超时后尝试本地 PDF 文本层解析，成功后票据终态为 `fallback`；
- 取消等待任务会同时终止 item 与外部票据，且不会再次进入 ready queue；
- 定向：`32 passed`；后端全量：`1568 passed`；
- Ruff 全仓 lint：通过；mypy：`229 source files` 无错误；
- 前端：`400 passed`；生产构建通过；`git diff --check` 通过；
- 全仓 `ruff format --check` 仍受基线 144 个历史文件影响；本 PR 涉及的 12 个 Python 文件全部通过 format check。
- 真实 MinerU 合成 PDF smoke：`uploading -> queued -> running -> completed`，返回 1 个 block、44 个 Markdown 字符。

真实 MinerU 上传不进入自动测试，避免把用户资料发送给第三方。手工联调只使用合成 PDF，
并继续要求显式启用外部解析和配置 source allowlist。
