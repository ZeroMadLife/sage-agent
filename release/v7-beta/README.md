# Sage V7 Beta

> Last verified against: `dev/sage-v7@1009e53` (2026-07-20)

这里是 V7 Beta 的稳定发布入口，也是随代码持续校正的学习索引。它不记录每小时的开发
流水账，也不把计划写成已交付能力；版本事实以对应 source ref、代码和测试为准。

## 阅读顺序

| 文档 | 适合何时阅读 | 回答的问题 |
| --- | --- | --- |
| [SHOWCASE](SHOWCASE.md) | 3 分钟了解项目 | Sage 是什么，技术含量在哪里 |
| [CHANGELOG](CHANGELOG.md) | 先了解版本变化 | V7 Beta 改了什么 |
| [REVIEW](REVIEW.md) | 评估架构与风险 | 为什么这样设计，边界在哪里 |
| [TESTING](TESTING.md) | 准备运行或验收 | 如何复现和验证 |
| [Learning Handbook](learning/00-reading-map.md) | 系统学习代码 | 从哪些模块开始读 |

根目录 [README](../../README.md) 面向第一次认识 Sage 的读者，展示产品定位、真实界面、
架构、快速开始与当前边界。本目录保留更接近版本评审的技术内容。

## 版本主线

V7 Beta 将 Sage 从领域型聊天应用推进为本地优先的个人 AI 学习与实践工作台：

1. 统一 Assistant、Knowledge 与 Practice 的 Chat Harness 和事件协议。
2. 将 Coding 收敛为可审批、可恢复、可验证的 Practice Engine。
3. 建立来源快照、Wiki 提案、混合检索和 citation 组成的 Knowledge 工作流。
4. 引入 Context budget、checkpoint、timeline、artifact 与 usage，保留运行证据。
5. 提供云控制面的身份、Workspace 和 Provider 基础，但不提前宣称生产公网已经完成。

## 当前可用

- Assistant 首页、近期会话与统一任务入口。
- Harness 2.0 默认 runtime profile，以及 plan/tool/approval/reply/terminal 事件链。
- 本地 Workspace 的文件、搜索、Shell、写入、Patch、Diff 和 Git 工具。
- 本地 Knowledge 来源、图谱、Wiki 提案、混合检索与引用。
- Skills、MCP、受限子 Agent、模型 Provider 和运行配置。
- 公开成长主页 MVP，以及限定公开 corpus 的确定性问答与来源回执。

## 发布边界

- 当前仍是 Beta，`main` 只接收完成发布门禁的版本。
- `docker-compose.yml` 是本地依赖编排，不是生产栈。
- `local_workspace` 仅用于可信开发机；公网任务必须使用 Container Sandbox。
- 云端 Knowledge 尚未开放租户级来源与元数据工作流。
- 公开主页不是公网 Harness，不具备主会话的文件或工具权限。
- Loop Engineer 自动扫描服务目前暂停，不计入运行中的产品能力。

## 手册维护规则

- `00-16` 是稳定主题编号；新的学习结果优先修改既有章节，而不是每天新增文件。
- 每章都标记最后核对的 source ref，并区分代码事实、设计目标和待验证假设。
- 竞品只作为设计参考，不使用无法由一手资料和源码复现的排他性结论。
- 测试数量不是长期事实；发布时记录命令和 CI 链接，避免维护会快速失真的数字。

## 下一道发布门禁

1. 将公开访问迁移到正式 HTTPS 域名，并完成身份、限流和恢复演练。
2. 完成生产 Container Sandbox 的 admission、资源限制和故障清理验证。
3. 完成云端 Knowledge 来源与元数据的 tenant scope，再开放多用户导入。
4. 以 [TESTING](TESTING.md) 的场景完成桌面、移动端和断线恢复验收。
