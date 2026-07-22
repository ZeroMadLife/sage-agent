# Sage Public Agent P1：公开资料包与隔离问答服务

> 日期：2026-07-22
> 基线：`dev/sage-v7@918dcee`
> 范围：独立 public-only API、不可变资料包、引用回执、限流、泄漏防护与镜像门禁

## 1. 目标

把公开门面的确定性本地匹配升级为可部署的受限 Agent 后端，同时保持当前公网静态容器已经验证的安全边界。公开 Agent 不是私人 Sage 的匿名入口，也不复用 Coding Session、Memory、Knowledge 数据库、工作区工具或 Provider 配置。

## 2. P1 交付

```text
versioned public JSON package
  -> digest verification
  -> bounded lexical retrieval (top 3)
  -> public-only model prompt
  -> answer + evidence citations + package receipt
```

- 独立 `public_agent.app` FastAPI 进程，仅开放 `/healthz` 与 `/api/public/v1/ask`。
- 独立 `SAGE_PUBLIC_AGENT_API_KEY / BASE_URL / MODEL`，不读取私人 Provider Repository。
- 公开资料在 `data/public/sage-public-v1.json` 中版本化；每个文档校验内容 SHA-256。
- 每次回答返回 document revision、公开 URL、内容 hash、package digest 与 request receipt。
- 每 IP 滑动窗口限流；不信任浏览器可伪造的 `X-Forwarded-For`。
- 当前 P1 只按直连 socket 地址限流；P2 接入 Caddy 时必须由受信代理生成客户端标识，不能让所有访客共享一个代理地址配额，也不能直接信任公网转发头。
- 服务启动时扫描整个公开 JSON 包（包括未知附加字段），命中本地路径、凭据赋值或常见 token 前缀即拒绝加载。
- 私有资料请求和 prompt injection 在模型调用前拒绝；模型输出若包含本地路径或凭据形态则安全替换。
- 无命中不调用模型，不消耗 Provider token。
- 专用 Docker 镜像只复制 `public_agent/` 和 `data/public/`，不复制 `api/`、`core/`、`.env` 或工作区。
- 继续复用现有 `public-release` check 名称，同时验证静态门面和 Agent 镜像；不改变 Canary 必需 check 列表。

## 3. P1 不代表已经上线

P1 不修改公网 Caddy 的 `connect-src 'none'`，也不让生产发布控制器启动 Agent 容器。当前博客仍使用静态公开问答。只有 P2 完成以下门禁后，前端才切换为真实 Agent：

1. root-owned 发布控制器支持同 SHA 的静态门面与 Agent 双镜像候选探活、原子切换和共同回滚；
2. Agent 容器只加入独立 public network，端口不直接暴露公网；
3. Caddy 仅把 `/api/public/v1/ask` 反向代理到 Agent，并把 CSP 收窄为 `connect-src 'self'`；
4. 服务器单独注入公开模型凭据，不注入 `/etc/sage/env` 或私人 Provider 配置；
5. 完成真实 Provider smoke、并发限流、超时、费用上限、prompt injection 和私人信息泄漏评测；
6. 前端明确显示 package revision、引用和限流/失败状态，不伪造工具过程。

## 4. 后续

- P2：双镜像发布控制器、Caddy 同源代理、前端渐进接线与公网 smoke。
- P3：由审核通过的 PublishedPackage 生成公开索引，支持撤回旧 revision 并继续服务上一健康版本。
- H2.9：私人 Knowledge 的发布 proposal 只能生成 public candidate，不能直接写公开 corpus。
