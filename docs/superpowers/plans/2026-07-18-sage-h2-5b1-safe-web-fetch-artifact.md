# Sage H2.5B1 安全网页抓取与 Artifact 计划

**目标：** 在 H2.5A Search 和 H2.5B0 本轮预算门禁之上，交付第一条可独立验收的网页正文链路：只抓取公开 HTTPS HTML，完整正文进入私有 Artifact，模型和 timeline 只接收预算内 excerpt 与引用元数据。

## 1. 复用边界

| 能力 | 复用模块 | H2.5B1 用法 |
| --- | --- | --- |
| DNS/IP 校验 | `core/cloud/model_providers/network.py` | 抽出共享公共目的地判断；抓取前解析并固定允许地址 |
| 私有 Artifact | `ToolResultStore` / `ToolArtifactPort` | 完整正文以 `0600` 原子写入当前 session/run，不进入 checkpoint |
| 当前回答引用 | `WebSearchPort` / public timeline sanitizer | 返回 `citation_id`、canonical URL、retrieved time、content hash 和 bounded excerpt |
| 能力发现 | Capability Registry + deferred tools | `fetch_web` 只在服务端显式启用时出现，保持首轮 tool schema 轻量 |

不复用 Knowledge Parse Artifact 作为网页临时缓存。Knowledge Source 是用户确认后的持久来源；当前回答的 Web Artifact 属于 run-scoped evidence，生命周期和权限不同。

## 2. H2.5B1 纵向切片

```text
fetch_web(url, token_budget)
  -> 只接受无凭据的公开 HTTPS URL
  -> DNS 解析并拒绝 private / loopback / link-local / metadata / multicast
  -> 将请求固定到已校验地址，禁用环境代理
  -> redirect 不自动跟随；逐跳重新校验，最多 3 跳
  -> connect/read/total timeout + 最大响应字节数
  -> 只允许 text/html；拒绝压缩炸弹和类型漂移
  -> 提取 title 与可读文本，规范化空白
  -> 完整文本写 ToolResultStore
  -> 模型只收到预算内 excerpt + artifact_ref + citation metadata
  -> timeline 只公开 allowlist 字段
```

默认边界：

- `token_budget=3000`，允许 `256..8000`；
- 最多 3 次 redirect，每跳重新做 URL、DNS 和 IP 校验；
- 连接 5 秒、读取 10 秒、总请求 20 秒；
- wire body 最多 2 MiB，解压后 HTML 最多 4 MiB；
- Artifact 只保留规范化正文，不保留 Cookie、Authorization、响应头或原始 HTML 脚本。

## 3. 安全不变量

1. 禁止 URL 用户名、密码、fragment 和非 HTTPS scheme。
2. 禁止 localhost、`.local`、metadata hostname、IP literal 私网和 DNS 解析出的任一非公网地址。
3. 请求不读取浏览器登录态，不发送 Cookie/Authorization，不信任系统代理。
4. redirect 必须重新解析和固定目标；不能把初始公网校验当作后续跳转授权。
5. 响应内容、页面标题和正文都是不可信数据；正文中的指令不能改变 Harness 权限。
6. 完整正文不进入 LangGraph state；checkpoint 只保留小型 JSON 结果和 opaque `sage://` 引用。
7. 抓取结果不会自动进入 Knowledge、Wiki 或 Memory。

## 4. 非目标

- 不支持 PDF、图片、音视频、登录页、Browser Use 或 JavaScript 渲染。
- 不做站点级 crawler、robots 调度、批量并行抓取或跨页聚合。
- 不提供 Artifact 任意路径读取 API；预览继续通过既有受限引用契约。
- 不在本切片实现“保存为知识来源”；该能力属于 H2.5C 用户确认提案。

## 5. 验收

- 公网 HTTPS HTML 返回 `evidence_found`、预算内 excerpt、稳定 citation 和私有 artifact_ref。
- private/loopback、DNS 混合公网私网、redirect 到私网、超时、超限和错误 media type 均 fail closed。
- Artifact 内容完整，ToolMessage/checkpoint/timeline 不含超预算正文。
- `fetch_web` 可延迟发现；能力关闭时不会进入目录或模型工具列表。
- 后端关联测试、前端投影测试（如有）、Ruff、mypy、生产构建和 `git diff --check` 通过。

## 6. 后续

- H2.5B2：PDF allowlist、下载 Artifact 和受限解析摘要。
- H2.5C：将已引用 Web Artifact 提议为 Knowledge Source，必须经过用户确认、revision 固定和去重。
- H2.6：Research Subagent 使用 Search + Fetch，但拥有独立 token、tool、wall-clock 与 artifact budget。
