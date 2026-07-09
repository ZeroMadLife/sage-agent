---
name: planmode
description: 进入计划模式,规划任务方案后再执行
allowed-tools: read_file, search, list_files
arguments: TOPIC
---
进入计划模式,为以下任务制定详细方案:

$ARGUMENTS

请按以下步骤操作:
1. 如 enter_plan_mode 工具不可用,先调用 tool_search 激活它
2. 调用 enter_plan_mode 工具进入计划模式,topic 为上述任务描述
3. 在计划模式下,阅读相关代码文件,理解现有架构
4. 将计划写入 plan 文件(.coding/plans/ 目录下)
5. 计划应包含:目标、步骤、涉及文件、风险评估
6. 调用 exit_plan_mode 提交计划等待用户审批
7. 用户审批后,按计划执行
