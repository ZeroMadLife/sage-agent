# V7 Beta Changelog

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

本文件描述 V7 Beta 相对早期 TourSwarm/Sage 原型的产品与架构变化。精确行为以当前代码、
迁移和测试为准；历史设计稿中的未来能力不自动进入此清单。

## Product

- 将产品定位收敛为 **Personal AI Learning Companion**，以目标、知识、实践、证据和复盘
  组成持续学习闭环。
- 增加 Assistant 首页、Knowledge 工作台、Practice 工作台、成长记录和公开主页。
- 统一中文产品壳、响应式布局、深链接和 light/dark 主题。
- 公开主页以经过筛选的项目与成长记录为内容源，确定性问答限制在公开 corpus，并附来源回执。

## Harness

- 将通用运行时抽离到 `packages/sage_harness/`，产品能力通过 adapter 接入。
- Harness 2.0 成为新会话默认 profile，统一 plan、reasoning、tool、approval、reply 与
  terminal 事件。
- 加入 checkpoint、durable timeline、run lease、context budget、自动压缩、artifact
  和 usage 记录。
- 加入 Skills、MCP、受限子 Agent 与 capability health；Knowledge/Memory 写入仍经过提案
  或明确授权。

## Practice Engine

- 提供文件列表、读取、搜索、Shell、写入、Patch、Diff 与 Git 等工作区能力。
- 加入路径 containment、fresh-read、权限模式、危险操作审批和结构化运行证据。
- 实现 `local_workspace` 与 Container Sandbox provider；后者覆盖只读 rootfs、禁网、
  资源限制、受控挂载与清理，但生产部署门禁仍在收口。
- 修复 Stop、approval 终态、重连、Diff、symlink 和工具结果持久化等关键状态问题。

## Knowledge

- 建立 immutable source snapshot、异步 ingest job 和失败恢复路径。
- 增加 Wiki proposal 的批准、拒绝、回滚和版本证据。
- 支持 sparse/dense hybrid retrieval、RRF、上下文预算与稳定 citation。
- 增加知识图谱、社区分析、学习目标与来源提案界面。

## Cloud Foundation

- 建立 GitHub OAuth、一次性邀请、session/workspace ownership 和 Provider 配置基础。
- Provider 凭据使用加密存储，并通过 capability discovery 暴露可用能力。
- Workspace 等云资源已有 owner scope；Knowledge 的云端来源与元数据仍未开放多租户。

## Defaults And Developer Experience

- Python 最低版本升级到 3.12；Harness 使用 LangChain 1.x / LangGraph 1.x 依赖线。
- CI 前端使用 Node.js 24，并执行测试与生产构建。
- 开发启动支持幂等自动迁移；生产继续要求显式 migration gate。
- 新增统一 bootstrap/dev/check 脚本、中文 PR 约定和 worktree 分支规则。

## Known Limitations

- 尚无正式公网发布地址、SLA、备份恢复演练或生产发布流水线。
- Container Sandbox 有实现和测试，但没有完成公网 workload admission。
- Knowledge 的本地来源工作流不等于云端 tenant-scoped 导入。
- 公开主页问答是限定静态内容，不是拥有私有文件权限的 Agent。
- Loop Engineer 自动扫描服务暂停；其设计与代码仅保留为后续受控自动化基础。
