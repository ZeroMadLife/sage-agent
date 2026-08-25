# v1.2.0 MVP Review

## 发布判断

**建议：允许作为本机单用户 MVP 候选使用和创建 PR。**

这版的发布对象是个人 Mac 上可双击运行的 `.app`，目标是验证 Sage 的学习产品主线，而不是完成商业软件发行。桌面宿主、sidecar、Provider、Learning Task、本地 RAG、引用材料和恢复链已经形成一条可运行路径；首次启动的 workspace 选择已经接入 macOS 原生 picker，并由 Rust onboarding 继续做绝对路径和目录校验；Research/L3 API 已随 sidecar 打包，但默认没有 Web Provider，因此知识不足时保持 `source_gap`。

## 关键取舍

### 为什么不用 JWT

本版本没有账号、云端用户或刷新令牌。Rust 宿主每次启动生成一次性的高熵 session bearer，并通过受控 bootstrap 传给 sidecar；sidecar 对 token、Host 和 Origin 做 fail-closed 校验。它已经满足“防止本机其他进程随便调用 API”的目标。

引入 JWT 会增加签名密钥传递、过期处理和刷新逻辑，但不会增加单用户本机 MVP 的实际安全边界，因此暂不引入。未来如果接 Cloud OAuth 或多用户，再单独设计 JWT/access-refresh contract。

### 为什么学习默认只读

Research、RAG 和材料生成需要外部事实，但不应该因为普通学习请求就获得修改文件、Shell、Patch 或 MCP 的能力。只有 Coding 场景经过现有 Permission、Policy、Approval 和 Sandbox 链路后，才开放受控执行。

### 为什么保留 SQLite

SQLite 是本机可携带的 canonical store，能在没有 PostgreSQL、Redis 或 Docker 时完成学习任务和恢复。PostgreSQL 混合检索仍是长书和质量优先路径，不作为桌面 MVP 的硬依赖。

## 保留风险

- 当前只验收 macOS Apple Silicon；Intel、Windows、Linux 不在本版本范围。
- 原生目录选择器依赖 macOS 桌面宿主；浏览器开发模式仍保留手工粘贴路径作为后备。
- 本轮正式 bundle 基于 `3b09fc6ab20e5a24dad15fe56af9b868be7aee4d` 构建；receipt 的生命周期 smoke 全部通过，人工 picker 验收覆盖选择、回填、提交和恢复。
- ad-hoc `.app` 不是可公开安装的商业发行版；没有 Developer ID、公证、DMG 和自动更新。
- 本地 fake Provider/Web 的 E2E 不代表真实 Provider 质量、Web 新鲜度、生成正确率或生产 SLA。
- 桌面默认关闭 Web Provider；不能把源码层 Research API 或 fake Web 测试描述成当前 App 已具备联网 Research。
- 全仓仍可能受既有 Coding 测试隔离债务和依赖环境 Mypy 影响；不阻断本地 MVP 验收。

## 下一步

后续版本再单独评估：Cloud OAuth、多用户 workspace、远程同步、JWT access/refresh、签名公证、DMG 和 updater。它们不能反向扩大 v1.2.0 的验收目标。
