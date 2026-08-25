# v1.2.0 Changelog

## Added

- macOS Apple Silicon Tauri `.app` 本地启动入口和 sidecar 生命周期管理。
- Local Provider 首次配置、Keychain 保存、模型探测、默认模型、轮换、注销和删除。
- 本机 session bearer、loopback Host/Origin 校验和 WebSocket 身份校验。
- Assistant 聊天、可恢复 Learning Task、Knowledge-first RAG、条件只读 Research 和引用材料。
- 刷新、关闭、后端重启后的 checkpoint/resume 与 generation takeover。
- `release/v1.2.0/` 本地 MVP 运行说明、验收清单和发布边界。

## Changed

- 主开发分支从 `dev/sage-v7` 收敛为 `dev/sage-local-mvp`，突出当前产品目标而不是内部代号。
- 前端和 Tauri 应用版本统一为 `1.2.0`。
- README 从“平台能力总览”调整为“打开即用的本地学习产品 + 工程事实”双层结构。
- 普通学习场景默认只读；Coding 场景才允许受控执行能力。

## Not Included

- JWT 账号体系、注册、OAuth、refresh token、多用户、云端同步和在线权限后台。
- Developer ID、公证、DMG、Gatekeeper、自动更新和跨平台发行。
- 生产 SLA、真实 Provider 质量承诺和公开商业发布。
