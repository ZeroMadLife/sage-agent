---
name: dream
description: 整理记忆,生成待审批的记忆提案
allowed-tools: read_file, search
arguments: TOPIC
---
整理记忆,为以下主题生成提案:

$ARGUMENTS

请按以下步骤操作:
1. 如 dream 工具不可用,先调用 tool_search 激活它
2. 调用 dream 工具生成记忆提案(仅生成提案,不自动写入)
3. 提案会列出待审批的事实,等待用户确认后再调用 approve 写入
4. 输出要面向用户,不要暴露原始内部数据
