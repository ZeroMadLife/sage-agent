# Sage v1.2.0 Local Desktop MVP

> 版本定位：本地单用户、macOS Apple Silicon、双击后可用的个人学习工作台
>
> 发布形态：本地验收候选；可将 `.app` 目录压缩为 zip 自用，不称为商业发行版

## 这版解决什么

Sage 现在先解决一个清晰问题：个人打开应用后，可以配置自己的模型，围绕一个学习目标聊天、查本地知识、在证据不足时按策略 Research，并在刷新、关闭或 sidecar 重启后继续原任务。

```text
双击 Sage.app
  -> Local 启动
  -> 选择学习工作区
  -> 配置 Provider / API Key / 默认模型
  -> Assistant 聊天或创建 Learning Task
  -> Knowledge-first RAG
  -> source gap 时进入只读 Research
  -> 生成带 citation 的学习材料
  -> Checkpoint / Resume 恢复
```

## 已交付

- Rust 桌面宿主负责 sidecar 启动、握手、重启和退出清理。
- 首次启动可以选择 Local、选择 workspace、添加和探测 Provider、设置默认模型、轮换或注销 Key。
- Provider Key 只由 Rust 写入 macOS Keychain，再通过一次性 stdin bootstrap 进入 sidecar 进程内存；不进入前端存储、URL、日志、Timeline 或 SQLite 正文。
- 每次启动由宿主生成一次性的高熵本机 session bearer。HTTP/SSE 使用 `Authorization: Bearer`，WebSocket 使用受控子协议；sidecar 同时校验 bearer、Host 和 Origin。
- Assistant、Coding、Knowledge 和 Learning Task 共享同一个本地 sidecar；学习场景通过 scope 关闭写入、Shell、Patch、MCP 和子 Agent 副作用能力。
- Knowledge-first 学习链支持本地 RAG、证据充分性判断、条件 Research、冲突来源保留、Artifact citation 和 canonical Resume。
- durable checkpoint、generation guard、lease/takeover 和 SQLite 持久化支持刷新、进程重启后的继续执行。
- 退出时宿主终止并确认 sidecar，不留下 Sage sidecar 残留进程。

## 明确不做

- 注册、OAuth、在线账号、刷新令牌、多用户和云端同步；
- Developer ID、公证、Gatekeeper、DMG 安装器和自动更新；
- 商业化部署、生产 SLA、跨平台发行和公网服务；
- 自动把学习过程写入长期记忆或自动批准 Memory Proposal；
- 把本地 fake Provider/Web 的通过结果包装成真实模型质量或生产准确率。

## 运行入口

- 双击：`Sage.app`
- 源码开发：[GETTING-STARTED](../../docs/GETTING-STARTED.md)
- MVP 验收：[TESTING](TESTING.md)
- 版本变更：[CHANGELOG](CHANGELOG.md)
- 架构与风险：[REVIEW](REVIEW.md)

## 分支与发布边界

- 长期集成分支：`dev/sage-local-mvp`
- 发布候选分支：`release/v1.2.0`
- `main` 只接收通过本地 MVP 验收的同一不可变 SHA。
- 本版本允许创建 GitHub PR 和推送候选分支，但不自动执行商业签名、公证或公开发布。
