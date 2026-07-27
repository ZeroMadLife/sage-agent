# v1.0.0 Changelog

> 发布日期：2026-07-27 · Release ref：`v1.0.0`

## 主要能力

- 统一 Assistant、Knowledge 与 Practice 的 Agent Harness、事件协议和可恢复时间线。
- 提供受工作区、权限、审批与 Sandbox 约束的文件、Shell、Patch、Diff 和 Git 工具。
- 建立来源快照、Wiki proposal、混合检索、稳定 citation 与知识图谱工作流。
- 独立发布只读取 PublishedPackage 的 Public Agent，隔离私人 Session、Knowledge、Memory、
  Workspace 与工具权限。

## 可复现证据

- RAG Benchmark v2：200 条分层查询，Recall@10 `0.578 → 0.814`，NDCG@10
  `0.444 → 0.695`。
- Sandbox Level 1 v2：live audit `10/10`，覆盖禁网、只读 rootfs、capability、资源限制与清理。
- Memory Lifecycle v1：`40/40` 确定性场景，覆盖 proposal、supersession、retraction 与恢复。

## 工程收口

- 移除早期旅游原型的 Agent、地图/天气/景点 MCP、mock 数据、旧聊天 API 与对应测试。
- 清理旧品牌命名、过期环境变量、重复文件排除规则和失效的 Docker COPY 路径。
- 将发布资料收敛到 `release/v1.0.0/`，重写 README 首屏与当前开发指南。

## 已知边界

- 新安装默认使用 `sage` 数据库与本地存储 key。已有安装应保留原 `.env` 完成数据库升级；
  不要删除 PostgreSQL volume。浏览器端旧登录状态不会迁移，需要重新验证访问口令。
- 无答案查询的 abstention accuracy 仍为 0，检索指标不等于回答可信度。
- Container Sandbox 的生产 rootless audit、固定 image digest 与 workspace 写边界仍需验收。
- 云端 Knowledge tenant scope、正式 HTTPS 域名与完整恢复演练尚未关闭。
- `v1.0.0` 固定本次已完成的能力；上述已知边界继续作为后续版本门禁。
