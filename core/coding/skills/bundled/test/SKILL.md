---
name: test
description: 跑测试并整理失败原因
allowed-tools: read_file, search, run_shell
---
请跑项目测试并分析结果：

1. 用 run_shell 跑 `bash scripts/check.sh`（若存在）或 `pytest -q`
2. 如果有失败，分析失败原因
3. 给出修复建议
