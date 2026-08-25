# v1.2.0 Changelog

## Added

- macOS Apple Silicon Tauri `.app` 本地启动入口和 sidecar 生命周期管理。
- Local Provider 首次配置、Keychain 保存、模型探测、默认模型、轮换、注销和删除。
- 本机 session bearer、loopback Host/Origin 校验和 WebSocket 身份校验。
- Assistant 聊天、可恢复 Learning Task、Knowledge-first RAG、条件只读 Research 和引用材料。
- 刷新、关闭、后端重启后的 checkpoint/resume 与 generation takeover。
- `release/v1.2.0/` 本地 MVP 运行说明、验收清单和发布边界。
- 冻结 sidecar 增加本地 Learning L3 打包 smoke，覆盖四阶段推进、Artifact citation 和同数据目录重启恢复。
- 首次启动接入 macOS 原生目录选择器，支持选择已有学习空间并回填绝对路径；保留手工输入作为后备。
- 首次设置错误改为中文可恢复提示，不直接向用户展示内部 reason/action。

## Changed

- 主开发分支从 `dev/sage-v7` 收敛为 `dev/sage-local-mvp`，突出当前产品目标而不是内部代号。
- 前端和 Tauri 应用版本统一为 `1.2.0`。
- README 从“平台能力总览”调整为“打开即用的本地学习产品 + 工程事实”双层结构。
- 普通学习场景默认只读；Coding 场景才允许受控执行能力。
- 桌面默认不启用 Web Search/Web Fetch；知识不足时保留可恢复 `source_gap`，不伪造联网 Research。
- Tauri dialog 权限收敛为 `dialog:allow-open`，只开放目录选择能力。

## Not Included

- JWT 账号体系、注册、OAuth、refresh token、多用户、云端同步和在线权限后台。
- Developer ID、公证、DMG、Gatekeeper、自动更新和跨平台发行。
- 生产 SLA、真实 Provider 质量承诺和公开商业发布。
