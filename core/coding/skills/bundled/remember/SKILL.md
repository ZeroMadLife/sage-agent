---
name: remember
description: 记住一个项目约定或决策,持久化到工作区记忆
allowed-tools: read_file, search
arguments: FACT
---
将以下内容记住到项目记忆中:

$ARGUMENTS

请按以下步骤操作:
1. 如 remember 工具不可用,先调用 tool_search 激活它
2. 调用 remember 工具,将上述事实写入持久记忆
3. topic 默认为 project-conventions;若这是一项技术决策,将 topic 设为 decisions
4. 写入后,简要确认已记住的内容与所属 topic
