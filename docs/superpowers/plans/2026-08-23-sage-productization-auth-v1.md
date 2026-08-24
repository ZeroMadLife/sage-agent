# Sage 统一认证控制面 V1 实施记录

## 目标

在保留 Web `HttpOnly sage_session` 的前提下，为未来 TUI/桌面客户端提供可撤销的 Bearer API 凭证，并把认证结果继续落到服务端 user/session ownership 上。

## 已交付

- 短期 HS256 access token：仅包含 `sub`、`sid`、`exp` 等最小声明，服务端每次请求仍校验 session 是否撤销、过期或对应用户已停用。
- opaque refresh token：数据库只保存 SHA-256 摘要；每次刷新生成替换 token，旧 token 重放会撤销整个 refresh family 和对应设备 session。
- 设备登录接口：`POST /api/v1/cloud/auth/device/login`，复用现有邀请制和设备上限，不返回浏览器 Cookie。
- 轮换与撤销接口：`POST /api/v1/cloud/auth/token/refresh`、`POST /api/v1/cloud/auth/token/revoke`。
- 统一依赖：HTTP 与 WebSocket 的认证依赖同时接受 Cookie 和 `Authorization: Bearer ...`。
- Web 体验：保留现有 Cookie 登录，私有认证开启时侧栏增加退出登录并广播 shell 状态更新。

## 明确边界

- 不是公开注册、Passkey、OAuth 设备授权，也不是完整 TUI 客户端。
- JWT 不直接授予 project/workspace 权限；资源接口仍依赖服务端 ownership 查询。
- refresh token 不进入 URL、日志或浏览器 localStorage；未来桌面端应使用系统安全存储。

## 验证证据

```text
认证、owner、migration 定向回归: 38 passed
后端全回归: 1974 passed, 12 skipped
前端全回归: 509 passed
frontend production build + public build: passed
Ruff lint/format + mypy (231 source files): passed
quickstart/dev script tests: 13 passed
bash -n + git diff --check: passed
```

12 个 skip 均为需要显式 `SAGE_TEST_POSTGRES_DSN` 的 PostgreSQL 集成测试。本机 PostgreSQL
端口可达，但当前仓库默认凭据认证失败，因此没有把 SQLite migration 结果包装成真实
PostgreSQL smoke；refresh 表、索引和 revision 的真实 PostgreSQL 验证仍是残余发布风险。

候选阶段另有 ego-lite 人工记录：邀请码登录后创建“金融投资一周学习”会话，完成 Web Search 降级、`write_file` 人工审批和学习计划生成；刷新原会话 URL 后诊断、当日任务、七天路线与 `investment-intro-plan.md` 仍可恢复；退出后登录门禁重新出现，`/api/v1/cloud/me` 返回 401。该记录不替代本轮自动化门禁或缺失的真实 PostgreSQL smoke。开发环境仍保留本地匿名 API 兼容，生产环境强制认证由 API 回归覆盖。

## 下一步

实现薄 TUI：登录、会话列表、启动学习任务、读取 Timeline、处理审批和恢复任务；客户端只持有 access/refresh token，Harness、RAG、Memory 和 Sandbox 继续在服务端运行。
