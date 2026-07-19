# Sage 飞书 cc-connect 接入设计

## 1. 背景与目标

Sage 当前代码仓库位于 `/Users/zeromadlife/Desktop/tour-agent`。本阶段使用
`cc-connect` 将现有飞书企业自建应用接入本机 Claude Code CLI，使用户可以在
飞书群中通过 `@机器人` 发起 Sage 的代码阅读、开发与后续部署任务。

第一阶段只验证最小可用链路：飞书群消息能够到达本机 `cc-connect`，由 Claude
Code 在 Sage 工作目录中执行只读任务，并把结果回复到原飞书会话。当前运行阶段
增加独立 `launchd` watchdog，避免 cc-connect 自身定时器无法发现自身退出或卡死。

## 2. 已确认决策

- 复用现有飞书企业自建应用，不重新创建机器人。
- 使用 `cc-connect 1.4.1` 稳定版。
- 第一阶段 agent 使用 Claude Code；Codex CLI 待本机安装修复后再接入。
- 使用 `cc-connect web` 的本地管理页面维护项目设置。`1.4.1` 的飞书 Web 向导
  只支持二维码新建应用，因此复用现有应用时通过 `cc-connect feishu bind` 完成；
  App Secret 由 macOS 隐藏输入对话框采集，不写入 shell 历史或执行日志。
- Claude Code 工作目录固定为 `/Users/zeromadlife/Desktop/tour-agent`。
- 初始权限模式使用 `default`，涉及工具调用时由用户在飞书侧确认。
- 首轮验收只读，不修改代码、不提交、不推送、不执行部署。

## 3. 架构与数据流

```text
飞书群用户
  -> @飞书机器人
  -> 飞书开放平台 WebSocket 长连接
  -> 本机 cc-connect
  -> Claude Code CLI
  -> /Users/zeromadlife/Desktop/tour-agent
  -> cc-connect
  -> 飞书群回复
```

`cc-connect` 是唯一桥接进程，负责飞书事件接收、群聊会话映射、Claude Code
子进程管理、权限交互和回复发送。飞书使用长连接模式，因此本阶段不配置公网 IP、
域名、Webhook 或反向代理。

## 4. 组件配置

### 4.1 飞书应用

飞书应用必须满足以下条件：

- 已启用机器人能力并发布可用版本。
- 已订阅长连接事件 `im.message.receive_v1`。
- 至少具备接收群聊 `@机器人` 消息、读取单聊消息和以机器人身份发送消息的权限。
- 若启用交互卡片，订阅长连接回调 `card.action.trigger`；否则关闭飞书卡片，回退到
  纯文本权限确认。
- 机器人已添加到目标飞书群，应用可用范围覆盖测试用户。

应用凭据只录入本机 `cc-connect` 配置，不写入 Sage 仓库，不进入 Git。

### 4.2 cc-connect 项目

本地项目配置的行为等价于：

```toml
[[projects]]
name = "sage"

[projects.agent]
type = "claudecode"

[projects.agent.options]
work_dir = "/Users/zeromadlife/Desktop/tour-agent"
mode = "default"

[[projects.platforms]]
type = "feishu"
```

实际的 `app_id` 和 `app_secret` 由本机绑定流程写入用户级配置，不出现在本文档中。

### 4.3 Claude Code

沿用本机现有 Claude Code OAuth 登录状态。`cc-connect` 由独立终端进程启动，避免
继承嵌套 Claude Code 会话的 `CLAUDECODE` 环境变量。Sage 仓库的 `AGENTS.md`
继续约束 Claude Code 的 Git、验证和文档行为。

## 5. 权限与安全边界

- 首轮使用 `default` 模式，不开启 `bypassPermissions`。
- 群内任务只允许作用于 Sage 工作目录；不配置额外工作区。
- 首轮测试提示词明确要求只读，不写文件、不运行部署、不进行 Git 提交或推送。
- Sage 当前工作区已有未跟踪文件，任何后续写操作都必须保留既有用户改动。
- App Secret 视为本机运行凭据，不进入仓库、设计文档、日志摘录或测试证据。
- 生产部署能力不在本阶段开放；后续应单独定义允许用户、部署命令、审批点与回滚策略。

## 6. 首轮验收流程

1. 安装并确认 `cc-connect 1.4.1` 可执行。
2. 首次启动生成用户级配置，并启用 `cc-connect web` 管理页。
3. 通过隐藏输入对话框和 `cc-connect feishu bind` 绑定现有飞书应用，创建 `sage`
   项目。
4. 在 Web UI 中设置 Claude Code、`default` 权限模式和 Sage 工作目录。
5. 启动 `cc-connect`，确认飞书平台和 WebSocket 长连接启动成功。
6. 将机器人加入目标飞书群，并发送只读测试请求：

   ```text
   @机器人 请只读取当前项目的 README.md，告诉我 Sage 的定位和主要技术栈。
   不要修改文件，不要执行部署，不要提交 Git。
   ```

7. 验证回复内容来自 Sage 仓库，并确认没有工作区文件变化。

验收通过需要同时满足：群消息被接收、Claude Code 成功启动、回复发送到原群、
工作目录正确、`git status --short` 在测试前后没有新增变化。

## 7. 失败处理

- 无法建立长连接：核对 App ID/Secret、应用发布状态和飞书长连接订阅方式。
- 群消息无响应：核对机器人是否在群内、应用可用范围和
  `im.message.receive_v1` 事件。
- 能接收但无法回复：核对 `im:message:send_as_bot` 权限和应用版本是否重新发布。
- 卡片操作超时：补充 `card.action.trigger` 回调，或关闭飞书卡片并使用纯文本模式。
- Claude Code 启动失败：核对 OAuth 状态、PATH 和 `CLAUDECODE` 环境变量。
- 工作目录错误：停止桥接进程，修正 `work_dir` 后重新启动，不在错误目录继续任务。

## 8. 后续阶段

首轮链路稳定后，按独立设计推进以下能力：

- 修复本机 Codex CLI 并评估 `exec` 与 `app_server` 后端。
- 配置飞书允许用户或群范围，避免机器人被非预期成员触发。
- 为 Sage 开发任务定义安全的写入、测试和 Git 工作流。
- 为部署任务增加显式审批、环境隔离、部署凭据管理、健康检查和回滚策略。
- 配置 `launchd` 或其他守护方式，使 `cc-connect` 重启后自动恢复长连接。

## 9. 非目标

- 本阶段不修改 Sage 产品代码。
- 本阶段不部署 Sage，不配置生产服务器。
- 本阶段不启用无人值守写代码、提交、推送或发布。
- 本阶段不接入第二个 CLI 或第二个飞书机器人。

## 10. 实施结果（2026-07-15）

- `cc-connect 1.4.1` 已安装，用户级配置权限为 `0600`。
- 现有飞书应用凭据验证通过，应用版本 `1.0.1` 已发布，机器人能力已启用。
- `sage` 项目使用 `claudecode/default`，工作目录为
  `/Users/zeromadlife/Desktop/tour-agent`。
- 飞书 WebSocket 长连接建立成功，群聊 `@机器人` 消息触发 Claude Code；首轮任务
  使用 1 个读取工具，约 13 秒完成并回复原群。
- 首轮测试前后 `git status --short` 一致，没有新增工作区改动。
- 当前通过用户级 `launchctl submit` 任务保持在线；该任务不会跨注销或重启持久化。
- `allow_from` 尚未收紧，当前群成员均可触发普通任务；特权命令因 `admin_from`
  为空而保持禁用。部署权限仍未开放。

## 11. 独立存活监控（2026-07-17）

- `cc-connect daemon install` 负责进程退出后的基础拉起；独立 LaunchAgent
  `com.sage.cc-connect-watchdog` 每 5 分钟补充检查 daemon 状态、Unix 管理 socket
  连通性，以及 Loop 小时任务和日报任务是否仍存在。
- 检查健康时完全静默。daemon 或管理 socket 异常时最多每 15 分钟尝试一次
  `cc-connect daemon restart`，防止反复重启；恢复成功后只向现有飞书会话发送一条
  中文通知。Loop cron 缺失无法通过重启修复，因此只发送一次人工处理告警，避免无效
  重启影响正在进行的会话。
- 通知会话从已有 cc-connect cron 绑定中读取，只保存在本机权限为 `0600` 的
  `~/.local/state/sage-cc-connect-watchdog/config.json`。session 不进入仓库、plist、
  launcher、日志或通知正文。
- watchdog 日志按 `2 MB x 4` 轮转，状态和日志目录权限为 `0700`，不会按小时生成
  Markdown 文档。
- 该检查能确认本机网关、管理 socket 和调度器存活，但不会主动向飞书发送心跳，
  因而不能在完全静默的前提下证明每次飞书消息投递都成功。端到端投递仍由真实消息
  和恢复通知验证。
