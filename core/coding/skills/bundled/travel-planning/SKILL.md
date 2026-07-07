---
name: travel-planning
description: 调用 Sage 的旅游规划领域能力，整理目的地、预算、天气、路线和行程约束
allowed-tools: read_file, search, run_shell
arguments: REQUEST
---
你正在使用 Sage 的 travel-planning domain skill。

用户需求：

$ARGUMENTS

请按以下方式处理：

1. 先判断用户是否给出了目的地、天数、预算、偏好、出发时间或位置。
2. 如果信息不完整，直接用自然语言追问最关键的一项，不要编造城市、日期或预算。
3. 如果信息完整，整理成一份旅游规划任务说明，包含目的地、天数、预算、偏好、天气/路线/附近搜索需求。
4. 当前 Sage coding runtime 还没有把旅游 MCP 工具直接挂进 `core/coding/tools`；如果需要真实高德/和风/景点工具调用，说明应走后端 travel-planning domain agent（`/api/v1/chat`）或后续统一 Skill runtime。
5. 输出要面向用户，不要暴露原始 JSON。
